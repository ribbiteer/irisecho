<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  A picture slot: choose or paste an image; shows it once added.

  Paste works three ways. Ctrl+V anywhere on the page fills the slot under the pointer, or
  the first empty one. The Paste button reads the clipboard where the browser allows it.
  Where it does not (a phone on plain HTTP, or iOS), the button opens a small pad: hold it,
  choose Paste, and the browser hands over the picture. -->
<script lang="ts" module>
  // Every empty slot on screen, so a paste can find the right one.
  type Slot = { el: () => HTMLElement | undefined; take: (file: File) => void };
  const slots = new Set<Slot>();

  const imageIn = (data: DataTransfer | null | undefined) =>
    [...(data?.files ?? [])].find((f) => f.type.startsWith("image/"));

  function onDocumentPaste(e: ClipboardEvent) {
    const file = imageIn(e.clipboardData);
    // Text on the clipboard stays text: a prompt box keeps pasting words.
    if (!file || e.clipboardData?.getData("text/plain")?.trim()) return;
    const open = [...slots].filter((s) => s.el()?.offsetParent);
    if (!open.length) return;
    open.sort((a, b) =>
      a.el()!.compareDocumentPosition(b.el()!) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    );
    const under = open.find((s) => s.el()!.matches(":hover, :focus-within"));
    e.preventDefault();
    (under ?? open[0]).take(file);
  }

  function join(slot: Slot) {
    if (!slots.size) document.addEventListener("paste", onDocumentPaste);
    slots.add(slot);
    return () => {
      slots.delete(slot);
      if (!slots.size) document.removeEventListener("paste", onDocumentPaste);
    };
  }
</script>

<script lang="ts">
  import ClipboardPaste from "@lucide/svelte/icons/clipboard-paste";
  import ImagePlus from "@lucide/svelte/icons/image-plus";
  import X from "@lucide/svelte/icons/x";
  import { tick } from "svelte";
  import { api, uploadUrl } from "../lib/api";
  import { fail } from "../lib/state.svelte";

  let {
    value = $bindable(null),
    label,
    tag = "",
    hint = "",
    compact = false,
  }: { value: string | null; label: string; tag?: string; hint?: string; compact?: boolean } = $props();

  let busy = $state(false);
  let pad = $state(false);
  let box = $state<HTMLElement>();
  let padEl = $state<HTMLElement>();

  async function upload(file: File) {
    if (!file.type.startsWith("image/")) {
      fail("Use a PNG, JPEG or WebP picture.");
      return;
    }
    busy = true;
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api<{ id: string }>("/uploads", { form });
      value = r.id;
      pad = false;
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }

  // While the slot is empty, it takes part in page-wide Ctrl+V.
  $effect(() => {
    if (value) return;
    return join({ el: () => box, take: upload });
  });

  const canRead = typeof navigator !== "undefined" && typeof navigator.clipboard?.read === "function";

  async function paste() {
    if (canRead) {
      try {
        for (const item of await navigator.clipboard.read()) {
          const type = item.types.find((t) => t.startsWith("image/"));
          if (type) {
            await upload(new File([await item.getType(type)], "pasted", { type }));
            return;
          }
        }
        fail("There is no picture on the clipboard. Copy one, then try again.");
        return;
      } catch {
        /* the browser refused to share the clipboard: use the pad */
      }
    }
    pad = true;
    await tick();
    padEl?.focus();
  }

  function onPadPaste(e: ClipboardEvent) {
    e.preventDefault();
    const file = imageIn(e.clipboardData);
    if (file) upload(file);
    else fail("There is no picture on the clipboard. Copy one, then try again.");
  }
</script>

{#if value}
  <div class="filled" class:compact>
    <img src={uploadUrl(value)} alt={tag || label} />
    <span class="tag">{tag || label}</span>
    <button class="btn sm icon glass" title="Remove" onclick={() => (value = null)}><X size={14} /></button>
  </div>
{:else}
  <div class="drop" class:compact bind:this={box}>
    <label class="choose">
      <input
        type="file"
        accept="image/png,image/jpeg,image/webp"
        class="sr-only"
        onchange={(e) => {
          const f = (e.currentTarget as HTMLInputElement).files?.[0];
          if (f) upload(f);
        }}
      />
      <ImagePlus size={compact ? 18 : 22} />
      <strong>{busy ? "Adding…" : label}</strong>
      {#if hint && !compact}<small>{hint}</small>{/if}
    </label>
    {#if pad}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <div
        class="pad"
        bind:this={padEl}
        contenteditable="true"
        inputmode="none"
        role="textbox"
        tabindex="0"
        aria-label="Paste a picture here"
        onpaste={onPadPaste}
        onbeforeinput={(e) => e.preventDefault()}
      >
        Hold here and choose Paste, or press Ctrl+V
      </div>
    {:else}
      <button class="btn sm ghost" onclick={paste} disabled={busy}><ClipboardPaste size={14} /> Paste</button>
    {/if}
  </div>
{/if}

<style>
  .drop {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 132px;
    padding: 14px 16px;
    border-radius: var(--r-3);
    border: 1.5px dashed var(--line-3);
    background: var(--surface-2);
    text-align: center;
    color: var(--accent-ink);
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .drop.compact {
    min-height: 92px;
    padding: 8px 10px;
    gap: 4px;
  }
  .choose {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }
  .drop strong {
    color: var(--text);
    font-weight: 560;
    font-size: 13px;
  }
  .drop small {
    color: var(--text-3);
    font-size: 12px;
    max-width: 260px;
  }
  .drop:hover,
  .drop:focus-within {
    border-color: var(--accent);
    background: color-mix(in oklab, var(--accent) 10%, var(--surface-2));
  }
  .pad {
    padding: 8px 12px;
    border-radius: var(--r-2);
    border: 1px solid var(--accent);
    background: var(--surface);
    color: var(--text-2);
    font-size: 12.5px;
    cursor: text;
    caret-color: transparent;
    outline: none;
  }
  .filled {
    position: relative;
    border-radius: var(--r-3);
    overflow: hidden;
    border: 1px solid var(--line-2);
    background: var(--surface-2);
    height: 180px;
  }
  .filled.compact {
    height: 92px;
  }
  .filled img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  .tag {
    position: absolute;
    left: 8px;
    bottom: 8px;
    font-size: 11.5px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 99px;
    background: rgb(14 11 22 / 0.7);
    color: #fff;
  }
  .filled .btn {
    position: absolute;
    top: 8px;
    right: 8px;
  }
  .glass {
    background: rgb(14 11 22 / 0.6);
    border-color: rgb(255 255 255 / 0.15);
    color: #fff;
  }
</style>
