<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Seed: random each time, or a fixed number to repeat or vary a result. -->
<script lang="ts">
  import Dices from "@lucide/svelte/icons/dices";

  let { fixed = $bindable(false), seed = $bindable(0) }: { fixed?: boolean; seed?: number } = $props();
</script>

<div class="field">
  <span class="label">
    Seed
    <button class="switch" role="switch" aria-checked={fixed} aria-label="Use a fixed seed" onclick={() => (fixed = !fixed)}
    ></button>
  </span>
  {#if fixed}
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

<style>
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
