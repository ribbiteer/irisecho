<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ModelPicker from "./ModelPicker.svelte";
  import CloneForm from "./forms/CloneForm.svelte";
  import EditForm from "./forms/EditForm.svelte";
  import ImageForm from "./forms/ImageForm.svelte";
  import Model3dForm from "./forms/Model3dForm.svelte";
  import MusicForm from "./forms/MusicForm.svelte";
  import SfxForm from "./forms/SfxForm.svelte";
  import UpscaleForm from "./forms/UpscaleForm.svelte";
  import VideoForm from "./forms/VideoForm.svelte";
  import VoiceForm from "./forms/VoiceForm.svelte";
  import { app, chosenModel, modelsOf } from "../lib/state.svelte";
  import type { Studio } from "../lib/studios";

  let { studio }: { studio: Studio } = $props();

  const models = $derived(modelsOf(studio.kind));
  const model = $derived(chosenModel(studio.kind));
</script>

<section class="composer" aria-label={`${studio.label} settings`}>
  <header>
    <h2>{studio.label}</h2>
    <p>{studio.hint}</p>
  </header>

  <div class="body">
    {#if !model}
      <div class="unavailable">
        <strong>Not available on this computer yet.</strong>
        <p>
          {models.length
            ? `The ${studio.label.toLowerCase()} models need ${app.system?.hardware.backend === "mps" ? "an NVIDIA graphics card for now" : "a supported graphics card"}.`
            : "No models for this studio are installed."}
        </p>
      </div>
    {:else}
      {#if studio.kind !== "model3d"}
        <!-- 3D picks its model from the pictures it gets (Model3dForm) -->
        <ModelPicker kind={studio.kind} {models} selected={model} />
      {/if}
      {#if studio.kind === "model3d"}
        <Model3dForm {model} {studio} />
      {:else if studio.kind === "image"}
        <ImageForm {model} {studio} />
      {:else if studio.kind === "edit"}
        <EditForm {model} {studio} />
      {:else if studio.kind === "upscale"}
        <UpscaleForm {model} {studio} />
      {:else if studio.kind === "video"}
        <VideoForm {model} {studio} />
      {:else if studio.kind === "voice"}
        <VoiceForm {model} {studio} />
      {:else if studio.kind === "music"}
        <MusicForm {model} {studio} />
      {:else if studio.kind === "clone"}
        <CloneForm {model} {studio} />
      {:else if studio.kind === "sfx"}
        <SfxForm {model} {studio} />
      {/if}
    {/if}
  </div>
</section>

<style>
  .composer {
    display: flex;
    flex-direction: column;
    min-height: 0;
    border-right: 1px solid var(--line);
    background: var(--surface-1);
  }
  header {
    padding: 22px 22px 6px;
  }
  h2 {
    font-size: 26px;
  }
  header p {
    color: var(--text-3);
    margin-top: 2px;
  }
  .body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding: 14px 22px 0;
    overflow-y: auto;
  }
  @media (max-width: 820px) {
    .composer {
      flex: none;
      border-right: 0;
      border-bottom: 1px solid var(--line);
    }
    header {
      padding: 16px 16px 4px;
    }
    .body {
      overflow: visible;
      padding: 12px 16px 0;
    }
  }
  .unavailable {
    padding: 18px;
    border-radius: var(--r-3);
    border: 1px dashed var(--line-2);
    color: var(--text-2);
    display: grid;
    gap: 6px;
  }
</style>
