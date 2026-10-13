<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import PromptTools from "../PromptTools.svelte";
  import SeedField from "../SeedField.svelte";
  import SourceAudio from "../SourceAudio.svelte";
  import { controlParams, reuseCommon } from "../../lib/controls";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const IDEAS = [
    "A heavy wooden desk drawer slammed shut, short and dry",
    "A soft UI click, like a small plastic switch, very short",
    "A bright coin pickup chime for a game, two quick rising notes",
    "Footsteps on loose gravel, slow walk, close microphone",
    "A distant thunder crack rolling across a valley",
    "A small crowd cheering and clapping in a gym",
  ];

  let prompt = $state("");
  let duration = $state(3);
  let takes = $state(3);
  let perLine = $state(false);
  let trim = $state(true);
  let sending = $state(false);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  // Start from a sound (advanced mode): make a variation of it, or make one part of it again.
  const advanced = $derived(!!app.settings?.advanced);
  let initAudio = $state<{ id: string; name: string } | null>(null);
  let replacePart = $state(false);
  let regionStart = $state(0);
  let regionEnd = $state(1);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      initAudio = r.params.init_audio ? { id: r.params.init_audio, name: "Sound from that job" } : null;
      replacePart = !!r.params.region;
      regionStart = r.params.region?.[0] ?? 0;
      regionEnd = r.params.region?.[1] ?? 1;
      prompt = r.params.prompt ?? "";
      duration = r.params.duration ?? 3;
      takes = r.params.takes ?? 3;
      trim = r.params.trim ?? true;
      const { seed: s } = reuseCommon(model, r);
      if (s != null) {
        seed = s;
        fixedSeed = true;
      }
      app.reuse = null;
    }
  });

  const lines = $derived(
    perLine
      ? prompt
          .split("\n")
          .map((l) => l.trim())
          .filter((l) => l && !l.startsWith("#"))
      : prompt.trim()
        ? [prompt.trim()]
        : [],
  );

  const fromSound = $derived(advanced && !!initAudio);
  // The strength control only applies to a variation, not to a replaced part.
  const context = $derived({ init_audio: fromSound && !replacePart ? initAudio!.id : "" });
  const soundProblem = $derived(
    fromSound && replacePart && !(Number(regionStart) >= 0 && Number(regionEnd) > Number(regionStart))
      ? "The end of the part to replace must come after its start."
      : fromSound && replacePart && Number(regionEnd) > 30
        ? "The part to replace cannot reach past 30 seconds."
        : "",
  );
  const soundParams = $derived(
    !fromSound
      ? {}
      : {
          init_audio: initAudio!.id,
          ...(replacePart ? { region: [Number(regionStart), Number(regionEnd)] } : {}),
        },
  );

  async function go() {
    if (!lines.length || soundProblem || sending) return;
    sending = true;
    for (const line of lines) {
      await submit(model, {
        ...soundParams,
        prompt: line,
        duration,
        takes,
        trim,
        ...(fixedSeed ? { seed: Number(seed) } : {}),
        ...controlParams(model, context),
      });
    }
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
    <label class="label" for="sfx">
      Describe the sound
      <span class="inline">
        One sound per line
        <button class="switch" role="switch" aria-checked={perLine} aria-label="One sound per line" onclick={() => (perLine = !perLine)}></button>
      </span>
    </label>
    <textarea id="sfx" class="textarea" rows="5" bind:value={prompt} placeholder="A heavy wooden desk drawer slammed shut, short and dry"></textarea>
    {#if !perLine}<PromptTools {model} bind:value={prompt} describe />{/if}
    <p class="tip">Say what makes the sound, how it is triggered, and how it was recorded. One sound at a time; layer bigger effects from separate takes.</p>
  </div>

  <div class="ideas">
    {#each IDEAS as idea (idea)}
      <button class="idea" onclick={() => (prompt = perLine && prompt ? `${prompt}\n${idea}` : idea)}>{idea}</button>
    {/each}
  </div>

  <div class="field">
    <span class="label">Length <span class="mono value">{duration.toFixed(1)} s</span></span>
    <input type="range" min="0.5" max="30" step="0.5" bind:value={duration} aria-label="Length in seconds" />
  </div>

  <div class="row">
    <div class="field">
      <span class="label">Takes of each</span>
      <div class="chips">
        {#each [1, 2, 3, 4] as n (n)}
          <button class="chip num" aria-pressed={takes === n} onclick={() => (takes = n)}>{n}</button>
        {/each}
      </div>
      {#if takes > 1}<p class="tip">Take 2 uses the seed plus one, and so on.</p>{/if}
    </div>
    <SeedField bind:fixed={fixedSeed} bind:seed />
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={trim} aria-label="Trim silence" onclick={() => (trim = !trim)}></button>
    <span>
      <strong>Trim silence</strong>
      <small>The model pads short sounds; trimming leaves just the sound.</small>
    </span>
  </label>

  {#if advanced}
    <div class="field source">
      <span class="label">Start from a sound</span>
      <SourceAudio
        bind:value={initAudio}
        seconds={30}
        prompt="Drop a sound, or choose a file"
        hint="The description says what it should become. Up to 30 seconds."
      />
      {#if initAudio}
        <label class="toggle">
          <button class="switch" role="switch" aria-checked={replacePart} aria-label="Make only one part again" onclick={() => (replacePart = !replacePart)}></button>
          <span>
            <strong>Make only one part again</strong>
            <small>{replacePart ? "The rest of the sound stays exactly as it is." : "Off makes a variation of the whole sound."}</small>
          </span>
        </label>
        {#if replacePart}
          <div class="region">
            <label>From <input class="input mono" type="number" min="0" max="30" step="0.1" bind:value={regionStart} aria-label="Start in seconds" /> s</label>
            <label>to <input class="input mono" type="number" min="0" max="30" step="0.1" bind:value={regionEnd} aria-label="End in seconds" /> s</label>
          </div>
        {/if}
        {#if soundProblem}<p class="tip warn">{soundProblem}</p>{/if}
        <p class="tip">The result is at least as long as the sound. Trimming silence is skipped when only a part is made again.</p>
      {/if}
    </div>
  {/if}

  <AdvancedControls {model} {context} />

  <ActionBar
    {model}
    label={lines.length > 1 ? `Generate ${lines.length} sounds` : "Generate"}
    hint={takes > 1 ? `${takes} takes each` : ""}
    disabled={!lines.length || !!soundProblem || sending}
    onsubmit={go}
  />
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 16px;
    flex: 1;
  }
  .inline {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
  }
  .tip {
    font-size: 12px;
    color: var(--text-4);
  }
  .ideas {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: -6px;
  }
  .idea {
    text-align: left;
    border: 0;
    background: transparent;
    color: var(--text-3);
    font-size: 12.5px;
    padding: 5px 8px;
    border-radius: 8px;
    cursor: pointer;
  }
  .idea:hover {
    background: var(--surface-2);
    color: var(--text);
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
    grid-template-columns: auto 1fr;
    gap: 22px;
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
</style>
