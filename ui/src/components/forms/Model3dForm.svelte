<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  The 3D studio. A picture (added, or made from a description) becomes a front
  and a back view when it was taken at eye level, and Pixal3D builds from both;
  a picture taken from above or at an angle goes to TRELLIS.2 on its own, which
  stands the object upright. The person sees the views before anything is built
  and can change course at every step. -->
<script lang="ts">
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import ImagePlus from "@lucide/svelte/icons/image-plus";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Sparkles from "@lucide/svelte/icons/sparkles";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import ActionBar from "../ActionBar.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import Iris from "../Iris.svelte";
  import SetupPanel from "../SetupPanel.svelte";
  import { api, outputToUpload, outputUrl, uploadUrl } from "../../lib/api";
  import { app, fail, followJob, modelById, modelsOf, submit, toast } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Job, Model } from "../../lib/types";

  let { studio }: { model: Model; studio: Studio } = $props();

  const pixal = $derived(modelById("pixal3d"));
  const trellis = $derived(modelById("trellis2"));
  const viewMaker = $derived(modelById("object-views"));
  // Front and back views need both the View Maker and Pixal3D on this machine.
  const canMakeViews = $derived(!!viewMaker?.available && !!pixal?.available);
  const viewsReady = $derived(!!viewMaker?.ready && !!pixal?.ready);

  let source = $state<"picture" | "describe">("picture");
  let picture = $state<string | null>(null);

  // --- describe it: pictures made for the purpose -------------------------------
  let description = $state("");
  let candidates = $state<string[]>([]); // job ids of the pictures being made
  const imageModel = $derived(
    ["z-image-turbo", "qwen-image-fast", "flux-schnell", "qwen-image", "krea-2", "flux-krea", "flux-dev"]
      .map((id) => modelById(id))
      .find((m) => m?.ready) ?? modelsOf("image").find((m) => m.ready),
  );
  const candidateJobs = $derived(candidates.map((id) => app.jobs.find((j) => j.id === id)).filter((j): j is Job => !!j));

  function objectPrompt(text: string): string {
    const what = text.trim().replace(/[.\s]+$/, "");
    return (
      `Product photo of ${what}. The whole object is visible and centered with space around it, ` +
      "seen straight on from the front at eye level, plain light grey studio background, " +
      "soft even lighting, sharp focus."
    );
  }

  async function makePictures() {
    if (!imageModel || !description.trim()) return;
    const ids: string[] = [];
    for (let i = 0; i < 4; i++) {
      const job = await submit(imageModel, {
        prompt: objectPrompt(description),
        aspect: "1:1",
        seed: Math.floor(Math.random() * 2 ** 50),
      });
      if (job) ids.push(job.id);
    }
    candidates = ids;
  }

  let choosing = $state<string | null>(null);
  async function choose(job: Job) {
    choosing = job.id;
    try {
      picture = await outputToUpload(job);
    } catch (e) {
      fail(e);
    } finally {
      choosing = null;
    }
  }

  // --- views --------------------------------------------------------------------
  type Views = {
    state: "working" | "ready" | "angled" | "failed";
    message: string;
    progress: number | null;
    job?: Job;
    front?: number; // output index of each view in the views job
    back?: number;
    level?: number;
    matched?: boolean;
    madeFront?: boolean;
    uploads?: { front: string; back: string };
    error?: string;
  };
  let views = $state<Views | null>(null);
  let onlyPicture = $state(false);
  let run = 0; // the latest views request; older ones are ignored when they finish

  async function makeViews(image: string, level: "auto" | "best" | "keep") {
    const mine = ++run;
    const previous = views?.job;
    if (previous && (previous.status === "queued" || previous.status === "running")) {
      api(`/jobs/${previous.id}/cancel`, { body: {} }).catch(() => {});
    }
    views = { state: "working", message: "Waiting for the graphics card", progress: null };
    try {
      let job = await api<Job>("/jobs", {
        body: { model: "object-views", params: { image1: image, level, seed: Math.floor(Math.random() * 2 ** 50) } },
      });
      if (mine !== run) return;
      views.job = job;
      job = await followJob(job, (message, progress) => {
        if (mine === run && views) Object.assign(views, { message, progress });
      });
      if (mine !== run) return;
      const roles = Object.fromEntries(job.outputs.map((o, i) => [o.role, i]));
      if (roles.back === undefined) {
        views = { state: "angled", message: "", progress: null, job, level: roles.level };
        return;
      }
      const back = job.outputs[roles.back];
      views = {
        state: "ready",
        message: "",
        progress: null,
        job,
        front: roles.front,
        back: roles.back,
        matched: !!back.matched,
        madeFront: job.outputs[roles.front].source === "made",
      };
      const [front, backUpload] = await Promise.all([outputToUpload(job, roles.front), outputToUpload(job, roles.back)]);
      if (mine === run && views) views.uploads = { front, back: backUpload };
    } catch (e) {
      if (mine !== run) return;
      views = { state: "failed", message: "", progress: null, error: e instanceof Error ? e.message : String(e) };
    }
  }

  // A new picture: check it and draw its back, when this machine can.
  let lastPicture: string | null = null;
  $effect(() => {
    if (picture === lastPicture) return;
    lastPicture = picture;
    onlyPicture = false;
    if (!picture) {
      run++;
      views = null;
    } else if (viewsReady && !restored) {
      makeViews(picture, "auto");
    }
    restored = false;
  });

  function drawBackAgain() {
    if (!picture) return;
    makeViews(picture, views?.madeFront ? "best" : "keep");
  }

  async function useLevelVersion() {
    if (!views?.job || views.level === undefined) return;
    try {
      const up = await outputToUpload(views.job, views.level);
      await makeViews(up, "keep");
    } catch (e) {
      fail(e);
    }
  }

  // --- what builds it -------------------------------------------------------------
  const plan = $derived<"pixal3d" | "trellis2">(
    !onlyPicture && views?.state === "ready" && views.uploads ? "pixal3d" : "trellis2",
  );
  const buildModel = $derived(plan === "pixal3d" ? pixal : trellis);
  const waitingForViews = $derived(!onlyPicture && (views?.state === "working" || (views?.state === "ready" && !views.uploads)));

  let detail = $state<"standard" | "high">("standard");
  const FACES = [
    { n: 500000, label: "Full", tip: "Every detail of the surface. For printing and close-ups." },
    { n: 100000, label: "Lighter", tip: "A fifth of the triangles; the surface detail is kept in a normal map. For 3D programs." },
    { n: 20000, label: "Game-ready", tip: "Light enough for games, AR and the web; the detail lives in its normal map. Not for printing." },
  ];
  let faces = $state(500000);
  let textures = $state(true);
  let keepOpenings = $state(false);
  let sending = $state(false);

  const minutes = $derived.by(() => {
    const pace = buildModel?.options.model3d?.pace;
    if (!pace) return "";
    const s = pace[detail];
    return s < 90 ? "about a minute" : `about ${Math.round(s / 60)} minutes`;
  });

  // "Use these settings again" from the library.
  let restored = false;
  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      detail = r.params.detail ?? "standard";
      faces = r.params.faces ?? 500000;
      textures = r.params.textures ?? true;
      keepOpenings = r.params.openings === "keep";
      source = "picture";
      restored = true;
      picture = r.params.front ?? null;
      if (r.model === "pixal3d" && r.params.back) {
        views = { state: "ready", message: "", progress: null, matched: true, uploads: { front: r.params.front, back: r.params.back } };
      } else {
        views = null;
        onlyPicture = r.model === "trellis2";
      }
      app.reuse = null;
    }
  });

  async function build() {
    if (!buildModel || !picture || sending || waitingForViews) return;
    sending = true;
    const common = {
      detail,
      textures,
      openings: keepOpenings ? "keep" : "close",
      faces,
      seed: Math.floor(Math.random() * 2 ** 50),
    };
    const params =
      plan === "pixal3d" && views?.uploads
        ? { ...common, front: views.uploads.front, back: views.uploads.back }
        : { ...common, front: picture };
    const job = await submit(buildModel, params);
    if (job) toast(`Building with ${buildModel.name}. It takes ${minutes}.`, "ok");
    sending = false;
  }

  function onKey(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      if (buildModel?.ready) build();
    }
  }
