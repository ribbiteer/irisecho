<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import { bytes } from "../lib/format";
  import { app } from "../lib/state.svelte";
  import type { Kind, Model } from "../lib/types";

  let { kind, models, selected }: { kind: Kind; models: Model[]; selected: Model } = $props();

  let open = $state(false);
  const choices = $derived(models.filter((m) => m.available));

  function pick(m: Model) {
    app.chosen[kind] = m.id;
    open = false;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") open = false;
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="field picker">
  <span class="label">Model</span>
  <button
    class="current"
    aria-expanded={open}
    aria-haspopup="listbox"
    disabled={choices.length < 2}
    onclick={() => (open = !open)}
  >
    <span class="meta">
      <span class="name">{selected.name}</span>
      <span class="summary">{selected.summary}</span>
    </span>
    <span class="tags">
      {#if !selected.license.commercial}<span class="badge warn">Non-commercial</span>{/if}
      {#if selected.ready}
        <span class="badge ok">Ready</span>
      {:else}
        <span class="badge">{selected.size - selected.have > 0 ? bytes(selected.size - selected.have) : "Files found"}</span>
      {/if}
      {#if choices.length > 1}<ChevronDown size={16} />{/if}
    </span>
  </button>

  {#if open}
    <button class="scrim" aria-label="Close" onclick={() => (open = false)}></button>
    <div class="menu" role="listbox" aria-label="Models">
      {#each choices as m (m.id)}
        <button class="option" role="option" aria-selected={m.id === selected.id} onclick={() => pick(m)}>
          <span class="tick">{#if m.id === selected.id}<Check size={15} />{/if}</span>
          <span class="meta">
            <span class="name">
              {m.name}
              {#if m.preview}<span class="badge">Preview</span>{/if}
            </span>
            <span class="summary">{m.summary}</span>
          </span>
          <span class="tags col">
            {#if m.ready}
              <span class="badge ok">Ready</span>
            {:else}
              <span class="badge">{m.size - m.have > 0 ? bytes(m.size - m.have) : "Files found"}</span>
            {/if}
            {#if !m.license.commercial}<span class="badge warn">Non-commercial</span>{/if}
          </span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .picker {
    position: relative;
  }
  .current,
  .option {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 12px;
    text-align: left;
    border: 1px solid var(--line-2);
    background: var(--surface-2);
    border-radius: var(--r-2);
    padding: 11px 12px;
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .current:hover:not(:disabled) {
    border-color: var(--line-3);
  }
  .current:disabled {
    cursor: default;
    opacity: 1;
  }
  .meta {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .name {
    font-weight: 620;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .summary {
    font-size: 12.5px;
    color: var(--text-3);
    line-height: 1.35;
  }
  .tags {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-3);
  }
  .tags.col {
    flex-direction: column;
    align-items: flex-end;
  }
  .scrim {
    position: fixed;
    inset: 0;
    background: transparent;
    border: 0;
    z-index: 20;
  }
  .menu {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    right: 0;
    z-index: 21;
    background: var(--surface-2);
    border: 1px solid var(--line-2);
    border-radius: var(--r-3);
    box-shadow: var(--shadow-2);
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    animation: drop 0.18s var(--ease);
  }
  .option {
    border-color: transparent;
    background: transparent;
    padding: 10px;
  }
  .option:hover,
  .option[aria-selected="true"] {
    background: var(--surface-3);
  }
  .tick {
    width: 16px;
    color: var(--accent-ink);
    flex: none;
  }
  @keyframes drop {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
</style>
