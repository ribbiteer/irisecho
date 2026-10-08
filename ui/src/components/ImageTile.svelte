<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Download from "@lucide/svelte/icons/download";
  import Heart from "@lucide/svelte/icons/heart";
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import Trash from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import Iris from "./Iris.svelte";
  import Box from "@lucide/svelte/icons/box";
  import { api, outputUrl, previewUrl } from "../lib/api";
  import { elapsed } from "../lib/format";
  import { app, fail, modelById, submit, toast } from "../lib/state.svelte";
  import type { Job } from "../lib/types";

  let { job }: { job: Job } = $props();

  const out = $derived(job.outputs[0]);
  const isVideo = $derived(out?.type === "video" || job.kind === "video");
  const is3d = $derived(out?.type === "model3d" || job.kind === "model3d");
  const ratio = $derived.by(() => {
    if (is3d) return "1 / 1";
    if (out?.width && out?.height) return `${out.width} / ${out.height}`;
    if (!job.params.aspect && job.kind !== "image" && job.kind !== "video") return "4 / 3";
    const [w, h] = String(job.params.aspect ?? "1:1").split(":").map(Number);
    return w && h ? `${w} / ${h}` : "1 / 1";
  });
  let videoEl = $state<HTMLVideoElement | null>(null);
  const model = $derived(modelById(job.model));
  const position = $derived(
    app.jobs
      .filter((j) => j.status === "queued")
      .sort((a, b) => a.created - b.created)
      .findIndex((j) => j.id === job.id) + 1,
  );

  async function cancel() {
    try {
      await api(`/jobs/${job.id}/cancel`, { body: {} });
    } catch (e) {
      fail(e);
    }
  }
  async function favorite() {
    try {
      await api(`/jobs/${job.id}/favorite`, { body: { favorite: !job.favorite } });
    } catch (e) {
      fail(e);
    }
  }
  function retry() {
    if (model) submit(model, job.params);
  }
  async function remove() {
    try {
      await api(`/jobs/${job.id}`, { method: "DELETE" });
    } catch (e) {
      fail(e);
    }
  }
</script>