</script>

<div class="form" onkeydown={onKey} role="presentation">
  <div class="chips source" aria-label="Start from">
    <button class="chip" aria-pressed={source === "picture"} onclick={() => (source = "picture")}>
      <ImagePlus size={15} /> From a picture
    </button>
    <button class="chip" aria-pressed={source === "describe"} onclick={() => (source = "describe")}>
      <Sparkles size={15} /> Describe it
    </button>
  </div>

  {#if source === "describe" && !picture}
    <div class="field">
      <label class="label" for="object">What should it be?</label>
      <textarea id="object" class="textarea" rows="3" bind:value={description} placeholder="a small ceramic owl figurine standing on a round base"></textarea>
      {#if imageModel}
        <button class="btn" onclick={makePictures} disabled={!description.trim()}>
          <Sparkles size={15} /> Make 4 pictures with {imageModel.name}
        </button>
        <p class="tip">Each picture shows the whole object straight on, which is what 3D works best from. Pick the one to build.</p>
      {:else}
        <p class="tip">Set up an image model in the <button class="linkish" onclick={() => (app.studio = "image")}>Image studio</button> first.</p>
      {/if}
    </div>
    {#if candidateJobs.length}
      <div class="candidates">
        {#each candidateJobs as job (job.id)}
          {#if job.status === "done" && job.outputs[0]}
            <button class="candidate" onclick={() => choose(job)} disabled={!!choosing} title="Build from this one">
              <img src={outputUrl(job)} alt="One of the versions made" />
              {#if choosing === job.id}<span class="busy"><Iris size={36} bars={30} active /></span>{/if}
            </button>
          {:else if job.status === "running" || job.status === "queued"}
            <div class="candidate waiting"><Iris size={36} bars={30} active={job.status === "running"} progress={job.progress} /></div>
          {:else}
            <div class="candidate waiting failed">Didn't work</div>
          {/if}
        {/each}
      </div>
    {/if}
  {:else}
    <div class="field">
      <span class="label">The object</span>
      <ImageDrop bind:value={picture} label="Add a picture of the object" tag="Your object" hint="One whole object on a plain background works best. PNG, JPEG or WebP, or paste one." />
    </div>
  {/if}

  {#if picture}
    <div class="field">
      <span class="label">What it builds from</span>
      {#if !canMakeViews}
        <div class="views one">
          <figure><img src={uploadUrl(picture)} alt="The object as you added it" /><figcaption>Your picture</figcaption></figure>
        </div>
      {:else if !viewsReady}
        <div class="views one">
          <figure><img src={uploadUrl(picture)} alt="The object as you added it" /><figcaption>Your picture</figcaption></figure>
        </div>
        <details class="more">
          <summary>Truer backs: let IrisEcho draw the back of your object</summary>
          <p>
            For a picture taken at eye level, IrisEcho can draw the object from behind, check that it fits, and build
            from both sides with Pixal3D. That needs the View Maker (it uses Qwen Edit's files) and Pixal3D.
          </p>
          {#if viewMaker && !viewMaker.ready}<SetupPanel model={viewMaker} />{/if}
          {#if pixal && !pixal.ready}<SetupPanel model={pixal} />{/if}
        </details>
      {:else if views?.state === "working"}
        <div class="views">
          <figure><img src={uploadUrl(picture)} alt="The object as you added it" /><figcaption>Front</figcaption></figure>
          <figure class="pending">
            <Iris size={52} bars={36} active progress={views.progress} />
            <figcaption>Back</figcaption>
          </figure>
        </div>
        <p class="status">{views.message}</p>
      {:else if views?.state === "ready" && views.job}
        <div class="views" class:dim={onlyPicture}>
          <figure class="cut">
            <img src={outputUrl(views.job, views.front)} alt="The front" />
            <figcaption>Front{views.madeFront ? " · made at eye level" : ""}</figcaption>
          </figure>
          <figure class="cut">
            <img src={outputUrl(views.job, views.back)} alt="The back" />
            <figcaption>Back · made by IrisEcho</figcaption>
          </figure>
        </div>
        {#if views.matched}
          <p class="status ok"><CircleCheck size={15} /> The back's outline mirrors the front's: they fit together.</p>
        {:else}
          <p class="status warn"><TriangleAlert size={15} /> This back may not show the other side. Draw it again, or build from your picture alone.</p>
        {/if}
        <div class="row">
          <button class="btn sm ghost" onclick={drawBackAgain}><RefreshCw size={14} /> Draw the back again</button>
        </div>
      {:else if views?.state === "ready"}
        <!-- restored from the library: the views are uploads -->
        <div class="views" class:dim={onlyPicture}>
          <figure class="cut"><img src={uploadUrl(views.uploads!.front)} alt="The front" /><figcaption>Front</figcaption></figure>
          <figure class="cut"><img src={uploadUrl(views.uploads!.back)} alt="The back" /><figcaption>Back</figcaption></figure>
        </div>
      {:else if views?.state === "angled" && views.job}
        <div class="views">
          <figure><img src={uploadUrl(picture)} alt="The object as you added it" /><figcaption>Your picture</figcaption></figure>
          <figure class="dim">
            <img src={outputUrl(views.job, views.level)} alt="The object seen at eye level" />
            <figcaption>Eye-level version</figcaption>
          </figure>
        </div>
        <p class="status">
          Taken from above or at an angle, so {trellis?.name ?? "TRELLIS.2"} builds from your picture alone and stands it
          upright. For a truer back, use the eye-level version instead; its details may differ from your picture.
        </p>
        <div class="row">
          <button class="btn sm" onclick={useLevelVersion}>Use the eye-level version and add a back</button>
        </div>
      {:else if views?.state === "failed"}
        <div class="views one">
          <figure><img src={uploadUrl(picture)} alt="The object as you added it" /><figcaption>Your picture</figcaption></figure>
        </div>
        <p class="status warn"><TriangleAlert size={15} /> The back view could not be made ({views.error}). It will build from your picture alone.</p>
        <div class="row"><button class="btn sm ghost" onclick={() => picture && makeViews(picture, "auto")}><RefreshCw size={14} /> Try again</button></div>
      {/if}

      {#if views?.state === "ready"}
        <label class="toggle">
          <button class="switch" role="switch" aria-checked={onlyPicture} aria-label="Build from my picture alone" onclick={() => (onlyPicture = !onlyPicture)}></button>
          <span>
            <strong>Build from my picture alone</strong>
            <small>Keeps every detail of your picture with {trellis?.name ?? "TRELLIS.2"}; the back is guessed.</small>
          </span>
        </label>
      {/if}
    </div>

    <p class="plan">
      {#if plan === "pixal3d"}
        <strong>{pixal?.name}</strong> builds from the front and the back.
      {:else}
        <strong>{trellis?.name}</strong> builds from your picture and stands it upright.
      {/if}
    </p>

    <div class="field">
      <span class="label">Detail</span>
      <div class="chips">
        <button class="chip" aria-pressed={detail === "standard"} onclick={() => (detail = "standard")}>Standard</button>
        <button class="chip" aria-pressed={detail === "high"} onclick={() => (detail = "high")}>High</button>
      </div>
      <p class="tip">{detail === "high" ? "Finer surfaces; about a minute longer. If it does not fit in graphics memory, IrisEcho steps down by itself." : "Clean surfaces for printing and 3D programs."}</p>
    </div>

    <div class="field">
      <span class="label">Triangles</span>
      <div class="chips">
        {#each FACES as f (f.n)}
          <button class="chip" aria-pressed={faces === f.n} onclick={() => (faces = f.n)}>{f.label}</button>
        {/each}
      </div>
      <p class="tip">{FACES.find((f) => f.n === faces)?.tip}</p>
    </div>

    <label class="toggle">
      <button class="switch" role="switch" aria-checked={textures} aria-label="Colours and textures" onclick={() => (textures = !textures)}></button>
      <span>
        <strong>Colours and textures</strong>
        <small>{textures ? "Painted like the picture." : "Shape only, a little faster. Fine for a one-colour print."}</small>
      </span>
    </label>

    <label class="toggle">
      <button class="switch" role="switch" aria-checked={keepOpenings} aria-label="Keep openings" onclick={() => (keepOpenings = !keepOpenings)}></button>
      <span>
        <strong>Keep openings</strong>
        <small>For cups, vases and bowls: leave the top open. Otherwise small holes and openings are closed so it prints solid.</small>
      </span>
    </label>
  {/if}

  {#if buildModel}
    <ActionBar
      model={buildModel}
      label="Build 3D model"
      hint={!picture ? "Add a picture" : waitingForViews ? "Making the views" : minutes}
      disabled={!picture || sending || waitingForViews}
      onsubmit={build}
    />
  {/if}
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 16px;
    flex: 1;
  }
  .source .chip {
    flex: 1;
    justify-content: center;
    gap: 6px;
  }
  .tip {
    font-size: 12px;
    color: var(--text-4);
  }
  .linkish {
    border: 0;
    padding: 0;
    background: none;
    color: var(--accent-ink);
    font: inherit;
    cursor: pointer;
    text-decoration: underline;
  }
  .candidates {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .candidate {
    position: relative;
    aspect-ratio: 1;
    border-radius: var(--r-2);
    overflow: hidden;
    border: 1px solid var(--line-2);
    background: var(--surface-2);
    padding: 0;
    cursor: pointer;
  }
  .candidate:hover:not(:disabled) {
    border-color: var(--accent);
  }
  .candidate img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .candidate.waiting {
    display: grid;
    place-items: center;
    cursor: default;
  }
  .candidate.failed {
    color: var(--text-3);
    font-size: 12px;
  }
  .busy {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    background: rgb(14 11 22 / 0.55);
  }
  .views {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .views.one {
    grid-template-columns: 1fr;
  }
  .views.dim {
    opacity: 0.5;
  }
  figure {
    position: relative;
    margin: 0;
    aspect-ratio: 1;
    border-radius: var(--r-2);
    overflow: hidden;
    border: 1px solid var(--line-2);
    background: var(--surface-2);
  }
  .views.one figure {
    aspect-ratio: 4 / 3;
  }
  figure.cut {
    /* the views are cut out: show their transparency */
    background:
      conic-gradient(var(--surface-3) 25%, var(--surface-2) 0 50%, var(--surface-3) 0 75%, var(--surface-2) 0) 0 0 / 16px
      16px;
  }
  figure.pending {
    display: grid;
    place-items: center;
  }
  figure.dim img {
    opacity: 0.75;
  }
  figure img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  figcaption {
    position: absolute;
    left: 6px;
    bottom: 6px;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 99px;
    background: rgb(14 11 22 / 0.7);
    color: #fff;
  }
  .status {
    font-size: 12.5px;
    color: var(--text-3);
    display: flex;
    gap: 6px;
    align-items: flex-start;
    line-height: 1.4;
  }
  .status :global(svg) {
    flex: none;
    margin-top: 1px;
  }
  .status.ok {
    color: var(--teal-ink);
  }
  .status.warn {
    color: var(--amber-ink);
  }
  .row {
    display: flex;
    gap: 8px;
  }
  .more {
    font-size: 12.5px;
    color: var(--text-2);
    display: grid;
    gap: 8px;
  }
  .more summary {
    cursor: pointer;
    color: var(--accent-ink);
  }
  .more p {
    color: var(--text-3);
    margin: 6px 0;
  }
  .plan {
    font-size: 13px;
    color: var(--text-2);
  }
  .toggle {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }
  .toggle span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .toggle strong {
    font-weight: 560;
    font-size: 13.5px;
  }
  .toggle small {
    color: var(--text-3);
    font-size: 12.5px;
  }
</style>
