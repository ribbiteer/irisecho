<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Copy from "@lucide/svelte/icons/copy";
  import Download from "@lucide/svelte/icons/download";
  import FolderOpen from "@lucide/svelte/icons/folder-open";
  import Heart from "@lucide/svelte/icons/heart";
  import Repeat from "@lucide/svelte/icons/repeat-2";
  import Trash from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import { api, outputUrl } from "../lib/api";
  import { ago, elapsed } from "../lib/format";
  import { app, fail, modelById, toast } from "../lib/state.svelte";

  const lb = $derived(app.lightbox!);
  const job = $derived(lb.job);
  const model = $derived(modelById(job.model));
  const siblings = $derived(app.jobs.filter((j) => j.kind === job.kind && j.status === "done" && j.outputs.length));
  const at = $derived(siblings.findIndex((j) => j.id === job.id));

  function go(delta: number) {
    const next = siblings[at + delta];
    if (next) app.lightbox = { job: next, index: 0 };
  }
  function close() {
    app.lightbox = null;
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") close();
    if (e.key === "ArrowLeft") go(-1);
    if (e.key === "ArrowRight") go(1);
  }
  async function post(path: string, body: unknown = {}) {
    try {
      await api(path, { body });
    } catch (e) {
      fail(e);
    }
  }
  async function remove() {
    const next = siblings[at + 1] ?? siblings[at - 1];
    try {
      await api(`/jobs/${job.id}`, { method: "DELETE" });
      app.lightbox = next ? { job: next, index: 0 } : null;
    } catch (e) {
      fail(e);
    }
  }
  function reuse() {
    app.reuse = job;
    app.studio = job.kind;
    app.view = "create";
    close();
  }
  async function copyPrompt() {
    await navigator.clipboard.writeText(job.params.prompt ?? job.params.text ?? "");
    toast("Prompt copied", "ok", 1800);
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="lightbox" role="dialog" aria-modal="true" aria-label="Image details">
  <button class="scrim" aria-label="Close" onclick={close}></button>
  <div class="stage">
    {#if job.outputs[lb.index]?.type === "video"}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video src={outputUrl(job, lb.index)} controls autoplay loop playsinline></video>
    {:else}
      <img src={outputUrl(job, lb.index)} alt={job.params.prompt ?? "Result"} />
    {/if}
    {#if at > 0}
      <button class="nav prev btn icon" onclick={() => go(-1)} aria-label="Previous"><ChevronLeft size={20} /></button>
    {/if}
    {#if at < siblings.length - 1}
      <button class="nav next btn icon" onclick={() => go(1)} aria-label="Next"><ChevronRight size={20} /></button>
    {/if}
  </div>
  <aside>
    <div class="top">
      <span class="badge violet">{model?.name ?? job.model}</span>
      <button class="btn icon ghost" onclick={close} aria-label="Close"><X size={18} /></button>
    </div>
    {#if job.params.prompt}
      <p class="prompt">{job.params.prompt}</p>
      <button class="btn sm ghost copy" onclick={copyPrompt}><Copy size={14} /> Copy prompt</button>
    {/if}

    <dl>
      {#if job.outputs[lb.index]?.width}
        <dt>Size</dt>
        <dd class="mono">{job.outputs[lb.index].width} × {job.outputs[lb.index].height}</dd>
      {/if}
      {#if job.params.aspect}
        <dt>Shape</dt>
        <dd class="mono">{job.params.aspect}</dd>
      {/if}
      {#if job.params.scale}
        <dt>Upscale</dt>
        <dd class="mono">{job.params.scale}×{job.params.retain ? " (kept size)" : ""}</dd>
      {/if}
      {#if job.params.seconds}
        <dt>Length</dt>
        <dd class="mono">{job.params.seconds} s{job.params.smooth ? " · 32 fps" : ""}</dd>
      {/if}
      {#if job.params.seed != null}
        <dt>Seed</dt>
        <dd class="mono">{job.params.seed}</dd>
      {/if}
      <dt>Took</dt>
      <dd class="mono">{elapsed(job)}</dd>
      <dt>Made</dt>
      <dd>{ago(job.created)}</dd>
    </dl>

    <div class="actions">
      <button class="btn primary" onclick={reuse}><Repeat size={16} /> Use these settings again</button>
      <div class="row">
        <a class="btn" href={outputUrl(job, lb.index, true)}><Download size={16} /> Download</a>
        {#if !app.remote}
          <button class="btn" onclick={() => post(`/jobs/${job.id}/outputs/${lb.index}/reveal`)}><FolderOpen size={16} /> Show</button>
        {/if}
      </div>
      <div class="row">
        <button class="btn" class:fav={job.favorite} onclick={() => post(`/jobs/${job.id}/favorite`, { favorite: !job.favorite })}>
          <Heart size={16} fill={job.favorite ? "currentColor" : "none"} />
          {job.favorite ? "Favorite" : "Add to favorites"}
        </button>
        <button class="btn danger icon" title="Delete" onclick={remove}><Trash size={16} /></button>
      </div>
    </div>
  </aside>
</div>

<style>
  .lightbox {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: grid;
    grid-template-columns: 1fr 340px;
    animation: fade 0.2s var(--ease);
  }
  .scrim {
    position: absolute;
    inset: 0;
    border: 0;
    background: rgb(8 6 14 / 0.92);
    backdrop-filter: blur(6px);
  }
  .stage {
    position: relative;
    display: grid;
    place-items: center;
    padding: 28px;
    min-height: 0;
    pointer-events: none;
  }
  .stage video {
    max-width: 100%;
    max-height: calc(100vh - 56px);
    border-radius: var(--r-2);
    pointer-events: auto;
  }
  .stage img {
    max-width: 100%;
    max-height: calc(100vh - 56px);
    border-radius: var(--r-2);
    box-shadow: 0 30px 80px -20px rgb(0 0 0 / 0.8);
    pointer-events: auto;
  }
  .nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: auto;
    background: rgb(14 11 22 / 0.7);
    border-radius: 50%;
  }
  .prev {
    left: 22px;
  }
  .next {
    right: 22px;
  }
  aside {
    position: relative;
    background: var(--surface-1);
    border-left: 1px solid var(--line);
    padding: 18px 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    overflow-y: auto;
  }
  .top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .prompt {
    font-size: 15px;
    line-height: 1.5;
  }
  .copy {
    align-self: flex-start;
    margin-left: -10px;
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 8px 16px;
    margin: 4px 0;
    padding: 14px 0;
    border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
    font-size: 13px;
  }
  dt {
    color: var(--text-3);
  }
  dd {
    margin: 0;
    text-align: right;
  }
  .actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .row {
    display: flex;
    gap: 8px;
  }
  .row .btn:not(.icon) {
    flex: 1;
  }
  .fav {
    color: #ff8fa3;
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
  }
</style>
