<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import ActionBar from "../ActionBar.svelte";
  import AdvancedControls from "../AdvancedControls.svelte";
  import SeedField from "../SeedField.svelte";
  import SourceAudio from "../SourceAudio.svelte";
  import { applyReuse, controlParams, ctrlEnter, MAX_BATCH, seedParam, splitLines, submitEach } from "../../lib/controls";
  import { takeChoices } from "../../lib/limits";
  import { app, submit } from "../../lib/state.svelte";
  import type { Studio } from "../../lib/studios";
  import type { Model } from "../../lib/types";

  let { model, studio }: { model: Model; studio: Studio } = $props();

  const TAGS = ["[laugh]", "[chuckle]", "[sigh]", "[gasp]", "[groan]", "[cough]", "[sniff]", "[shush]", "[clear throat]"];
  const turbo = $derived(model.id === "chatterbox-turbo");

  let ref = $state<{ id: string; name: string } | null>(null);
  let text = $state("");
  let perLine = $state(false);
  let takes = $state(2);
  let exaggeration = $state(0.5);
  let cfg = $state(0.5);
  let clean = $state(true);
  let consent = $state(false);
  let sending = $state(false);
  let fixedSeed = $state(false);
  let seed = $state(Math.floor(Math.random() * 1e9));
  const advanced = $derived(!!app.settings?.advanced);
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
      applyReuse(model, r, (s) => {
        fixedSeed = s != null;
        if (s != null) seed = s;
      });
      app.reuse = null;
    }
  });

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

  const lines = $derived(splitLines(text, perLine));

  async function go() {
    if (!lines.length || lines.length > MAX_BATCH || !ref || !consent || sending) return;
    sending = true;
    const sample = ref.id;
    try {
      await submitEach(lines, (line) =>
        submit(model, {
          text: line,
          ref: sample,
          consent,
          takes,
          exaggeration,
          cfg,
          clean,
          ...seedParam(fixedSeed, seed),
          ...controlParams(model),
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
    <span class="label">Voice sample</span>
    <SourceAudio
      bind:value={ref}
      prompt="Drop a recording, or choose a file"
      hint="10 to 15 seconds of one clear voice, no music or other speakers. Phone voice memos work."
      seconds={30}
      stereo={false}
    />
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
        <input type="range" min="0.25" max={advanced ? 2 : 1.5} step="0.05" bind:value={exaggeration} aria-label="Intensity" />
      </div>
      <div class="field">
        <span class="label">Pacing guide <span class="mono value">{cfg.toFixed(2)}</span></span>
        <input type="range" min="0" max="1" step="0.05" bind:value={cfg} aria-label="Pacing guide" />
      </div>
      <p class="tip">Livelier reads: raise intensity and lower the pacing guide.</p>
    </div>
  {/if}

  <div class="row">
    <div class="field">
      <span class="label">Takes of each line</span>
      <div class="chips">
        {#each takeChoices(model) as n (n)}
          <button class="chip num" aria-pressed={takes === n} onclick={() => (takes = n)}>{n}</button>
        {/each}
      </div>
      <p class="tip">The liveliest take is listed first. Take 2 uses the seed plus one, and so on.</p>
    </div>
    <SeedField bind:fixed={fixedSeed} bind:seed />
  </div>

  <label class="toggle">
    <button class="switch" role="switch" aria-checked={clean} aria-label="Clean up" onclick={() => (clean = !clean)}></button>
    <span>
      <strong>Trim silence and match loudness</strong>
      <small>Every line lands at the same level.</small>
    </span>
  </label>

  <AdvancedControls {model} />

  <label class="consent">
    <input type="checkbox" bind:checked={consent} />
    <span>
      I have permission to use this voice. <small>Clips carry Resemble AI's inaudible watermark, which marks them as generated.</small>
    </span>
  </label>

  <ActionBar
    {model}
    label={lines.length > 1 ? `Speak ${lines.length} lines` : "Speak"}
    hint={!ref ? "Add a voice sample" : !consent ? "Confirm permission" : lines.length > MAX_BATCH ? `At most ${MAX_BATCH} lines at a time` : ""}
    disabled={!lines.length || lines.length > MAX_BATCH || !ref || !consent || sending}
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
