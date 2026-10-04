<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import PromptTools from "../PromptTools.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const fromPicture = $derived(model.prompt_style === "wan-i2v");
  const ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4"];
  // 720p peaks at about 11.7 GB (graphs.VIDEO_720P_MIN_MB).
  const fits720 = $derived((app.system?.hardware.vram_mb ?? 0) >= 12000);

  let start = $state<string | null>(null);
  let end = $state<string | null>(null);
  let prompt = $state("");
  let aspect = $state("16:9");
  let seconds = $state(3);
  let size = $state<"standard" | "large" | "720p">("standard");
  let smooth = $state(false);
  let sending = $state(false);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      start = r.params.start ?? null;
      end = r.params.end ?? null;
      aspect = r.params.aspect ?? "16:9";
      seconds = r.params.seconds ?? 3;
      size = r.params.size ?? "standard";
      smooth = !!r.params.smooth;
      app.reuse = null;
    }
  });

  const needsFrame = $derived(fromPicture && !start && !end);
  const estimate = $derived.by(() => {
    // Seconds of work per second of video on a 12 GB card, smooth on.
    const base = { standard: 45, large: 60, "720p": 100 }[size];
    const min = Math.round((base * seconds) / 60 + 0.4);
    return min <= 1 ? "about a minute" : `about ${min} minutes`;
  });

  async function go() {
    if (!prompt.trim() || needsFrame || sending) return;
    sending = true;
    await submit(model, {
      prompt: prompt.trim(),
      start: fromPicture ? start : null,
      end: fromPicture ? end : null,
      aspect,
      seconds,
      size,
      smooth,
      seed: Math.floor(Math.random() * 2 ** 50),
    });
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
  {#if fromPicture}
    <div class="field">
      <span class="label">Frames <span class="opt">first, last, or both</span></span>
      <div class="pair">
        <ImageDrop bind:value={start} label="First frame" compact />
        <ImageDrop bind:value={end} label="Last frame" compact />
      </div>
      <p class="tip">Clips look best with a first frame. Pictures are cropped to fit the shape below.</p>
    </div>
  {/if}

  <div class="field">
    <label class="label" for="motion">{fromPicture ? "What happens?" : "Describe the clip"}</label>
    <textarea
      id="motion"
      class="textarea"
      rows="4"
      bind:value={prompt}
      placeholder={fromPicture ? "The camera slowly pushes in as the waves crash against the rocks" : "A paper boat drifting down a rain-soaked street at night, reflections of neon signs"}
    ></textarea>
    <PromptTools {model} bind:value={prompt} image={fromPicture ? (start ?? end) : null} />
    <p class="tip">{fromPicture ? "Describe motion and camera, not what is already in the frame." : "Say what should be there; negative words are ignored."}</p>
  </div>

  <div class="field">
    <span class="label">Shape</span>
    <div class="chips">
      {#each ASPECTS as a (a)}
        <button class="chip" aria-pressed={aspect === a} onclick={() => (aspect = a)}>{a}</button>
      {/each}
    </div>
  </div>

  <div class="field">
    <span class="label">Length <span class="mono value">{seconds} s</span></span>
    <input type="range" min="2" max="5" step="1" bind:value={seconds} aria-label="Length in seconds" />
  </div>

  <div class="field">
    <span class="label">Quality</span>
    <div class="chips">
      <button class="chip" aria-pressed={size === "standard"} onclick={() => (size = "standard")}>Standard</button>
      <button class="chip" aria-pressed={size === "large"} onclick={() => (size = "large")}>Large</button>
      {#if fits720 || size === "720p"}
        <button class="chip" aria-pressed={size === "720p"} onclick={() => (size = "720p")}>720p</button>
      {/if}
    </div>
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={smooth} aria-label="Smooth motion" onclick={() => (smooth = !smooth)}></button>
    <span>
      <strong>Smooth motion</strong>
      <small>Doubles the frame rate to 32 fps. Same length.</small>
    </span>
  </label>

  <ActionBar
    {model}
    label="Make video"
    hint={needsFrame ? "Add a frame" : estimate}
    disabled={!prompt.trim() || needsFrame || sending}
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
  .value {
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
