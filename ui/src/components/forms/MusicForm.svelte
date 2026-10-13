<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import SeedField from "../SeedField.svelte";
  import SourceAudio from "../SourceAudio.svelte";
  import { controlParams, reuseCommon } from "../../lib/controls";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const SUGGESTIONS: [string, string[]][] = [
    ["Genre", ["synthwave", "lo-fi hip hop", "orchestral", "funk", "house", "jazz", "ambient", "rock", "chiptune", "cinematic"]],
    ["Mood", ["uplifting", "tense", "dreamy", "playful", "epic", "melancholic", "triumphant"]],
    ["Sound", ["piano", "strings", "analog synth", "brass stabs", "slap bass", "acoustic guitar", "808 drums", "choir"]],
  ];

  let prompt = $state("");
  let duration = $state(30);
  let setBpm = $state(false);
  let bpm = $state(110);
  let withLyrics = $state(false);
  let lyrics = $state("");
  let takes = $state(2);
  let thinking = $state(true);
  let loop = $state(false);
  let sending = $state(false);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  // Start from a recording (advanced mode): cover it, repaint a part of it, or add to its end.
  type Task = "cover" | "repaint" | "extend";
  const TASKS: [Task, string, string][] = [
    ["cover", "Cover", "The same song in the style you describe."],
    ["repaint", "Repaint a part", "New music for one part; the rest stays as it is."],
    ["extend", "Extend", "Carries on after the end of the recording."],
  ];
  const advanced = $derived(!!app.settings?.advanced);
  let source = $state<{ id: string; name: string } | null>(null);
  let task = $state<Task>("cover");
  let regionStart = $state(0);
  let regionEnd = $state(10);
  let extendBy = $state(20);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      source = r.params.source ? { id: r.params.source, name: "Recording from that job" } : null;
      task = r.params.task ?? "cover";
      regionStart = r.params.region?.[0] ?? 0;
      regionEnd = r.params.region?.[1] ?? 10;
      extendBy = r.params.extend_by ?? 20;
      prompt = r.params.prompt ?? "";
      duration = r.params.duration ?? 30;
      setBpm = !!r.params.bpm;
      bpm = r.params.bpm ?? 110;
      withLyrics = !!r.params.lyrics;
      lyrics = r.params.lyrics ?? "";
      takes = r.params.takes ?? 2;
      thinking = r.params.thinking !== false;
      loop = !!r.params.loop;
      const { seed: s } = reuseCommon(model, r);
      if (s != null) {
        seed = s;
        fixedSeed = true;
      }
      app.reuse = null;
    }
  });

  const tags = $derived(
    prompt
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean),
  );

  function toggleTag(tag: string) {
    const has = tags.includes(tag);
    const next = has ? tags.filter((t) => t !== tag) : [...tags, tag];
    prompt = next.join(", ");
  }

  // What a control's `needs` may refer to: the language only matters with lyrics, the planner knobs with planning.
  // The recording's own controls (how closely a cover follows it) only apply to a cover.
  const fromSource = $derived(advanced && !!source);
  const context = $derived({
    lyrics: withLyrics ? lyrics.trim() : "",
    thinking: thinking && !fromSource, // the planner is skipped when starting from a recording
    source: fromSource && task === "cover" ? source!.id : "",
  });
  const sourceProblem = $derived(
    !fromSource
      ? ""
      : task === "repaint" && !(Number(regionStart) >= 0 && Number(regionEnd) > Number(regionStart))
        ? "The end of the part to repaint must come after its start."
        : task === "extend" && !(Number(extendBy) >= 1 && Number(extendBy) <= 240)
          ? "Choose between 1 and 240 seconds to add."
          : "",
  );
  const sourceParams = $derived(
    !fromSource
      ? {}
      : {
          source: source!.id,
          task,
          ...(task === "repaint" ? { region: [Number(regionStart), Number(regionEnd)] } : {}),
          ...(task === "extend" ? { extend_by: Number(extendBy) } : {}),
        },
  );

  async function go() {
    if (!prompt.trim() || sourceProblem || sending) return;
    sending = true;
    await submit(model, {
      ...sourceParams,
      prompt: prompt.trim(),
      duration,
      bpm: setBpm ? bpm : null,
      lyrics: withLyrics ? lyrics : "",
      takes,
      thinking,
      loop: loop && setBpm,
      ...(fixedSeed ? { seed: Number(seed) } : {}),
      ...controlParams(model, context),
    });
    sending = false;
  }

  function onKey(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      if (model.ready) go();
    }
  }
</script>

