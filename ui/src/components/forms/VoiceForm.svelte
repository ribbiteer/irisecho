<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const voices = $derived(model.options.voices ?? []);
  const groups = $derived([...new Set(voices.map((v) => v.group))]);

  let text = $state("");
  let voice = $state("af_heart");
  let speed = $state(1);
  let perLine = $state(false);
  let clean = $state(true);
  let sending = $state(false);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      text = r.params.text ?? "";
      voice = r.params.voice ?? voice;
      speed = r.params.speed ?? 1;
      clean = r.params.clean ?? true;
      app.reuse = null;
    }
  });

  const lines = $derived(
    perLine
      ? text
          .split("\n")
          .map((l) => l.trim())
          .filter((l) => l && !l.startsWith("#"))
      : text.trim()
        ? [text.trim()]
        : [],
  );

  async function go() {
    if (!lines.length || sending) return;
    sending = true;
    for (const line of lines) await submit(model, { text: line, voice, speed, clean });
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
    <label class="label" for="script">
      What should it say?
      <span class="inline">
        One file per line
        <button
          class="switch"
          role="switch"
          aria-checked={perLine}
          aria-label="One file per line"
          onclick={() => (perLine = !perLine)}
        ></button>
      </span>
    </label>
    <textarea
      id="script"
      class="textarea"
      rows="8"
      bind:value={text}
      placeholder={perLine
        ? "Welcome back.\nQuestion one: which planet has the shortest day?\nCorrect! Ten points."
        : "Welcome to the show. Tonight we have three rounds and one very nervous host."}
    ></textarea>
  </div>

  <div class="field">
    <label class="label" for="voice">Voice</label>
    <select id="voice" class="select" bind:value={voice}>
      {#each groups as g (g)}
        <optgroup label={g}>
          {#each voices.filter((v) => v.group === g) as v (v.id)}
            <option value={v.id}>{v.label}</option>
          {/each}
        </optgroup>
      {/each}
    </select>
  </div>

  <div class="field">
    <span class="label">Pace <span class="mono value">{speed.toFixed(2)}×</span></span>
    <input type="range" min="0.7" max="1.4" step="0.05" bind:value={speed} aria-label="Pace" />
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={clean} aria-label="Clean up" onclick={() => (clean = !clean)}></button>
    <span>
      <strong>Trim silence and match loudness</strong>
      <small>Every line comes out at the same level, ready to drop into a game or edit.</small>
    </span>
  </label>

  <ActionBar
    {model}
    label={lines.length > 1 ? `Speak ${lines.length} lines` : "Speak"}
    disabled={!lines.length || sending}
    onsubmit={go}
  />
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 18px;
    flex: 1;
  }
  .inline {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
  }
  .value {
    color: var(--text-2);
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
  .select {
    appearance: none;
    background-image: linear-gradient(45deg, transparent 50%, var(--text-3) 50%),
      linear-gradient(135deg, var(--text-3) 50%, transparent 50%);
    background-position:
      calc(100% - 18px) 50%,
      calc(100% - 13px) 50%;
    background-size: 5px 5px;
    background-repeat: no-repeat;
  }
</style>