<article class="tile" style:aspect-ratio={ratio} class:done={job.status === "done"}>
  {#if job.status === "done" && job.outputs[0]}
    <button
      class="open"
      onclick={() => (app.lightbox = { job, index: 0 })}
      onmouseenter={() => videoEl?.play()}
      onmouseleave={() => {
        if (videoEl) {
          videoEl.pause();
          videoEl.currentTime = 0;
        }
      }}
      aria-label="Open"
    >
      {#if is3d}
        {#if out?.preview}
          <img class="model" src={previewUrl(job)} alt={job.params.prompt ?? "3D model"} loading="lazy" decoding="async" />
        {:else}
          <span class="no-preview"><Box size={48} strokeWidth={1.2} /></span>
        {/if}
        <span class="play-hint">3D</span>
      {:else if isVideo}
        <video bind:this={videoEl} src={`${outputUrl(job)}#t=0.05`} muted loop playsinline preload="auto"></video>
        <span class="play-hint">▶</span>
      {:else}
        <img src={outputUrl(job)} alt={job.params.prompt ?? "Result"} loading="lazy" decoding="async" />
      {/if}
    </button>
    <div class="actions">
      <button class="btn sm icon glass" class:fav={job.favorite} onclick={favorite} title="Favorite">
        <Heart size={15} fill={job.favorite ? "currentColor" : "none"} />
      </button>
      <a class="btn sm icon glass" href={outputUrl(job, 0, true)} title="Download" download><Download size={15} /></a>
    </div>
    <div class="caption">
      <span>{model?.name}</span>
      <span class="mono">{elapsed(job)}</span>
    </div>
  {:else if job.status === "running" || job.status === "queued"}
    <div class="state">
      <span class="mark">
        <Iris size={84} bars={44} active={job.status === "running"} progress={job.status === "running" ? job.progress : 0} />
      </span>
      <div class="words">
        <strong>{job.status === "queued" ? (position ? `Waiting · #${position}` : "Waiting") : (job.message ?? "Working")}</strong>
        <small>{job.params.prompt}</small>
        <div class="buttons">
          <button class="btn sm ghost" onclick={cancel} title="Cancel"><X size={14} /> <span class="label">Cancel</span></button>
        </div>
      </div>
    </div>
  {:else}
    <div class="state muted" title={job.status === "failed" ? (job.error ?? undefined) : undefined}>
      <div class="words">
        {#if job.status === "failed" && job.error}
          <!-- A short tile has no room for the reason, and a phone has no hover: a tap shows it. -->
          <button class="why" onclick={() => toast(job.error ?? "", "error", 8000)}><strong>Didn't work</strong></button>
        {:else}
          <strong>{job.status === "failed" ? "Didn't work" : job.status === "interrupted" ? "Interrupted" : "Cancelled"}</strong>
        {/if}
        <small>{job.status === "failed" ? job.error : job.params.prompt}</small>
        <div class="buttons">
          <button class="btn sm" onclick={retry} title="Try again"><RotateCcw size={14} /> <span class="label">Try again</span></button>
          <button class="btn sm icon ghost danger" onclick={remove} title="Remove" aria-label="Remove"><Trash size={14} /></button>
        </div>
      </div>
    </div>
  {/if}
</article>

<style>
  .tile {
    position: relative;
    border-radius: var(--r-3);
    overflow: hidden;
    background: var(--surface-1);
    border: 1px solid var(--line);
    animation: appear 0.35s var(--ease);
  }
  .open {
    display: block;
    width: 100%;
    height: 100%;
    padding: 0;
    border: 0;
    background: none;
    cursor: zoom-in;
  }
  img,
  video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  img.model {
    object-fit: contain;
    background: radial-gradient(circle at 50% 42%, var(--surface-3), var(--surface-1) 72%);
  }
  .no-preview {
    display: grid;
    place-items: center;
    width: 100%;
    height: 100%;
    color: var(--text-4);
  }
  .play-hint {
    position: absolute;
    left: 10px;
    top: 10px;
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 99px;
    background: rgb(14 11 22 / 0.6);
    color: #fff;
    pointer-events: none;
  }
  .actions {
    position: absolute;
    top: 10px;
    right: 10px;
    display: flex;
    gap: 6px;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .caption {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 24px 12px 10px;
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: rgb(255 255 255 / 0.85);
    background: linear-gradient(to top, rgb(0 0 0 / 0.55), transparent);
    opacity: 0;
    transition: opacity 0.15s;
    pointer-events: none;
  }
  .tile.done:hover .actions,
  .tile.done:hover .caption,
  .tile:focus-within .actions {
    opacity: 1;
  }
  .glass {
    background: rgb(14 11 22 / 0.55);
    border-color: rgb(255 255 255 / 0.15);
    backdrop-filter: blur(8px);
    color: #fff;
  }
  .glass.fav {
    color: #ff8fa3;
  }
  /* A tile takes the shape of what it will hold, so a wide picture makes a short
     tile. The waiting and stopped states size themselves to the tile: the mark
     shrinks with its height, the prompt goes when there is no room for it, and a
     short tile lays out in a row. */
  .tile:not(.done) {
    container: tile / size;
  }
  .state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: clamp(4px, 4cqh, 12px);
    padding: clamp(8px, 6cqh, 18px) 12px;
    text-align: center;
  }
  .mark :global(svg) {
    width: clamp(30px, 34cqh, 84px);
    height: auto;
  }
  .words {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    min-width: 0;
    max-width: 100%;
  }
  .state strong {
    font-size: 13.5px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .state small {
    font-size: 12px;
    color: var(--text-3);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    max-width: 90%;
  }
  .state.muted strong {
    color: var(--text-2);
  }
  .buttons {
    display: flex;
    gap: 6px;
    justify-content: center;
  }
  @container tile (max-height: 210px) {
    .state small {
      display: none;
    }
  }
  @container tile (max-height: 150px) {
    .state {
      flex-direction: row;
      gap: 12px;
      text-align: left;
    }
    .mark :global(svg) {
      width: clamp(30px, 48cqh, 56px);
    }
    .words {
      align-items: flex-start;
      gap: 4px;
    }
    .buttons {
      justify-content: flex-start;
    }
    /* Nothing beside the words in a stopped tile: keep them centred. */
    .muted .words {
      align-items: center;
    }
    .muted .buttons {
      justify-content: center;
    }
  }
  .why {
    border: 0;
    padding: 0;
    background: none;
    color: inherit;
    font: inherit;
    max-width: 100%;
    cursor: help;
    text-decoration: underline dotted var(--text-3);
    text-underline-offset: 3px;
  }
  @container tile (max-width: 200px) and (max-height: 150px) {
    .label {
      display: none;
    }
  }
  @keyframes appear {
    from {
      opacity: 0;
      transform: scale(0.98);
    }
  }
</style>
