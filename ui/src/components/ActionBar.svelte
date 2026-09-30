<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Sparkles from "@lucide/svelte/icons/sparkles";
  import SetupPanel from "./SetupPanel.svelte";
  import type { Model } from "../lib/types";

  let {
    model,
    label,
    disabled = false,
    hint = "",
    onsubmit,
  }: { model: Model; label: string; disabled?: boolean; hint?: string; onsubmit: () => void } = $props();

  const mac = navigator.platform.toLowerCase().includes("mac");
</script>

<div class="bar">
  {#if model.ready}
    <button class="btn primary go" {disabled} onclick={onsubmit}>
      <Sparkles size={17} />
      {label}
    </button>
    <p class="hint">
      {#if hint}{hint} · {/if}<kbd>{mac ? "⌘" : "Ctrl"}</kbd> <kbd>Enter</kbd>
    </p>
  {:else}
    <SetupPanel {model} />
  {/if}
</div>

<style>
  .bar {
    position: sticky;
    bottom: 0;
    margin: auto -22px 0;
    padding: 14px 22px 16px;
    background: var(--surface-1);
    border-top: 1px solid var(--line);
    box-shadow: 0 -18px 24px -18px rgb(0 0 0 / 0.7);
    display: flex;
    flex-direction: column;
    gap: 8px;
    pointer-events: none;
  }
  .bar > :global(*) {
    pointer-events: auto;
  }
  @media (hover: none) {
    kbd {
      display: none;
    }
  }
  @media (max-width: 820px) {
    .bar {
      margin: auto -16px 0;
      padding: 12px 16px calc(14px + env(safe-area-inset-bottom));
    }
  }
  .go {
    height: 48px;
    font-size: 15.5px;
    border-radius: var(--r-3);
    box-shadow: 0 10px 30px -12px color-mix(in oklab, var(--accent) 80%, transparent);
  }
  .hint {
    text-align: center;
    font-size: 12px;
    color: var(--text-4);
  }
  kbd {
    font-family: var(--mono);
    font-size: 11px;
    padding: 1px 5px;
    border-radius: 5px;
    border: 1px solid var(--line-2);
    color: var(--text-3);
  }
</style>
