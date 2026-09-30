<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
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
  let thinking = $state(false);
  let loop = $state(false);
  let sending = $state(false);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      duration = r.params.duration ?? 30;
      setBpm = !!r.params.bpm;
      bpm = r.params.bpm ?? 110;
      withLyrics = !!r.params.lyrics;
      lyrics = r.params.lyrics ?? "";
      takes = r.params.takes ?? 2;
      thinking = !!r.params.thinking;
      loop = !!r.params.loop;
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

  async function go() {
    if (!prompt.trim() || sending) return;
    sending = true;
    await submit(model, {
      prompt: prompt.trim(),
      duration,
      bpm: setBpm ? bpm : null,
      lyrics: withLyrics ? lyrics : "",
      takes,
      thinking,
      loop: loop && setBpm,
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

  <div class="field">
    <span class="label">Length <span class="mono value">{duration} s</span></span>
    <input type="range" min="10" max="240" step="5" bind:value={duration} aria-label="Length in seconds" />
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
      <small>Better shape on longer pieces. Slower.</small>
    </span>
  </label>

  <ActionBar {model} label={takes > 1 ? `Compose ${takes} takes` : "Compose"} disabled={!prompt.trim() || sending} onsubmit={go} />
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
