<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- A recording to start from: drop or choose a file, hear it, take it away again. -->
<script lang="ts">
  import FileAudio from "@lucide/svelte/icons/file-audio";
  import Mic from "@lucide/svelte/icons/mic";
  import Square from "@lucide/svelte/icons/square";
  import X from "@lucide/svelte/icons/x";
  import { onDestroy } from "svelte";
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
    stereo = true,
    record = false,
  }: { value: Source; prompt?: string; hint?: string; seconds?: number; stereo?: boolean; record?: boolean } = $props();

  let uploading = $state(false);
  let dragging = $state(false);

  // Recording from the microphone. Browsers allow it only on a secure page: the app on
  // this computer is (127.0.0.1), a phone over plain HTTP on the network is not, so the
  // button is shown only where it can work.
  const canRecord =
    typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
  let recorder: MediaRecorder | null = null;
  let stream: MediaStream | null = null;
  let recording = $state(false);
  let elapsed = $state(0);
  let ticker: ReturnType<typeof setInterval> | null = null;

  function release() {
    if (ticker) clearInterval(ticker);
    ticker = null;
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
    recording = false;
  }

  async function startRecording() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      release();
      fail("IrisEcho could not use the microphone. Allow it, or choose a file instead.");
      return;
    }
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    rec.onstop = () => {
      const took = elapsed;
      release();
      recorder = null;
      if (!chunks.length || took < 1) return;
      const type = rec.mimeType || chunks[0].type || "audio/webm";
      const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
      upload(new File(chunks, `Recording (${Math.round(took)} s).${ext}`, { type }));
    };
    recorder = rec;
    elapsed = 0;
    const began = Date.now();
    ticker = setInterval(() => {
      elapsed = (Date.now() - began) / 1000;
      if (elapsed >= seconds) stopRecording(); // the most a sample can use
    }, 200);
    rec.start();
    recording = true;
  }

  function stopRecording() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
    else release();
  }

  onDestroy(() => {
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
    }
    release();
  });

  async function upload(file: File) {
    if (!looksLikeAudio(file)) {
      fail("Use a WAV, MP3, FLAC, OGG or M4A recording.");
      return;
    }
    uploading = true;
    try {
      const form = new FormData();
      form.append("file", await readableAudio(file, { seconds, stereo }));
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
  {#if record && canRecord}
    <div class="rec">
      {#if recording}
        <span class="dot" aria-hidden="true"></span>
        <span class="time">{elapsed.toFixed(0)} s</span>
        <span class="aim">{elapsed < 10 ? "keep talking, 10 to 15 s is best" : "that is enough; stop when ready"}</span>
        <button class="btn sm" onclick={stopRecording}><Square size={13} /> Stop</button>
      {:else}
        <button class="btn sm ghost" onclick={startRecording} disabled={uploading}><Mic size={14} /> Record a sample</button>
      {/if}
    </div>
  {/if}
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
  .rec {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 8px;
    font-size: 13px;
    color: var(--text-2);
  }
  .rec .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--danger-ink);
    animation: blink 1s infinite;
  }
  .rec .time {
    font-variant-numeric: tabular-nums;
    color: var(--text);
    min-width: 32px;
  }
  .rec .aim {
    flex: 1;
    color: var(--text-3);
  }
  @keyframes blink {
    50% {
      opacity: 0.3;
    }
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
