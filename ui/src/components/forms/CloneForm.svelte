<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import FileAudio from "@lucide/svelte/icons/file-audio";
  import X from "@lucide/svelte/icons/x";
  import ActionBar from "../ActionBar.svelte";
  import Waveform from "../Waveform.svelte";
  import { api, uploadUrl } from "../../lib/api";
  import { app, fail, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const TAGS = ["[laugh]", "[chuckle]", "[sigh]", "[gasp]", "[groan]", "[cough]", "[sniff]", "[shush]", "[clear throat]"];
  const turbo = $derived(model.id === "chatterbox-turbo");

  let ref = $state<{ id: string; name: string } | null>(null);
  let uploading = $state(false);
  let dragging = $state(false);
  let text = $state("");
  let perLine = $state(false);
  let takes = $state(2);
  let exaggeration = $state(0.5);
  let cfg = $state(0.5);
  let clean = $state(true);
  let consent = $state(false);
  let sending = $state(false);
  let textarea: HTMLTextAreaElement;

  $effect(() => {
    const r = app.reuse;
    if (r && r.kind === studio.kind) {
      text = r.params.text ?? "";
      if (r.params.ref) ref = { id: r.params.ref, name: "Voice sample from that take" };
      takes = r.params.takes ?? 2;
      exaggeration = r.params.exaggeration ?? 0.5;
      cfg = r.params.cfg ?? 0.5;
      clean = r.params.clean ?? true;
      app.reuse = null;
    }
  });

  async function upload(file: File) {
    if (!file.type.startsWith("audio/")) {
      fail("Use a WAV, MP3, FLAC or OGG recording.");
      return;
    }
    uploading = true;
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api<{ id: string }>("/uploads", { form });
      ref = { id: r.id, name: file.name };
    } catch (e) {
      fail(e);
    } finally {
      uploading = false;
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    const file = e.dataTransfer?.files?.[0];
    if (file) upload(file);
  }

  function insertTag(tag: string) {
    const start = textarea?.selectionStart ?? text.length;
    const end = textarea?.selectionEnd ?? text.length;
    text = `${text.slice(0, start)}${tag} ${text.slice(end)}`;
    queueMicrotask(() => {
      textarea?.focus();
      const pos = start + tag.length + 1;
      textarea?.setSelectionRange(pos, pos);
    });
  }

  const lines = $derived(
    perLine
      ? text
          .split("\n")
          .map((l) => l.trim())
          .filter((l) => l && !l.startsWith("#"))
      : text.trim()
        ? [text.trim()]
        : [],
  );

  async function go() {
    if (!lines.length || !ref || !consent || sending) return;
    sending = true;
    for (const line of lines) {
      await submit(model, { text: line, ref: ref.id, consent, takes, exaggeration, cfg, clean });
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
    <span class="label">Voice sample</span>
    {#if ref}
      <div class="sample">
        <div class="sample-head">
          <FileAudio size={16} />
          <span class="fname">{ref.name}</span>
          <button class="btn sm icon ghost" title="Use a different sample" onclick={() => (ref = null)}><X size={14} /></button>
        </div>
        <Waveform src={uploadUrl(ref.id)} bars={64} height={32} />
      </div>
    {:else}
      <label
        class="drop"
        class:dragging
        ondragover={(e) => {
          e.preventDefault();
          dragging = true;
        }}
        ondragleave={() => (dragging = false)}
        ondrop={onDrop}
      >
        <input
          type="file"
          accept="audio/*"
          class="sr-only"
          onchange={(e) => {
            const f = (e.currentTarget as HTMLInputElement).files?.[0];
            if (f) upload(f);
          }}
        />
        <FileAudio size={22} />
        <strong>{uploading ? "Adding…" : "Drop a recording, or choose a file"}</strong>
        <small>5 to 15 seconds of one clear voice. The first 6 seconds shape the delivery most.</small>
      </label>
    {/if}
  </div>

  <div class="field">
    <label class="label" for="line">
      What should it say?
      <span class="inline">
        One file per line
        <button class="switch" role="switch" aria-checked={perLine} aria-label="One file per line" onclick={() => (perLine = !perLine)}></button>
      </span>
    </label>
    <textarea
      id="line"
      bind:this={textarea}
      class="textarea"
      rows="5"
      bind:value={text}
      placeholder={turbo ? "Correct! [chuckle] Ten points to you." : "Question one. Which planet has the shortest day?"}
    ></textarea>
    {#if turbo}
      <div class="tags" aria-label="Reactions">
        {#each TAGS as tag (tag)}
          <button class="chip small" onclick={() => insertTag(tag)}>{tag}</button>
        {/each}
      </div>
    {/if}
  </div>

  {#if !turbo}
    <div class="dials">
      <div class="field">
        <span class="label">Intensity <span class="mono value">{exaggeration.toFixed(2)}</span></span>
        <input type="range" min="0.25" max="1.5" step="0.05" bind:value={exaggeration} aria-label="Intensity" />
      </div>
      <div class="field">
        <span class="label">Pacing guide <span class="mono value">{cfg.toFixed(2)}</span></span>
        <input type="range" min="0" max="1" step="0.05" bind:value={cfg} aria-label="Pacing guide" />
      </div>
      <p class="tip">Livelier reads: raise intensity and lower the pacing guide.</p>
    </div>
  {/if}

  <div class="field">
    <span class="label">Takes of each line</span>
    <div class="chips">
      {#each [1, 2, 3, 4] as n (n)}
        <button class="chip num" aria-pressed={takes === n} onclick={() => (takes = n)}>{n}</button>
      {/each}
    </div>
    <p class="tip">The liveliest take is listed first.</p>
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={clean} aria-label="Clean up" onclick={() => (clean = !clean)}></button>
    <span>
      <strong>Trim silence and match loudness</strong>
      <small>Every line lands at the same level.</small>
    </span>
  </label>

  <label class="consent">
    <input type="checkbox" bind:checked={consent} />
    <span>
      I have permission to use this voice. <small>Clips carry Resemble AI's inaudible watermark, which marks them as generated.</small>
    </span>
  </label>

  <ActionBar
    {model}
    label={lines.length > 1 ? `Speak ${lines.length} lines` : "Speak"}
    hint={!ref ? "Add a voice sample" : !consent ? "Confirm permission" : ""}
    disabled={!lines.length || !ref || !consent || sending}
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
  .drop {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 20px 16px;
    border-radius: var(--r-3);
    border: 1.5px dashed var(--line-3);
    background: var(--surface-2);
    text-align: center;
    color: var(--accent-ink);
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .drop strong {
    color: var(--text);
    font-weight: 580;
  }
  .drop small {
    color: var(--text-3);
    font-size: 12px;
    max-width: 280px;
  }
  .drop:hover,
  .drop.dragging {
    border-color: var(--accent);
    background: color-mix(in oklab, var(--accent) 10%, var(--surface-2));
  }
  .sample {
    padding: 10px 12px 12px;
    border-radius: var(--r-3);
    background: var(--surface-2);
    border: 1px solid var(--line-2);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .sample-head {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--accent-ink);
  }
  .fname {
    flex: 1;
    color: var(--text);
    font-size: 13px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .inline {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  .chip.small {
    height: 26px;
    padding: 0 9px;
    font-size: 12px;
    font-family: var(--mono);
  }
  .dials {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .value {
    color: var(--text-2);
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
  .consent {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 10px 12px;
    border-radius: var(--r-2);
    border: 1px solid var(--line-2);
    cursor: pointer;
    font-size: 13px;
  }
  .consent input {
    margin-top: 3px;
    accent-color: var(--teal);
  }
  .consent small {
    display: block;
    color: var(--text-3);
    font-size: 12px;
    margin-top: 2px;
  }
</style>
