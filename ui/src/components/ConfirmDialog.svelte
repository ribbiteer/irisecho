<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- A question that needs a yes before something is removed. Escape or the shaded area says no. -->
<script lang="ts">
  import { onMount } from "svelte";

  let {
    title,
    text,
    confirm = "Remove",
    busy = false,
    onconfirm,
    oncancel,
  }: { title: string; text: string; confirm?: string; busy?: boolean; onconfirm: () => void; oncancel: () => void } = $props();

  let cancelButton = $state<HTMLButtonElement>();
  onMount(() => cancelButton?.focus());
</script>

<svelte:window onkeydown={(e) => e.key === "Escape" && !busy && oncancel()} />

<div class="wrap" role="alertdialog" aria-modal="true" aria-label={title}>
  <button class="scrim" aria-label="Cancel" onclick={() => !busy && oncancel()}></button>
  <div class="box">
    <h3>{title}</h3>
    <p>{text}</p>
    <div class="buttons">
      <button class="btn" bind:this={cancelButton} onclick={oncancel} disabled={busy}>Cancel</button>
      <button class="btn danger" onclick={onconfirm} disabled={busy}>{confirm}</button>
    </div>
  </div>
</div>

<style>
  .wrap {
    position: fixed;
    inset: 0;
    z-index: 80;
    display: grid;
    place-items: center;
    padding: 16px;
  }
  .scrim {
    position: absolute;
    inset: 0;
    border: 0;
    background: rgb(8 6 14 / 0.7);
  }
  .box {
    position: relative;
    width: min(420px, 100%);
    padding: 20px;
    border-radius: var(--r-3);
    background: var(--surface-2);
    border: 1px solid var(--line-2);
    box-shadow: var(--shadow-2);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  h3 {
    font-size: 17px;
  }
  p {
    color: var(--text-2);
    font-size: 14px;
    line-height: 1.5;
  }
  .buttons {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 6px;
  }
</style>
