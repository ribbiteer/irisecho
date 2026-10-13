<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import BoxSelect from "../BoxSelect.svelte";
  import { uploadUrl } from "../../lib/api";
  import PromptTools from "../PromptTools.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import SeedField from "../SeedField.svelte";
  import { controlParams, nextSeed, reuseCommon } from "../../lib/controls";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const multi = $derived(model.id.startsWith("qwen-edit"));
  let image1 = $state<string | null>(null);
  let image2 = $state<string | null>(null);
  let image3 = $state<string | null>(null);
  let prompt = $state("");
  let count = $state(1);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  let sending = $state(false);
  // Advanced, Qwen Edit only: change only an area of Picture 1; the rest stays exactly as it is.
  let regionOn = $state(false);
  let box = $state<number[] | null>(null);
  const region = $derived(multi && !!app.settings?.advanced && regionOn && !!image1 && !!box);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      image1 = r.params.image1 ?? null;
      image2 = r.params.image2 ?? null;
      image3 = r.params.image3 ?? null;
      box = Array.isArray(r.params.box) ? r.params.box : null;
      regionOn = !!box;
      const { seed: s } = reuseCommon(model, r);
      if (s != null) {
        seed = s;
        fixedSeed = true;
      }
      app.reuse = null;
    }
  });

  async function go() {
    if (!prompt.trim() || !image1 || sending) return;
    sending = true;
    for (let i = 0; i < count; i++) {
      await submit(model, {
        prompt: prompt.trim(),
        image1,
        image2: multi ? image2 : null,
        image3: multi ? image3 : null,
        seed: nextSeed(fixedSeed, seed, i),
        ...(region ? { box } : {}),
        ...controlParams(model, { image1, box: region ? box : null }),
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
    <span class="label">Picture to change</span>
    <ImageDrop bind:value={image1} label={multi ? "Picture 1" : "Add a picture"} hint="Choose a PNG, JPEG or WebP, or paste one." />
  </div>

  {#if multi}
    <div class="field">
      <span class="label">More pictures to draw from <span class="opt">optional</span></span>
      <div class="pair">
        <ImageDrop bind:value={image2} label="Picture 2" compact />
        <ImageDrop bind:value={image3} label="Picture 3" compact />
      </div>
    </div>
  {/if}

  {#if multi && app.settings?.advanced && image1}
    <div class="field">
      <span class="label">
        Change only an area
        <button class="switch" role="switch" aria-checked={regionOn} aria-label="Change only an area" onclick={() => (regionOn = !regionOn)}
        ></button>
      </span>
      {#if regionOn}
        <BoxSelect src={uploadUrl(image1)} bind:box />
        <p class="tip">
          {box ? "Only the marked area changes; everything outside it stays exactly as it is." : "Drag a rectangle over the area to change."}
        </p>
      {/if}
    </div>
  {/if}

  <div class="field">
    <label class="label" for="change">What should change?</label>
    <textarea
      id="change"
      class="textarea"
      rows="4"
      bind:value={prompt}
      placeholder={multi
        ? "The woman from Picture 2 sits at the table in Picture 1, holding the teapot from Picture 3"
        : "Replace the white mug with a glass of orange juice; keep her hands and the table unchanged"}
    ></textarea>
    <PromptTools {model} bind:value={prompt} images={multi ? [image1, image2, image3] : [image1]} />
    <p class="tip">Name only what should change; the rest is kept.</p>
  </div>

  <div class="row">
    <div class="field">
      <span class="label">Versions</span>
      <div class="chips">
        {#each [1, 2, 3, 4] as n (n)}
          <button class="chip num" aria-pressed={count === n} onclick={() => (count = n)}>{n}</button>
        {/each}
      </div>
    </div>
    <SeedField bind:fixed={fixedSeed} bind:seed />
  </div>

  <AdvancedControls {model} context={{ image1 }} />

  <ActionBar
    {model}
    label={count > 1 ? `Make ${count} versions` : "Edit picture"}
    hint={!image1 ? "Add a picture" : ""}
    disabled={!prompt.trim() || !image1 || sending}
    onsubmit={go}
  />
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 16px;
    flex: 1;
  }
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .opt {
    font-weight: 500;
    color: var(--text-4);
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
</style>