<div class="form" onkeydown={onKey} role="presentation">
  <div class="field">
    <label class="label" for="style">Describe the sound</label>
    <textarea id="style" class="textarea" rows="3" bind:value={prompt} placeholder="synthwave, driving, analog synth, gated drums, night drive"></textarea>
    <p class="tip">A few comma-separated tags work best: one genre, a mood, specific instruments.</p>
  </div>

  <div class="suggest">
    {#each SUGGESTIONS as [group, list] (group)}
      <div class="group">
        <span class="gname">{group}</span>
        <div class="chips">
          {#each list as tag (tag)}
            <button class="chip small" aria-pressed={tags.includes(tag)} onclick={() => toggleTag(tag)}>{tag}</button>
          {/each}
        </div>
      </div>
    {/each}
  </div>

  {#if advanced}
    <div class="field source">
      <span class="label">Start from a recording</span>
      <SourceAudio
        bind:value={source}
        prompt="Drop a song, or choose a file"
        hint="WAV, MP3, FLAC, OGG or M4A. The tags above describe what it should become."
      />
      {#if source}
        <div class="chips" role="group" aria-label="What to do with it">
          {#each TASKS as [id, label] (id)}
            <button class="chip" aria-pressed={task === id} onclick={() => (task = id)}>{label}</button>
          {/each}
        </div>
        <p class="tip">{TASKS.find((t) => t[0] === task)?.[2]}</p>
        {#if task === "repaint"}
          <div class="region">
            <label>From <input class="input mono" type="number" min="0" step="0.5" bind:value={regionStart} aria-label="Start in seconds" /> s</label>
            <label>to <input class="input mono" type="number" min="0" step="0.5" bind:value={regionEnd} aria-label="End in seconds" /> s</label>
          </div>
        {:else if task === "extend"}
          <div class="region">
            <label>Add <input class="input mono" type="number" min="1" max="240" step="1" bind:value={extendBy} aria-label="Seconds to add" /> s</label>
          </div>
        {/if}
        {#if sourceProblem}<p class="tip warn">{sourceProblem}</p>{/if}
        <p class="tip">The song keeps its own length, so the length below is not used. Add lyrics below to sing them, or leave them off for an instrumental.</p>
      {/if}
    </div>
  {/if}

  <div class="field">
    <span class="label">Length <span class="mono value">{duration} s</span></span>
    <input type="range" min="10" max="240" step="5" bind:value={duration} aria-label="Length in seconds" disabled={fromSource} />
  </div>

  <div class="row">
    <div class="field">
      <span class="label">
        Tempo
        <button class="switch" role="switch" aria-checked={setBpm} aria-label="Set a tempo" onclick={() => (setBpm = !setBpm)}></button>
      </span>
      {#if setBpm}
        <div class="bpm">
          <input class="input mono" type="number" min="40" max="220" bind:value={bpm} aria-label="Beats per minute" />
          <span>BPM</span>
        </div>
      {:else}
        <p class="muted">Let the model choose</p>
      {/if}
    </div>
    <div class="field">
      <span class="label">Takes</span>
      <div class="chips">
        {#each [1, 2, 3, 4] as n (n)}
          <button class="chip num" aria-pressed={takes === n} onclick={() => (takes = n)}>{n}</button>
        {/each}
      </div>
    </div>
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={withLyrics} aria-label="Add lyrics" onclick={() => (withLyrics = !withLyrics)}></button>
    <span>
      <strong>Sung lyrics</strong>
      <small>Off gives a clean instrumental.</small>
    </span>
  </label>
  {#if withLyrics}
    <textarea
      class="textarea"
      rows="6"
      bind:value={lyrics}
      aria-label="Lyrics"
      placeholder={"[verse]\nNeon on the water\nWe drive until the dawn\n\n[chorus]\nHold on, hold on"}
    ></textarea>
  {/if}

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={loop} aria-label="Make it loop" disabled={!setBpm} onclick={() => (loop = !loop)}></button>
    <span>
      <strong>Make it loop</strong>
      <small>{setBpm ? "Cuts on the bar and blends the end into the start." : "Set a tempo to cut a seamless loop."}</small>
    </span>
  </label>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={thinking} aria-label="Plan the structure first" onclick={() => (thinking = !thinking)}></button>
    <span>
      <strong>Plan the structure first</strong>
      <small>Plans tempo, key and form before composing, as ACE-Step does by default. Turn off for faster drafts.</small>
    </span>
  </label>

  <div class="field">
    <SeedField bind:fixed={fixedSeed} bind:seed />
    {#if takes > 1}<p class="tip">Take 2 uses the seed plus one, and so on.</p>{/if}
  </div>

  <AdvancedControls {model} {context} />

  <ActionBar {model} label={takes > 1 ? `Compose ${takes} takes` : "Compose"} disabled={!prompt.trim() || !!sourceProblem || sending} onsubmit={go} />
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 16px;
    flex: 1;
  }
  .tip,
  .muted {
    font-size: 12px;
    color: var(--text-4);
  }
  .muted {
    height: 30px;
    display: flex;
    align-items: center;
    color: var(--text-3);
    font-size: 13px;
  }
  .suggest {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: -6px;
  }
  .group {
    display: grid;
    grid-template-columns: 52px 1fr;
    align-items: start;
    gap: 8px;
  }
  .gname {
    font-size: 11.5px;
    color: var(--text-4);
    padding-top: 5px;
  }
  .chip.small {
    height: 26px;
    padding: 0 9px;
    font-size: 12px;
  }
  .value {
    color: var(--text-2);
  }
  .source {
    gap: 8px;
  }
  .region {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    font-size: 13px;
    color: var(--text-3);
  }
  .region .input {
    width: 84px;
    height: 32px;
    padding: 0 10px;
    margin: 0 4px;
  }
  .warn {
    color: var(--danger-ink);
  }
  .row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 22px;
  }
  .bpm {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-3);
    font-size: 13px;
  }
  .bpm .input {
    width: 88px;
    height: 32px;
    padding: 0 10px;
  }
  .num {
    width: 34px;
    justify-content: center;
    padding: 0;
  }
  .toggle {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }
  .toggle span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .toggle strong {
    font-weight: 560;
    font-size: 13.5px;
  }
  .toggle small {
    color: var(--text-3);
    font-size: 12.5px;
  }
  .switch:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
