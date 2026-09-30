<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  A picture slot: drop, paste or choose an image; shows it once added. -->
<script lang="ts">
  import ImagePlus from "@lucide/svelte/icons/image-plus";
  import X from "@lucide/svelte/icons/x";
  import { api, uploadUrl } from "../lib/api";
  import { fail } from "../lib/state.svelte";

  let {
    value = $bindable(null),
    label,
    hint = "",
    compact = false,
  }: { value: string | null; label: string; hint?: string; compact?: boolean } = $props();

  let busy = $state(false);
  let dragging = $state(false);

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
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }

  function onPaste(e: ClipboardEvent) {
    const file = [...(e.clipboardData?.files ?? [])].find((f) => f.type.startsWith("image/"));
    if (file) {
      e.preventDefault();
      upload(file);
    }
  }
</script>

{#if value}
  <div class="filled" class:compact>
    <img src={uploadUrl(value)} alt={label} />
    <span class="tag">{label}</span>
    <button class="btn sm icon glass" title="Remove" onclick={() => (value = null)}><X size={14} /></button>
  </div>
{:else}
  <label
    class="drop"
    class:compact
    class:dragging
    tabindex="-1"
    onpaste={onPaste}
    ondragover={(e) => {
      e.preventDefault();
      dragging = true;
    }}
    ondragleave={() => (dragging = false)}
    ondrop={(e) => {
      e.preventDefault();
      dragging = false;
      const f = e.dataTransfer?.files?.[0];
      if (f) upload(f);
    }}
  >
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
{/if}

<style>
  .drop {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 132px;
    padding: 16px;
    border-radius: var(--r-3);
    border: 1.5px dashed var(--line-3);
    background: var(--surface-2);
    text-align: center;
    color: var(--accent-ink);
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .drop.compact {
    min-height: 92px;
    padding: 10px;
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
  .drop.dragging {
    border-color: var(--accent);
    background: color-mix(in oklab, var(--accent) 10%, var(--surface-2));
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
