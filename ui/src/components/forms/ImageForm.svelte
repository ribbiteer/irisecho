<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Dices from "@lucide/svelte/icons/dices";
  import ActionBar from "../ActionBar.svelte";
  import PromptTools from "../PromptTools.svelte";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const ASPECTS: [string, number, number][] = [
    ["1:1", 1, 1],
    ["4:3", 4, 3],
    ["3:4", 3, 4],
    ["16:9", 16, 9],
    ["9:16", 9, 16],
    ["3:2", 3, 2],
    ["2:3", 2, 3],
  ];

  let prompt = $state("");
  let aspect = $state("1:1");
  let count = $state(1);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  let sending = $state(false);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      aspect = r.params.aspect ?? "1:1";
      if (r.params.seed != null) {
        seed = r.params.seed;
        fixedSeed = true;
      }
      app.reuse = null;
    }
  });

  async function go() {
    if (!prompt.trim() || sending) return;
    sending = true;
    for (let i = 0; i < count; i++) {
      const s = fixedSeed ? seed + i : Math.floor(Math.random() * 2 ** 50);
      await submit(model, { prompt: prompt.trim(), aspect, seed: s });
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
    <label class="label" for="prompt">Describe the picture</label>
    <textarea
      id="prompt"
      class="textarea"
      rows="6"
      bind:value={prompt}
      placeholder="A lighthouse on a black basalt cliff at blue hour, waves catching the last violet light"
    ></textarea>
    <PromptTools {model} bind:value={prompt} describe />
    <p class="tip">Describe what should be there. These models ignore "no" and "without".</p>
  </div>

  <div class="field">
    <span class="label">Shape</span>
    <div class="chips">
      {#each ASPECTS as [id, w, h] (id)}
        <button class="chip" aria-pressed={aspect === id} onclick={() => (aspect = id)}>
          <svg width="14" height="14" viewBox="-8 -8 16 16" aria-hidden="true">
            <rect
              x={-(7 * w) / Math.max(w, h)}
              y={-(7 * h) / Math.max(w, h)}
              width={(14 * w) / Math.max(w, h)}
              height={(14 * h) / Math.max(w, h)}
              rx="2"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
            />
          </svg>
          {id}
        </button>
      {/each}
    </div>
  </div>

  <div class="row">
    <div class="field">
      <span class="label">How many</span>
      <div class="chips">
        {#each [1, 2, 3, 4] as n (n)}
          <button class="chip num" aria-pressed={count === n} onclick={() => (count = n)}>{n}</button>
        {/each}
      </div>
    </div>
    <div class="field">
      <span class="label">
        Seed
        <button
          class="switch"
          role="switch"
          aria-checked={fixedSeed}
          aria-label="Use a fixed seed"
          onclick={() => (fixedSeed = !fixedSeed)}
        ></button>
      </span>
      {#if fixedSeed}
        <div class="seed">
          <input class="input mono" type="number" min="0" bind:value={seed} aria-label="Seed" />
          <button class="btn icon" title="New seed" onclick={() => (seed = Math.floor(Math.random() * 1e9))}>
            <Dices size={16} />
          </button>
        </div>
      {:else}
        <p class="random">Random each time</p>
      {/if}
    </div>
  </div>

  <ActionBar
    {model}
    label={count > 1 ? `Create ${count} images` : "Create image"}
    disabled={!prompt.trim() || sending}
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
  .tip {
    font-size: 12px;
    color: var(--text-4);
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
  .seed {
    display: flex;
    gap: 6px;
  }
  .seed .input {
    height: 36px;
    padding: 0 10px;
  }
  .random {
    height: 30px;
    display: flex;
    align-items: center;
    color: var(--text-3);
    font-size: 13px;
  }
</style>
