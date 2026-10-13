<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import PromptTools from "../PromptTools.svelte";
  import SeedField from "../SeedField.svelte";
  import { controlParams, nextSeed, reuseCommon, settingsFromPicture } from "../../lib/controls";
  import { app, fail, submit, toast } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const ASPECTS: [string, number, number][] = [
    ["1:1", 1, 1],
    ["4:3", 4, 3],
    ["3:4", 3, 4],
    ["16:9", 16, 9],
    ["9:16", 9, 16],
    ["3:2", 3, 2],
    ["2:3", 2, 3],
  ];

  let prompt = $state("");
  let aspect = $state("1:1");
  let count = $state(1);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  let sending = $state(false);
  // Advanced: start from a picture (image to image) and how much of it may change.
  let image1 = $state<string | null>(null);
  let denoise = $state(0.2);
  const advanced = $derived(!!app.settings?.advanced);
  // Z-Image with a guide picture: the picture is the point of the model, so it is always shown.
  const guided = $derived(model.id === "z-image-control");
  let guide = $state<string | null>(null);
  let guideType = $state("canny");
  const GUIDE_TYPES: [string, string, string][] = [
    ["canny", "Outlines", "Follows the edges of your picture: its shapes and lines."],
    ["depth", "Depth", "Follows how near or far things are in your picture."],
    ["pose", "Pose", "Follows the people's poses: body, hands and face. Best with people in view."],
    ["as_is", "Already a map", "Use this if your picture already is an edge, depth or pose map."],
  ];

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      aspect = r.params.aspect ?? "1:1";
      const { seed: s } = reuseCommon(model, r);
      if (s != null) {
        seed = s;
        fixedSeed = true;
      }
      image1 = advanced && !guided ? (r.params.image1 ?? null) : null;
      if (guided) {
        guide = r.params.guide ?? null;
        guideType = r.params.guide_type ?? "canny";
      }
      if (r.params.denoise != null) denoise = r.params.denoise;
      app.reuse = null;
    }
  });

  async function readSettings(e: Event & { currentTarget: HTMLInputElement }) {
    const file = e.currentTarget.files?.[0];
    e.currentTarget.value = "";
    if (!file) return;
    try {
      toast(await settingsFromPicture(file, studio.kind), "ok");
    } catch (err) {
      fail(err);
    }
  }

  async function go() {
    if (!prompt.trim() || sending || (guided && !guide)) return;
    sending = true;
    const from = guided ? { guide, guide_type: guideType } : advanced && image1 ? { image1, denoise } : {};
    for (let i = 0; i < count; i++) {
      await submit(model, {
        prompt: prompt.trim(),
        aspect,
        seed: nextSeed(fixedSeed, seed, i),
        ...from,
        ...controlParams(model, from),
      });
    }
    sending = false;
  }

  function onKey(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      if (model.ready) go();
    }
  }
</script>

<div class="form" onkeydown={onKey} role="presentation">
  <div class="field">
    <label class="label" for="prompt">Describe the picture</label>
    <textarea
      id="prompt"
      class="textarea"
      rows="6"
      bind:value={prompt}
      placeholder="A lighthouse on a black basalt cliff at blue hour, waves catching the last violet light"
    ></textarea>
    <PromptTools {model} bind:value={prompt} describe />
    <p class="tip">Describe what should be there. These models ignore "no" and "without".</p>
  </div>

  <div class="field">
    <span class="label">Shape</span>
    <div class="chips">
      {#each ASPECTS as [id, w, h] (id)}
        <button class="chip" aria-pressed={aspect === id} onclick={() => (aspect = id)}>
          <svg width="14" height="14" viewBox="-8 -8 16 16" aria-hidden="true">
            <rect
              x={-(7 * w) / Math.max(w, h)}
              y={-(7 * h) / Math.max(w, h)}
              width={(14 * w) / Math.max(w, h)}
              height={(14 * h) / Math.max(w, h)}
              rx="2"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
            />
          </svg>
          {id}
        </button>
      {/each}
    </div>
  </div>

  <div class="row">
    <div class="field">
      <span class="label">How many</span>
      <div class="chips">
        {#each [1, 2, 3, 4] as n (n)}
          <button class="chip num" aria-pressed={count === n} onclick={() => (count = n)}>{n}</button>
        {/each}
      </div>
    </div>
    <SeedField bind:fixed={fixedSeed} bind:seed />
  </div>

  {#if guided}
    <div class="field">
      <span class="label">Guide picture</span>
      <ImageDrop bind:value={guide} label="Add a guide picture" hint="The new image follows its outlines, depth or pose. It is cropped to the shape above." />
      <div class="chips" aria-label="What the guide gives">
        {#each GUIDE_TYPES as [id, label] (id)}
          <button class="chip" aria-pressed={guideType === id} onclick={() => (guideType = id)}>{label}</button>
        {/each}
      </div>
      <p class="tip">{GUIDE_TYPES.find(([id]) => id === guideType)?.[2]}</p>
    </div>
  {/if}

  {#if advanced && !guided}
    <div class="field">
      <span class="label">Start from a picture <span class="opt">optional</span></span>
      <ImageDrop bind:value={image1} label="Add a picture" hint="The new image follows its layout and colours." />
      {#if image1}
        <div class="denoise">
          <span>How much to change</span>
          <input type="range" min="0.05" max="1" step="0.05" bind:value={denoise} aria-label="How much to change" />
          <span class="mono">{Math.round(denoise * 100)}%</span>
        </div>
        <p class="tip">Low keeps the picture and redraws detail; high keeps only its rough layout. The shape follows the picture.</p>
      {/if}
      <label class="from-file">
        <input type="file" accept="image/png" onchange={readSettings} hidden />
        <span>Read the settings from a picture…</span>
      </label>
    </div>
  {/if}

  <AdvancedControls {model} context={{ image1 }} />

  <ActionBar
    {model}
    label={count > 1 ? `Create ${count} images` : "Create image"}
    hint={guided && !guide ? "Add a guide picture" : undefined}
    disabled={!prompt.trim() || sending || (guided && !guide)}
    onsubmit={go}
  />
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 18px;
    flex: 1;
  }
  .tip {
    font-size: 12px;
    color: var(--text-4);
  }
  .row {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 22px;
  }
  .num {
    width: 34px;
    justify-content: center;
    padding: 0;
  }
  .opt {
    font-weight: 400;
    color: var(--text-4);
    margin-left: 4px;
  }
  .from-file {
    align-self: flex-start;
    font-size: 12px;
    color: var(--accent-ink);
    cursor: pointer;
  }
  .denoise {
    display: grid;
    grid-template-columns: auto 1fr 44px;
    gap: 10px;
    align-items: center;
    font-size: 13px;
    color: var(--text-2);
  }
</style>
