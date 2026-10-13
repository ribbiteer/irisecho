<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import ImageDrop from "../ImageDrop.svelte";
  import SeedField from "../SeedField.svelte";
  import { controlParams, nextSeed, reuseCommon } from "../../lib/controls";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  let image1 = $state<string | null>(null);
  let scale = $state(2);
  let retain = $state(false);
  let sending = $state(false);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      image1 = r.params.image1 ?? null;
      scale = r.params.scale ?? 2;
      retain = !!r.params.retain;
      const { seed: s } = reuseCommon(model, r);
      fixedSeed = s != null;
      if (s != null) seed = s;
      app.reuse = null;
    }
  });

  async function go() {
    if (!image1 || sending) return;
    sending = true;
    await submit(model, {
      image1,
      scale,
      retain: retain ? true : false,
      seed: nextSeed(fixedSeed, seed, 0),
      ...controlParams(model, { image1 }),
    });
    sending = false;
  }
</script>

<div class="form">
  <div class="field">
    <span class="label">Picture</span>
    <ImageDrop bind:value={image1} label="Add a picture" hint="Photos, renders and screenshots all work. No prompt needed." />
  </div>

  <div class="field">
    <span class="label">Enlarge</span>
    <div class="chips">
      {#each [2, 3, 4] as n (n)}
        <button class="chip" aria-pressed={scale === n} onclick={() => (scale = n)}>{n}×</button>
      {/each}
    </div>
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={retain} aria-label="Keep the original size" onclick={() => (retain = !retain)}></button>
    <span>
      <strong>Keep the original size</strong>
      <small>Restores detail at {scale}× internally, then returns the picture at its original size. 4× shows the difference best.</small>
    </span>
  </label>

  <SeedField bind:fixed={fixedSeed} bind:seed />

  <AdvancedControls {model} context={{ image1 }} />

  <ActionBar {model} label={retain ? "Restore detail" : `Upscale ${scale}×`} hint={!image1 ? "Add a picture" : ""} disabled={!image1 || sending} onsubmit={go} />
</div>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 16px;
    flex: 1;
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
