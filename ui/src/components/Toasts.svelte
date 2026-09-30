<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import { app } from "../lib/state.svelte";
</script>

<div class="toasts" role="status" aria-live="polite">
  {#each app.toasts as t (t.id)}
    <div class="toast {t.tone}">{t.text}</div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    right: 18px;
    bottom: 18px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    z-index: 100;
    max-width: 420px;
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
