<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import { app } from "../lib/state.svelte";
</script>

<div class="toasts" role="status" aria-live="polite">
  {#each app.toasts as t (t.id)}
    <div class="toast {t.tone}">
      <span>{t.text}</span>
      {#if t.action}
        <button class="undo" onclick={() => { t.action?.run(); }}>{t.action.label}</button>
      {/if}
    </div>
  {/each}
</div>

<style>
  /* Bottom centre, clear of the delete buttons at the end of rows and on tiles. */
  .toasts {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    bottom: 18px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    z-index: 100;
    width: max-content;
    max-width: min(420px, calc(100vw - 32px));
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
  }
  .toast {
    padding: 11px 14px;
    border-radius: var(--r-2);
    background: var(--surface-3);
    border: 1px solid var(--line-2);
    box-shadow: var(--shadow-2);
    font-size: 13.5px;
    animation: rise 0.3s var(--ease);
    white-space: pre-line;
  }
  .toast:has(.undo) {
    display: flex;
    align-items: center;
    gap: 14px;
    justify-content: space-between;
  }
  .undo {
    border: 0;
    background: transparent;
    color: var(--violet-ink);
    font: inherit;
    font-weight: 600;
    padding: 4px 6px;
    margin: -4px -6px;
    cursor: pointer;
    border-radius: var(--r-1, 6px);
  }
  .undo:hover {
    text-decoration: underline;
  }
  .toast.ok {
    border-color: rgb(47 184 166 / 0.4);
  }
  .toast.error {
    border-color: rgb(239 106 118 / 0.45);
    color: var(--danger-ink);
  }
  @keyframes rise {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
  }
</style>
