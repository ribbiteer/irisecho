<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  First run: what this computer can do, and the quickest ways to start. -->
<script lang="ts">
  import AudioLines from "@lucide/svelte/icons/audio-lines";
  import FolderSearch from "@lucide/svelte/icons/folder-search";
  import ImageIcon from "@lucide/svelte/icons/image";
  import Iris from "./Iris.svelte";
  import { bytes } from "../lib/format";
  import { app, modelById } from "../lib/state.svelte";
  import type { Kind } from "../lib/types";

  const hw = $derived(app.system?.hardware);
  const gpu = $derived(hw?.gpus[0]);
  const verdict = $derived.by(() => {
    if (!hw) return "";
    if (hw.backend === "cuda") {
      const gb = Math.round((gpu?.vram_mb ?? 0) / 1024);
      return gb >= 8
        ? `Your ${gpu?.name.replace("NVIDIA GeForce ", "")} with ${gb} GB can run every studio.`
        : `Your ${gpu?.name.replace("NVIDIA GeForce ", "")} has ${gb} GB; voice and music will run well, large picture models may not.`;
    }
    if (hw.backend === "mps") return "On Apple Silicon, voice, music and Z-Image are available as a preview.";
    return "No supported graphics card was found, so only lighter voice models are available.";
  });

  const starts = $derived(
    [
      { id: "kokoro", kind: "voice" as Kind, icon: AudioLines, line: "Narration in about a second a line" },
      { id: "z-image-turbo", kind: "image" as Kind, icon: ImageIcon, line: "Photographs and illustration in seconds" },
    ]
      .map((s) => ({ ...s, model: modelById(s.id) }))
      .filter((s) => s.model?.available),
  );

  function go(kind: Kind, id: string) {
    app.chosen[kind] = id;
    app.studio = kind;
    app.view = "create";
  }
</script>

<div class="welcome">
  <Iris size={132} bars={60} active />
  <h1>Welcome to IrisEcho</h1>
  <p class="lead">Pictures, sound and 3D models, made on this computer. {verdict}</p>

  <div class="starts">
    {#each starts as s (s.id)}
      {@const Icon = s.icon}
      <button class="start" class:sound={s.kind === "voice"} onclick={() => go(s.kind, s.id)}>
        <span class="glyph"><Icon size={20} /></span>
        <span class="text">
          <strong>Start with {s.model?.name}</strong>
          <small>{s.line} · {bytes((s.model?.size ?? 0) - (s.model?.have ?? 0)) === "0 MB" ? "ready" : `${bytes((s.model?.size ?? 0) - (s.model?.have ?? 0))} download`}</small>
        </span>
      </button>
    {/each}
  </div>

  <button class="link" onclick={() => (app.view = "models")}>
    <FolderSearch size={16} />
    Already have models from ComfyUI or elsewhere? Use them without downloading again.
  </button>
</div>

<style>
  .welcome {
    height: 100%;
    max-width: 560px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    text-align: center;
    padding: 24px;
  }
  h1 {
    font-size: 34px;
    margin-top: 6px;
  }
  .lead {
    color: var(--text-2);
    font-size: 15px;
    max-width: 460px;
  }
  .starts {
    display: grid;
    gap: 10px;
    width: 100%;
    margin-top: 12px;
  }
  .start {
    --accent: var(--violet);
    --accent-ink: var(--violet-ink);
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 16px;
    border-radius: var(--r-3);
    border: 1px solid var(--line-2);
    background: var(--surface-1);
    text-align: left;
    cursor: pointer;
    transition:
      border-color 0.15s,
      transform 0.15s var(--ease);
  }
  .start.sound {
    --accent: var(--teal);
    --accent-ink: var(--teal-ink);
  }
  .start:hover {
    border-color: color-mix(in oklab, var(--accent) 60%, transparent);
    transform: translateY(-1px);
  }
  .glyph {
    width: 40px;
    height: 40px;
    border-radius: 12px;
    display: grid;
    place-items: center;
    background: color-mix(in oklab, var(--accent) 22%, transparent);
    color: var(--accent-ink);
    flex: none;
  }
  .text {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .text small {
    color: var(--text-3);
    font-size: 12.5px;
  }
  .link {
    margin-top: 8px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border: 0;
    background: none;
    color: var(--amber-ink);
    cursor: pointer;
    font-size: 13px;
  }
  .link:hover {
    text-decoration: underline;
  }
</style>
