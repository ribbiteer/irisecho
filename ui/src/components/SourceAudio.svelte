<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- A recording to start from: drop or choose a file, hear it, take it away again. -->
<script lang="ts">
  import FileAudio from "@lucide/svelte/icons/file-audio";
  import X from "@lucide/svelte/icons/x";
  import Waveform from "./Waveform.svelte";
  import { api, uploadUrl } from "../lib/api";
  import { looksLikeAudio, readableAudio } from "../lib/audio";
  import { fail } from "../lib/state.svelte";

  type Source = { id: string; name: string } | null;
  let {
    value = $bindable(null),
    prompt = "Drop a recording, or choose a file",
    hint = "",
    seconds = 240,
  }: { value: Source; prompt?: string; hint?: string; seconds?: number } = $props();

  let uploading = $state(false);
  let dragging = $state(false);

  async function upload(file: File) {
    if (!looksLikeAudio(file)) {
      fail("Use a WAV, MP3, FLAC, OGG or M4A recording.");
      return;
    }
    uploading = true;
    try {
      const form = new FormData();
      form.append("file", await readableAudio(file, { seconds, stereo: true }));
      const r = await api<{ id: string }>("/uploads", { form });
      value = { id: r.id, name: file.name };
    } catch (e) {
      fail(e);
    } finally {
      uploading = false;
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    const file = e.dataTransfer?.files?.[0];
    if (file) upload(file);
  }
</script>

{#if value}
  <div class="sample">
    <div class="head">
      <FileAudio size={16} />
      <span class="fname">{value.name}</span>
      <button class="btn sm icon ghost" title="Remove it" aria-label="Remove the recording" onclick={() => (value = null)}><X size={14} /></button>
    </div>
    <Waveform src={uploadUrl(value.id)} bars={64} height={32} />
  </div>
{:else}
  <label
    class="drop"
    class:dragging
    ondragover={(e) => {
      e.preventDefault();
      dragging = true;
    }}
    ondragleave={() => (dragging = false)}
    ondrop={onDrop}
  >
    <input
      type="file"
      accept="audio/*"
      class="sr-only"
      onchange={(e) => {
        const f = (e.currentTarget as HTMLInputElement).files?.[0];
        if (f) upload(f);
      }}
    />
    <FileAudio size={22} />
    <strong>{uploading ? "Adding…" : prompt}</strong>
    {#if hint}<small>{hint}</small>{/if}
  </label>
{/if}

<style>
  .drop {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
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
  .drop strong {
    color: var(--text);
    font-weight: 580;
  }
  .drop small {
    color: var(--text-3);
    font-size: 12px;
    max-width: 300px;
  }
  .drop:hover,
  .drop.dragging {
    border-color: var(--accent);
    background: color-mix(in oklab, var(--accent) 10%, var(--surface-2));
  }
  .sample {
    padding: 10px 12px 12px;
    border-radius: var(--r-3);
    background: var(--surface-2);
    border: 1px solid var(--line-2);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--accent-ink);
  }
  .fname {
    flex: 1;
    color: var(--text);
    font-size: 13px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
