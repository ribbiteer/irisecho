<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import BoxSelect from "../BoxSelect.svelte";
  import { uploadUrl } from "../../lib/api";
  import PromptTools from "../PromptTools.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import PictureTray from "../PictureTray.svelte";
  import SeedField from "../SeedField.svelte";
  import { applyReuse, controlParams, ctrlEnter, nextSeed, submitEach } from "../../lib/controls";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  // What the model takes (registry settings.edit): pictures in all, and marked areas.
  const limits = $derived(model.options?.edit ?? { pictures: 1, areas: 0 });
  const multi = $derived(limits.pictures > 1);
  // Several areas are told apart by colour: the model sees a circle of that colour round each.
  const AREA_COLOURS = [
    { name: "Red", css: "#ff3b30" },
    { name: "Blue", css: "#1f6bff" },
    { name: "Green", css: "#1fb954" },
  ];
  const coloured = $derived(limits.areas > 1);
  let image1 = $state<string | null>(null);
  // Picture 2 onward, in order; a model that takes fewer keeps the rest unused.
  let extras = $state<(string | null)[]>([]);
  const sent = $derived(multi ? extras.slice(0, limits.pictures - 1) : []);
  let prompt = $state("");
  let count = $state(1);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  let sending = $state(false);
  // Advanced: change only some areas of Picture 1; the rest stays exactly as it is.
  let regionOn = $state(false);
  let boxes = $state<number[][]>([]);
  const kept = $derived(boxes.slice(0, Math.max(limits.areas, 0)));
  const region = $derived(limits.areas > 0 && !!app.settings?.advanced && regionOn && !!image1 && kept.length > 0);
  // One area travels as a single box (as before), several as a list.
  const box = $derived(region ? (kept.length === 1 ? kept[0] : kept) : null);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      image1 = r.params.image1 ?? null;
      const more: string[] = [];
      for (let i = 2; i <= 10; i++) if (r.params[`image${i}`]) more.push(r.params[`image${i}`]);
      extras = more;
      const b = r.params.box;
      boxes = Array.isArray(b) && b.length ? (Array.isArray(b[0]) ? b : [b]) : [];
      regionOn = boxes.length > 0;
      applyReuse(model, r, (s) => {
        fixedSeed = s != null;
        if (s != null) seed = s;
      });
      app.reuse = null;
    }
  });

  // Removing Picture n moves the later ones up a number; the prompt follows them.
  function renumber(removed: number) {
    prompt = prompt.replace(/\b(picture|image)(\s*)(\d{1,2})\b/gi, (m, word, gap, n) =>
      Number(n) > removed ? `${word}${gap}${Number(n) - 1}` : m,
    );
  }

  async function go() {
    if (!prompt.trim() || !image1 || sending) return;
    sending = true;
    const pictures: Record<string, string> = {};
    sent.forEach((id, i) => {
      if (id) pictures[`image${i + 2}`] = id;
    });
    const first = image1;
    try {
      await submitEach(Array.from({ length: count }, (_, i) => i), (i) =>
        submit(model, {
          prompt: prompt.trim(),
          image1: first,
          ...pictures,
          seed: nextSeed(fixedSeed, seed, i),
          ...(box ? { box } : {}),
          ...controlParams(model, { image1: first, image2: sent[0] ?? null, box }),
        }),
      );
    } finally {
      sending = false;
    }
  }

  const onKey = ctrlEnter(() => {
    if (model.ready) go();
  });
</script>

<div class="form" onkeydown={onKey} role="presentation">
  <div class="field">
    <span class="label">Picture to change</span>
    <ImageDrop bind:value={image1} label={multi ? "Picture 1" : "Add a picture"} hint="Choose a PNG, JPEG or WebP, or paste one." />
  </div>

  {#if multi}
    <div class="field">
      <span class="label">
        More pictures to draw from <span class="opt">optional · up to {limits.pictures - 1}</span>
      </span>
      <PictureTray bind:pictures={extras} max={limits.pictures - 1} onremove={renumber} />
      {#if extras.length > limits.pictures - 1}
        <p class="tip">{model.name} uses Pictures 1 to {limits.pictures}; the rest stay here for a model that takes more.</p>
      {:else if extras.length >= 3}
        <p class="tip">Name each one by its number, as "Picture 4". Removing one moves the later ones up, and the prompt follows.</p>
      {/if}
    </div>
  {/if}

  {#if limits.areas > 0 && app.settings?.advanced && image1}
    <div class="field">
      <span class="label">
        {coloured ? "Change only some areas" : "Change only an area"}
        <button
          class="switch"
          role="switch"
          aria-checked={regionOn}
          aria-label={coloured ? "Change only some areas" : "Change only an area"}
          onclick={() => (regionOn = !regionOn)}
        ></button>
      </span>
      {#if regionOn}
        <BoxSelect src={uploadUrl(image1)} bind:boxes max={limits.areas} colours={coloured ? AREA_COLOURS : []} />
        <p class="tip">
          {#if !kept.length}
            Drag a rectangle over the area to change{coloured ? `; up to ${limits.areas}, each circled in its own colour` : ""}.
          {:else if kept.length > 1}
            Say what happens in each circle by its colour: "the mug in the red circle", "the sky in the blue circle".
            Everything outside the boxes stays exactly as it is.
          {:else}
            Only the marked area changes; everything outside it stays exactly as it is.
          {/if}
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
        ? limits.pictures > 3
          ? "The woman from Picture 1 wears the jacket from Picture 2, the boots from Picture 3 and the hat from Picture 4"
          : "The woman from Picture 2 sits at the table in Picture 1, holding the teapot from Picture 3"
        : "Replace the white mug with a glass of orange juice; keep her hands and the table unchanged"}
    ></textarea>
    <PromptTools {model} bind:value={prompt} images={[image1, ...sent]} />
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

  <AdvancedControls {model} context={{ image1, image2: sent[0] ?? null, box }} />

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
