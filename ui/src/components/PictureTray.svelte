<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  The pictures an edit draws from beyond Picture 1: numbered tiles that grow as pictures
  are added (several can be chosen at once), up to `max`. Removing one moves the later
  ones up a number, and `onremove` tells the form which number went so it can follow in
  the prompt. Pictures past `use` are kept but marked as unused by the chosen model. -->
<script lang="ts">
  import ImageDrop from "./ImageDrop.svelte";

  let {
    pictures = $bindable([]),
    max,
    use = max,
    first = 2,
    onremove,
  }: {
    pictures?: (string | null)[];
    max: number;
    use?: number;
    first?: number;
    onremove?: (number: number) => void;
  } = $props();

  // A tile's Remove sets its slot to null: close the gap and report the number.
  $effect(() => {
    const gap = pictures.indexOf(null);
    if (gap < 0) return;
    pictures = pictures.filter((p) => p !== null);
    onremove?.(first + gap);
  });

  let adding = $state<string | null>(null);
  $effect(() => {
    if (!adding) return;
    pictures = [...pictures, adding].slice(0, max);
    adding = null;
  });

  function more(ids: string[]) {
    pictures = [...pictures, ...ids].slice(0, max);
  }
</script>

<div class="tray">
  {#each pictures as _, i (i)}
    <div class="tile" class:unused={i >= use} title={i >= use ? "Not used by this model" : undefined}>
      <ImageDrop bind:value={pictures[i]} label={`Picture ${first + i}`} tag={`${first + i}`} compact />
    </div>
  {/each}
  {#if pictures.length < max}
    <div class="tile add">
      <ImageDrop
        bind:value={adding}
        label={pictures.length ? `Picture ${first + pictures.length}` : `Picture ${first}`}
        compact
        onmore={more}
      />
    </div>
  {/if}
</div>

<style>
  .tray {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
    gap: 8px;
  }
  .tile.unused {
    opacity: 0.4;
  }
  .tile :global(.filled.compact) {
    height: 96px;
  }
  .tile :global(.filled .tag) {
    left: 6px;
    bottom: 6px;
    min-width: 22px;
    text-align: center;
    padding: 2px 7px;
  }
  .tile :global(.filled .btn) {
    top: 6px;
    right: 6px;
  }
</style>
