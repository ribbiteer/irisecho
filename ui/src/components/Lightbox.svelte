<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Copy from "@lucide/svelte/icons/copy";
  import Download from "@lucide/svelte/icons/download";
  import FolderOpen from "@lucide/svelte/icons/folder-open";
  import Heart from "@lucide/svelte/icons/heart";
  import Maximize from "@lucide/svelte/icons/maximize-2";
  import Repeat from "@lucide/svelte/icons/repeat-2";
  import Trash from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import Box from "@lucide/svelte/icons/box";
  import Grid from "@lucide/svelte/icons/grid-3x3";
  import Palette from "@lucide/svelte/icons/palette";
  import RotateCw from "@lucide/svelte/icons/rotate-3d";
  import Focus from "@lucide/svelte/icons/scan";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import Viewer3D from "./Viewer3D.svelte";
  import { api, exportUrl, outputUrl } from "../lib/api";
  import { ago, elapsed } from "../lib/format";
  import { app, fail, modelById, submit, toast } from "../lib/state.svelte";

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
  // A swipe across the picture moves to the next or previous one on a phone.
  let touchX: number | null = null;
  function touchStart(e: TouchEvent) {
    touchX = e.touches.length === 1 ? e.touches[0].clientX : null;
  }
  function touchEnd(e: TouchEvent) {
    if (touchX == null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    touchX = null;
    if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
  }
  // 3D models: how they are shown, and what they are exported as.
  const out = $derived(job.outputs[lb.index]);
  const is3d = $derived(out?.type === "model3d");
  const mesh = $derived(out?.mesh);
  let viewMode = $state<"textured" | "clay" | "wireframe">("textured");
  let spin = $state(false);
  let viewer = $state<Viewer3D>();
  const FORMATS = [
    { id: "glb", label: "GLB", note: "With its textures, for Blender, game engines and the web" },
    { id: "stl", label: "STL", note: "For any slicer: upright, on the plate, in millimetres" },
    { id: "3mf", label: "3MF", note: "For Bambu Studio, PrusaSlicer and Orca: upright, in millimetres" },
    { id: "obj", label: "OBJ", note: "With its texture, as a zip, for older 3D programs" },
    { id: "ply", label: "PLY", note: "With its colours painted on the points" },
  ] as const;
  let format = $state<(typeof FORMATS)[number]["id"]>("glb");
  let heightMm = $state(100);
  const forPrint = $derived(format === "stl" || format === "3mf");
  // Width × depth × height in millimetres at the chosen printed height (models are Y up).
  const printSize = $derived.by(() => {
    const e = mesh?.extents;
    if (!e || !e[1]) return null;
    const k = heightMm / e[1];
    return [e[0] * k, e[2] * k, heightMm].map((v) => (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10));
  });

  let expanded = $state(false);
  $effect(() => {
    void job.id;
    expanded = false;
  });
  async function copyPrompt() {
    await navigator.clipboard.writeText(job.params.prompt ?? job.params.text ?? "");
    toast("Prompt copied", "ok", 1800);
  }

  // Redraw the whole picture with FLUX.1 Krea at about twice the pixels and a
  // light denoise: the people and the composition stay, skin and hair gain
  // detail. Offered for pictures well under the largest size that fits.
  const MAX_AREA = 1920 * 1088; // graphs.FLUX_MAX_AREA
  const krea = $derived(modelById("flux-krea"));
  const redrawSize = $derived.by((): [number, number] | null => {
    const out = job.outputs[lb.index];
    if (job.kind !== "image" || out?.type !== "image" || !out.width || !out.height || !job.params.prompt) return null;
    const area = out.width * out.height;
    if (area * 1.5 > MAX_AREA) return null;
    const s = Math.min(Math.SQRT2, Math.sqrt(MAX_AREA / area));
    let w = Math.round((out.width * s) / 16) * 16;
    let h = Math.round((out.height * s) / 16) * 16;
    while (w * h > MAX_AREA) {
      if (w >= h) w -= 16;
      else h -= 16;
    }
    return [w, h];
  });
  let redrawing = $state(false);
  async function redraw() {
    if (!redrawSize || !krea || redrawing) return;
    redrawing = true;
    try {
      const res = await fetch(outputUrl(job, lb.index), { credentials: "same-origin" });
      const blob = await res.blob();
      const form = new FormData();
      form.append("file", new File([blob], "redraw.png", { type: blob.type || "image/png" }));
      const up = await api<{ id: string }>("/uploads", { form });
      const [width, height] = redrawSize;
      const queued = await submit(krea, {
        prompt: job.params.prompt,
        aspect: job.params.aspect,
        image1: up.id,
        denoise: 0.2,
        width,
        height,
        seed: Math.floor(Math.random() * 2 ** 50),
      });
      if (queued) toast(`Redrawing at ${width} × ${height}`, "ok");
    } catch (e) {
      fail(e);
    } finally {
      redrawing = false;
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="lightbox" role="dialog" aria-modal="true" aria-label="Image details">
  <button class="scrim" aria-label="Close" onclick={close}></button>
  <div class="stage">
    {#if is3d}
      {#key `${job.id}/${lb.index}`}
        <div class="model3d">
          <Viewer3D bind:this={viewer} url={outputUrl(job, lb.index)} mode={viewMode} {spin} />
          <div class="tools" role="toolbar" aria-label="View">
            <button class="btn sm glass" aria-pressed={viewMode === "textured"} onclick={() => (viewMode = "textured")} title="Colours and textures"><Palette size={15} /> Colour</button>
            <button class="btn sm glass" aria-pressed={viewMode === "clay"} onclick={() => (viewMode = "clay")} title="The shape alone, as it will print"><Box size={15} /> Shape</button>
            <button class="btn sm glass" aria-pressed={viewMode === "wireframe"} onclick={() => (viewMode = "wireframe")} title="The mesh's triangles"><Grid size={15} /> Mesh</button>
            <span class="gap"></span>
            <button class="btn sm icon glass" aria-pressed={spin} onclick={() => (spin = !spin)} title="Turn it slowly"><RotateCw size={15} /></button>
            <button class="btn sm icon glass" onclick={() => viewer?.reset()} title="Back to the first view (or double-click it)"><Focus size={15} /></button>
          </div>
        </div>
      {/key}
    {:else if job.outputs[lb.index]?.type === "video"}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video src={outputUrl(job, lb.index)} controls autoplay loop playsinline></video>
    {:else}
      <img src={outputUrl(job, lb.index)} alt={job.params.prompt ?? "Result"} ontouchstart={touchStart} ontouchend={touchEnd} />
    {/if}
    <button class="close-float btn icon" onclick={close} aria-label="Close"><X size={20} /></button>
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
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
      <p class="prompt" class:expanded onclick={() => (expanded = !expanded)}>{job.params.prompt}</p>
      <button class="btn sm ghost copy" onclick={copyPrompt}><Copy size={14} /> Copy prompt</button>
    {/if}

    {#if is3d && mesh && !mesh.error}
      <div class="verdict" class:ok={mesh.printable}>
        {#if mesh.printable}
          <CircleCheck size={18} />
          <div><strong>Ready to print</strong><small>One closed, solid piece. No repairs needed.</small></div>
        {:else}
          <TriangleAlert size={18} />
          <div><strong>Check before printing</strong><small>{(mesh.reasons ?? []).join(" ")}</small></div>
        {/if}
      </div>
    {/if}

    <dl>
      {#if is3d && printSize}
        <div><dt>Printed size</dt><dd class="mono">{printSize[0]} × {printSize[1]} × {printSize[2]} mm</dd></div>
      {/if}
      {#if is3d && mesh?.faces}
        <div><dt>Triangles</dt><dd class="mono">{mesh.faces.toLocaleString()}</dd></div>
      {/if}
      {#if is3d && out?.resolution}
        <div><dt>Detail</dt><dd class="mono">{out.resolution >= 1536 ? "High" : out.resolution > 1024 ? `High (${out.resolution})` : "Standard"}</dd></div>
      {/if}
      {#if job.outputs[lb.index]?.width}
        <div><dt>Size</dt><dd class="mono">{job.outputs[lb.index].width} × {job.outputs[lb.index].height}</dd></div>
      {/if}
      {#if job.params.aspect}
        <div><dt>Shape</dt><dd class="mono">{job.params.aspect}</dd></div>
      {/if}
      {#if job.params.image1 && job.params.denoise != null}
        <div><dt>Redrawn</dt><dd class="mono">denoise {job.params.denoise}</dd></div>
      {/if}
      {#if job.params.scale}
        <div><dt>Upscale</dt><dd class="mono">{job.params.scale}×{job.params.retain ? " (kept size)" : ""}</dd></div>
      {/if}
      {#if job.params.seconds}
        <div><dt>Length</dt><dd class="mono">{job.params.seconds} s{job.params.smooth ? " · smooth motion" : ""}</dd></div>
      {/if}
      {#if job.params.seed != null}
        <div><dt>Seed</dt><dd class="mono">{job.params.seed}</dd></div>
      {/if}
      <div><dt>Took</dt><dd class="mono">{elapsed(job)}</dd></div>
      <div><dt>Made</dt><dd>{ago(job.created)}</dd></div>
    </dl>

    {#if is3d}
      <div class="export">
        <span class="label">Export</span>
        <div class="chips">
          {#each FORMATS as f (f.id)}
            <button class="chip" aria-pressed={format === f.id} onclick={() => (format = f.id)}>{f.label}</button>
          {/each}
        </div>
        <p class="note">{FORMATS.find((f) => f.id === format)?.note}</p>
        {#if forPrint}
          <label class="height">
            <span>Printed height</span>
            <input class="input mono" type="number" min="5" max="2000" step="1" bind:value={heightMm} />
            <span>mm</span>
          </label>
        {/if}
        <a class="btn primary" href={exportUrl(job, lb.index, format, forPrint ? heightMm : undefined)} download>
          <Download size={16} /> Download {FORMATS.find((f) => f.id === format)?.label}
        </a>
      </div>
    {/if}

    <div class="actions">
      <button class={is3d ? "btn" : "btn primary"} onclick={reuse}><Repeat size={16} /> Use these settings again</button>
      {#if krea?.ready && redrawSize}
        <button
          class="btn"
          onclick={redraw}
          disabled={redrawing}
          title="Redraws the whole picture at {redrawSize[0]} × {redrawSize[1]} with {krea.name} ({krea.license.name}). People and composition stay; small objects can change."
        >
          <Maximize size={16} /> Redraw larger
        </button>
      {/if}
      <div class="row">
        {#if !is3d}
          <a class="btn" href={outputUrl(job, lb.index, true)}><Download size={16} /> Download</a>
        {/if}
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
  .model3d {
    position: absolute;
    inset: 0;
    pointer-events: auto;
  }
  .tools {
    position: absolute;
    left: 50%;
    bottom: 22px;
    transform: translateX(-50%);
    display: flex;
    gap: 6px;
    padding: 6px;
    border-radius: 99px;
    background: rgb(14 11 22 / 0.55);
    backdrop-filter: blur(8px);
  }
  .tools .gap {
    width: 6px;
  }
  .glass {
    background: transparent;
    border-color: transparent;
    color: rgb(255 255 255 / 0.85);
    border-radius: 99px;
  }
  .glass[aria-pressed="true"] {
    background: color-mix(in oklab, var(--violet) 40%, transparent);
    color: #fff;
  }
  .verdict {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 10px 12px;
    border-radius: var(--r-2);
    background: color-mix(in oklab, var(--amber) 14%, transparent);
    color: var(--amber-ink);
  }
  .verdict.ok {
    background: color-mix(in oklab, var(--teal) 14%, transparent);
    color: var(--teal-ink);
  }
  .verdict div {
    display: grid;
    gap: 2px;
  }
  .verdict strong {
    color: var(--text);
    font-size: 13.5px;
  }
  .verdict small {
    color: var(--text-2);
    font-size: 12.5px;
    line-height: 1.4;
  }
  .export {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--line);
  }
  .export .note {
    font-size: 12px;
    color: var(--text-3);
    min-height: 2.6em;
  }
  .height {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-2);
  }
  .height input {
    width: 90px;
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
  dl > div {
    display: contents;
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
  .close-float {
    display: none;
  }

  /* A phone: the picture first and as large as the screen allows, the details
     below it in a compact panel, and the whole thing scrolls. */
  @media (max-width: 820px) {
    .lightbox {
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      overscroll-behavior: contain;
      background: rgb(8 6 14 / 0.96);
    }
    .scrim {
      position: fixed;
    }
    .stage {
      flex: none;
      padding: 56px 0 10px;
    }
    .model3d {
      position: relative;
      inset: auto;
      width: 100%;
      height: 62dvh;
    }
    .tools {
      bottom: 10px;
    }
    .stage img,
    .stage video {
      width: 100%;
      max-height: 70dvh;
      object-fit: contain;
      border-radius: 0;
      box-shadow: none;
    }
    .close-float {
      display: grid;
      position: absolute;
      top: 8px;
      right: 10px;
      pointer-events: auto;
      background: rgb(14 11 22 / 0.7);
      border-radius: 50%;
    }
    .nav {
      top: calc(50% + 23px);
    }
    .prev {
      left: 8px;
    }
    .next {
      right: 8px;
    }
    aside {
      flex: none;
      overflow: visible;
      border-left: 0;
      border-top: 1px solid var(--line);
      border-radius: var(--r-3) var(--r-3) 0 0;
      padding: 14px 16px calc(18px + env(safe-area-inset-bottom));
      gap: 10px;
    }
    .top .btn {
      display: none;
    }
    .prompt {
      font-size: 14px;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
      cursor: pointer;
    }
    .prompt.expanded {
      display: block;
    }
    dl {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 14px;
      margin: 0;
      padding: 10px 0;
      font-size: 12px;
    }
    dl > div {
      display: flex;
      gap: 5px;
    }
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
  }
</style>
