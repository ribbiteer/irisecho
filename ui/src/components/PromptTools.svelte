<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  Sits under a prompt box: rewrite the draft for the chosen model, or describe a
  picture as a prompt. The rewrite is shown first; nothing changes until it is accepted. -->
<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import ImageUp from "@lucide/svelte/icons/image-up";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import Undo from "@lucide/svelte/icons/undo-2";
  import WandSparkles from "@lucide/svelte/icons/wand-sparkles";
  import X from "@lucide/svelte/icons/x";
  import ImageDrop from "./ImageDrop.svelte";
  import { app, fail, promptWriter, toast, writePrompt } from "../lib/state.svelte";
  import type { Model } from "../lib/types";

  let {
    model,
    value = $bindable(""),
    image = null,
    images = null,
    describe = false,
  }: {
    model: Model; // the model the prompt is for
    value: string;
    image?: string | null; // a picture the rewrite may look at
    images?: (string | null)[] | null; // several, in order (Picture 1, 2, 3); overrides `image`
    describe?: boolean; // offer "from a picture"
  } = $props();

  const writer = $derived(promptWriter());
  let busy = $state(false);
  let status = $state("");
  let suggestion = $state("");
  let previous = $state<string | null>(null);
  let picking = $state(false);
  let picture = $state<string | null>(null);

  // A rewrite is written for one family of models (its prompt style). Remember which, so a
  // later change of model can say so: FLUX and Krea share a style, Z-Image and Qwen do not.
  type Written = { name: string; style: string };
  const written = (m: Model): Written => ({ name: m.name, style: m.prompt_style ?? m.id });
  let suggestionFor = $state<Written | null>(null); // the model the waiting suggestion is for
  let writtenFor = $state<Written | null>(null); // the model the accepted rewrite is for
  let dismissed = $state(false);
  const staleFor = $derived(
    writtenFor && value.trim() && !dismissed && written(model).style !== writtenFor.style ? writtenFor : null,
  );
  const pendingFor = $derived(
    suggestion && suggestionFor && written(model).style !== suggestionFor.style ? suggestionFor : null,
  );
  // Emptying the box starts over; picking another model brings a dismissed warning back.
  $effect(() => {
    if (!value.trim()) writtenFor = null;
  });
  $effect(() => {
    void model.id;
    dismissed = false;
  });

  const lookAt = $derived(images ? images.filter((i): i is string => !!i) : image ? [image] : []);
  let card = $state<HTMLElement>();

  // The composer scrolls; bring a new suggestion into view (its buttons come first).
  $effect(() => {
    if (suggestion) card?.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  async function ensure(): Promise<boolean> {
    if (writer?.ready) return true;
    if (app.remote) {
      toast("The prompt writer is not set up yet. Set it up on the computer.", "info", 6000);
      return false;
    }
    toast("Set up the prompt writer first. It is under Helpers on the Models page.", "info", 6000);
    app.view = "models";
    return false;
  }

  async function run(mode: "improve" | "describe" | "improve_image", images: string[]) {
    if (busy || !(await ensure())) return;
    busy = true;
    suggestion = "";
    const target = written(model);
    try {
      suggestion = await writePrompt({ mode, target: model.id, text: value.trim(), images }, (m) => (status = m));
      suggestionFor = target;
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
      status = "";
    }
  }

  const improve = () => run(lookAt.length && value.trim() ? "improve_image" : "improve", lookAt);

  async function fromPicture() {
    if (!picture) return;
    await run("describe", [picture]);
    if (suggestion) picking = false;
  }

  function accept() {
    previous = value;
    value = suggestion;
    writtenFor = suggestionFor ?? written(model);
    dismissed = false;
    suggestion = "";
  }

  function undo() {
    if (previous === null) return;
    value = previous;
    previous = null;
    writtenFor = null;
  }
</script>

{#if writer?.available}
  <div class="tools">
    <div class="buttons">
      <button class="btn sm" onclick={improve} disabled={busy || !value.trim()} title="Rewrite this the way {model.name} likes it">
        <WandSparkles size={14} /> Improve
      </button>
      {#if describe}
        <button class="btn sm ghost" onclick={() => (picking = !picking)} disabled={busy} aria-pressed={picking}>
          <ImageUp size={14} /> From a picture
        </button>
      {/if}
      {#if previous !== null && !suggestion}
        <button class="btn sm ghost" onclick={undo}><Undo size={14} /> Undo</button>
      {/if}
      {#if busy}<span class="status" role="status">{status || "Writing"}…</span>{/if}
    </div>

    {#if staleFor}
      <div class="notice" role="note">
        <TriangleAlert size={16} />
        <div class="body">
          <p>
            This prompt was improved for {staleFor.name}. {model.name} prefers a different style.
          </p>
          <div class="buttons">
            <button class="btn sm" onclick={improve} disabled={busy || !value.trim()}>
              <WandSparkles size={14} /> Improve for {model.name}
            </button>
            <button class="btn sm ghost" onclick={() => (dismissed = true)}>Dismiss</button>
          </div>
        </div>
      </div>
    {/if}

    {#if picking}
      <div class="pick">
        <ImageDrop bind:value={picture} label="Picture to describe" compact />
        <button class="btn sm primary" onclick={fromPicture} disabled={!picture || busy}>Describe it</button>
      </div>
    {/if}

    {#if suggestion}
      <div class="suggestion" bind:this={card} role="region" aria-label="Suggested prompt">
        {#if pendingFor}
          <div class="notice" role="note">
            <TriangleAlert size={16} />
            <p>
              This suggestion was written for {pendingFor.name}, not {model.name}. Press Improve again to write one for
              {model.name}.
            </p>
          </div>
        {/if}
        <div class="buttons">
          <button class="btn sm primary" onclick={accept}><Check size={14} /> Use this</button>
          <button class="btn sm ghost" onclick={() => (suggestion = "")}><X size={14} /> Keep mine</button>
        </div>
        <p>{suggestion}</p>
      </div>
    {/if}
  </div>
{/if}

<style>
  .tools {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .buttons {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }
  .status {
    color: var(--text-3);
    font-size: 12.5px;
  }
  .pick {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: end;
  }
  .suggestion {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px 14px;
    border-radius: var(--r-2);
    border: 1px solid var(--line-2);
    background: var(--surface-2);
  }
  .suggestion > p {
    font-size: 13.5px;
    line-height: 1.5;
    color: var(--text);
  }
  .notice {
    display: flex;
    gap: 10px;
    padding: 10px 12px;
    border-radius: var(--r-2);
    border: 1px solid color-mix(in oklab, var(--amber) 35%, transparent);
    background: color-mix(in oklab, var(--amber) 8%, transparent);
    color: var(--amber-ink);
    font-size: 12.5px;
    line-height: 1.5;
  }
  .notice :global(svg) {
    flex: none;
    margin-top: 2px;
  }
  .notice .body {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
</style>
