<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
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

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      duration = r.params.duration ?? 3;
      takes = r.params.takes ?? 3;
      trim = r.params.trim ?? true;
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

  async function go() {
    if (!lines.length || sending) return;
    sending = true;
    for (const line of lines) await submit(model, { prompt: line, duration, takes, trim });
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

  <div class="field">
    <span class="label">Takes of each</span>
    <div class="chips">
      {#each [1, 2, 3, 4] as n (n)}
        <button class="chip num" aria-pressed={takes === n} onclick={() => (takes = n)}>{n}</button>
      {/each}
    </div>
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={trim} aria-label="Trim silence" onclick={() => (trim = !trim)}></button>
    <span>
      <strong>Trim silence</strong>
      <small>The model pads short sounds; trimming leaves just the sound.</small>
    </span>
  </label>

  <ActionBar
    {model}
    label={lines.length > 1 ? `Generate ${lines.length} sounds` : "Generate"}
    hint={takes > 1 ? `${takes} takes each` : ""}
    disabled={!lines.length || sending}
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
