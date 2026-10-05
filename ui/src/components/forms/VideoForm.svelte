<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import PromptTools from "../PromptTools.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model, VideoOptions, VideoSize } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const NO_OPTIONS: VideoOptions = { frames: [], sizes: ["standard"], fps: 16, sound: false, pace: {} };
  const SIZE_LABELS: Record<VideoSize, string> = { standard: "Standard", large: "Large", "720p": "720p" };
  const video = $derived(model.options.video ?? NO_OPTIONS);
  const takesStart = $derived(video.frames.includes("start"));
  const takesEnd = $derived(video.frames.includes("end"));
  const fromPicture = $derived(takesStart || takesEnd);
  const ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4"];
  // 720p peaks at about 11.7 GB (graphs.VIDEO_720P_MIN_MB).
  const fits720 = $derived((app.system?.hardware.vram_mb ?? 0) >= 12000);
  const sizes = $derived(video.sizes.filter((s) => s !== "720p" || fits720 || size === "720p"));

  let start = $state<string | null>(null);
  let end = $state<string | null>(null);
  let prompt = $state("");
  let aspect = $state("16:9");
  let seconds = $state(3);
  let size = $state<VideoSize>("standard");
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

  // A size this model does not make falls back to standard.
  $effect(() => {
    if (!video.sizes.includes(size)) size = "standard";
  });

  const needsFrame = $derived(!!video.needs_frame && !(takesStart && start) && !(takesEnd && end));
  const estimate = $derived.by(() => {
    const base = video.pace[size] ?? 60;
    const min = Math.round((base * seconds) / 60 + 0.4);
    return min <= 1 ? "about a minute" : `about ${min} minutes`;
  });

  async function go() {
    if (!prompt.trim() || needsFrame || sending) return;
    sending = true;
    await submit(model, {
      prompt: prompt.trim(),
      start: takesStart ? start : null,
      end: takesEnd ? end : null,
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
      <span class="label">
        Frames
        <span class="opt">
          {takesStart && takesEnd ? `first, last, or both${video.needs_frame ? "" : " (optional)"}` : "first frame"}
        </span>
      </span>
      <div class="pair">
        {#if takesStart}<ImageDrop bind:value={start} label="First frame" compact />{/if}
        {#if takesEnd}<ImageDrop bind:value={end} label="Last frame" compact />{/if}
      </div>
      <p class="tip">Clips look best with a first frame. Pictures are cropped to fit the shape below.</p>
    </div>
  {/if}

  <div class="field">
    <label class="label" for="motion">{fromPicture && (start || end) ? "What happens?" : "Describe the clip"}</label>
    <textarea
      id="motion"
      class="textarea"
      rows="4"
      bind:value={prompt}
      placeholder={fromPicture ? "The camera slowly pushes in as the waves crash against the rocks" : "A paper boat drifting down a rain-soaked street at night, reflections of neon signs"}
    ></textarea>
    <PromptTools {model} bind:value={prompt} image={fromPicture ? (start ?? end) : null} />
    <p class="tip">
      {fromPicture && (start || end) ? "Describe motion and camera, not what is already in the frame." : "Say what should be there; negative words are ignored."}
      {#if video.sound}This model makes sound too: say what is heard.{/if}
    </p>
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
      {#each sizes as s (s)}
        <button class="chip" aria-pressed={size === s} onclick={() => (size = s)}>{SIZE_LABELS[s]}</button>
      {/each}
    </div>
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={smooth} aria-label="Smooth motion" onclick={() => (smooth = !smooth)}></button>
    <span>
      <strong>Smooth motion</strong>
      <small>Doubles the frame rate to {video.fps * 2} fps. Same length.</small>
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
