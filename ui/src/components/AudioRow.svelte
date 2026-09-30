<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Download from "@lucide/svelte/icons/download";
  import FolderOpen from "@lucide/svelte/icons/folder-open";
  import Heart from "@lucide/svelte/icons/heart";
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import Trash from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import Iris from "./Iris.svelte";
  import Waveform from "./Waveform.svelte";
  import { api, outputUrl } from "../lib/api";
  import { ago } from "../lib/format";
  import { app, fail, modelById, submit } from "../lib/state.svelte";
  import type { Job } from "../lib/types";

  let { job }: { job: Job } = $props();

  const model = $derived(modelById(job.model));
  const voiceLabel = $derived(model?.options.voices?.find((v) => v.id === job.params.voice)?.label ?? job.params.voice);
  const title = $derived(job.params.text ?? job.params.prompt ?? "");
  let take = $state(0);
  const current = $derived(Math.min(take, Math.max(0, job.outputs.length - 1)));

  async function post(path: string, body: unknown = {}) {
    try {
      await api(path, { body });
    } catch (e) {
      fail(e);
    }
  }
  async function remove() {
    try {
      await api(`/jobs/${job.id}`, { method: "DELETE" });
    } catch (e) {
      fail(e);
    }
  }
</script>

<article class="row sound" class:failed={job.status === "failed"}>
  <div class="head">
    <p class="text">{title}</p>
    <div class="meta">
      {#if voiceLabel}<span class="badge">{voiceLabel}</span>{/if}
      {#if job.params.bpm}<span class="badge mono">{job.params.bpm} BPM</span>{/if}
      {#if job.params.loop}<span class="badge">Loop</span>{/if}
      <span>{model?.name}</span>
      <span>·</span>
      <span>{ago(job.created)}</span>
    </div>
  </div>

  {#if job.status === "done" && job.outputs[0]}
    {#if job.outputs.length > 1}
      <div class="takes" role="tablist" aria-label="Takes">
        {#each job.outputs as _, i (i)}
          <button class="take" role="tab" aria-selected={current === i} onclick={() => (take = i)}>Take {i + 1}</button>
        {/each}
      </div>
    {/if}
    <div class="player">
      {#key current}
        <Waveform src={outputUrl(job, current)} duration={job.outputs[current]?.duration ?? 0} />
      {/key}
      <div class="tools">
        <button class="btn sm icon ghost" class:fav={job.favorite} title="Favorite" onclick={() => post(`/jobs/${job.id}/favorite`, { favorite: !job.favorite })}>
          <Heart size={15} fill={job.favorite ? "currentColor" : "none"} />
        </button>
        <a class="btn sm icon ghost" href={outputUrl(job, current, true)} title="Download"><Download size={15} /></a>
        {#if !app.remote}
          <!-- The folder is on the computer; a paired phone has nothing to open it with. -->
          <button class="btn sm icon ghost" title="Show in folder" onclick={() => post(`/jobs/${job.id}/outputs/${current}/reveal`)}>
            <FolderOpen size={15} />
          </button>
        {/if}
        <button class="btn sm icon ghost danger" title="Delete" onclick={remove}><Trash size={15} /></button>
      </div>
    </div>
  {:else if job.status === "running" || job.status === "queued"}
    <div class="working">
      <Iris size={38} bars={32} active={job.status === "running"} progress={job.status === "running" ? job.progress : 0} catchlight={false} />
      <span>{job.status === "queued" ? "Waiting for the GPU" : (job.message ?? "Working")}</span>
      <button class="btn sm ghost" onclick={() => post(`/jobs/${job.id}/cancel`)}><X size={14} /> Cancel</button>
    </div>
  {:else}
    <div class="working">
      <span class="err">{job.status === "failed" ? job.error : job.status === "interrupted" ? "Interrupted" : "Cancelled"}</span>
      <button class="btn sm" onclick={() => model && submit(model, job.params)}><RotateCcw size={14} /> Try again</button>
      <button class="btn sm icon ghost danger" title="Delete" onclick={remove}><Trash size={15} /></button>
    </div>
  {/if}
</article>

<style>
  .row {
    padding: 14px 16px;
    border-radius: var(--r-3);
    background: var(--surface-1);
    border: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    gap: 12px;
    animation: appear 0.3s var(--ease);
  }
  .row.failed {
    border-color: rgb(239 106 118 / 0.25);
  }
  .head {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .text {
    font-size: 14.5px;
    line-height: 1.45;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-3);
  }
  .takes {
    display: flex;
    gap: 4px;
    margin-bottom: -4px;
  }
  .take {
    border: 0;
    background: transparent;
    color: var(--text-3);
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 99px;
    cursor: pointer;
  }
  .take:hover {
    color: var(--text);
  }
  .take[aria-selected="true"] {
    background: color-mix(in oklab, var(--accent) 22%, transparent);
    color: var(--accent-ink);
  }
  .player {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .player :global(.wave) {
    flex: 1;
  }
  .tools {
    display: flex;
    gap: 2px;
  }
  .fav {
    color: #ff8fa3;
  }
  .working {
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--text-2);
    font-size: 13px;
  }
  .working span {
    flex: 1;
  }
  .err {
    color: var(--danger-ink);
  }
  @keyframes appear {
    from {
      opacity: 0;
      transform: translateY(4px);
    }
  }
</style>
