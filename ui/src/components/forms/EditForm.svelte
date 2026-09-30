<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import PromptTools from "../PromptTools.svelte";
  import ImageDrop from "../ImageDrop.svelte";
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
  let sending = $state(false);

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      prompt = r.params.prompt ?? "";
      image1 = r.params.image1 ?? null;
      image2 = r.params.image2 ?? null;
      image3 = r.params.image3 ?? null;
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
        seed: Math.floor(Math.random() * 2 ** 50),
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
    <ImageDrop bind:value={image1} label={multi ? "Picture 1" : "Add a picture"} hint="Drop, paste or choose a PNG, JPEG or WebP." />
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

  <div class="field">
    <label class="label" for="change">What should change?</label>
    <textarea
      id="change"
      class="textarea"
      rows="4"
      bind:value={prompt}
      placeholder={multi ? "Put the teapot from picture 2 on the table in picture 1, lit like the rest of the room" : "Make it a rainy night, keep everything else the same"}
    ></textarea>
    <PromptTools {model} bind:value={prompt} image={image1} />
    <p class="tip">Name only what should change; the rest is kept.</p>
  </div>

  <div class="field">
    <span class="label">Versions</span>
    <div class="chips">
      {#each [1, 2, 3, 4] as n (n)}
        <button class="chip num" aria-pressed={count === n} onclick={() => (count = n)}>{n}</button>
      {/each}
    </div>
  </div>

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
  .num {
    width: 34px;
    justify-content: center;
    padding: 0;
  }
</style>
