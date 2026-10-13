<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- "More controls": a model's advanced settings, shown when Settings › Show advanced controls is on.
     Only values that differ from the recommended ones are kept, so a job sends nothing extra
     unless the person changed something. -->
<script lang="ts">
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import Save from "@lucide/svelte/icons/save";
  import Trash from "@lucide/svelte/icons/trash-2";
  import LoraPicker from "./LoraPicker.svelte";
  import { api } from "../lib/api";
  import { app, fail, toast } from "../lib/state.svelte";
  import { applies, isChanged, same } from "../lib/controls";
  import type { Control, ControlValue, LoraEntry, Model } from "../lib/types";

  let { model, context = {} }: { model: Model; context?: Record<string, unknown> } = $props();

  let open = $state(false);
  let help = $state<string | null>(null);

  const shown = $derived(
    app.settings?.advanced ? (model.controls ?? []).filter((c) => applies(c, context)) : [],
  );
  const basic = $derived(shown.filter((c) => c.tier !== "expert"));
  const expert = $derived(shown.filter((c) => c.tier === "expert"));
  const values = $derived(app.advanced[model.id] ?? {});
  const changed = $derived(shown.filter((c) => isChanged(c, values[c.name])).length);

  // Presets: named sets of these values, saved in presets.json in the data folder.
  const saved = $derived(app.presets?.[model.id] ?? {});
  const current = $derived(Object.entries(saved).find(([, v]) => same(v, values))?.[0] ?? "");
  let naming = $state(false);
  let presetName = $state("");

  $effect(() => {
    if (open && app.presets === null) {
      app.presets = {};
      api<typeof app.presets>("/presets")
        .then((p) => (app.presets = p))
        .catch(() => {});
    }
  });

  function usePreset(name: string) {
    if (!name) return reset();
    app.advanced[model.id] = { ...(saved[name] ?? {}) };
  }
  async function savePreset() {
    const name = presetName.trim();
    if (!name) return;
    try {
      app.presets = await api("/presets", { method: "PUT", body: { model: model.id, name, values } });
      naming = false;
      presetName = "";
      toast(`Saved "${name}" for ${model.name}.`, "ok");
    } catch (e) {
      fail(e);
    }
  }
  async function deletePreset() {
    if (!current) return;
    try {
      const q = `model=${encodeURIComponent(model.id)}&name=${encodeURIComponent(current)}`;
      app.presets = await api(`/presets?${q}`, { method: "DELETE" });
    } catch (e) {
      fail(e);
    }
  }

  $effect(() => {
    if (app.advancedOpen && shown.length) {
      open = true;
      app.advancedOpen = false;
    }
  });

  function value(c: Control): ControlValue | null {
    const v = values[c.name];
    return v === undefined ? c.default : v;
  }

  function set(c: Control, v: ControlValue) {
    const next = { ...(app.advanced[model.id] ?? {}) };
    if (isChanged(c, v)) next[c.name] = v;
    else delete next[c.name];
    app.advanced[model.id] = next;
  }

  function reset(c?: Control) {
    if (!c) {
      app.advanced[model.id] = {};
      return;
    }
    const next = { ...(app.advanced[model.id] ?? {}) };
    delete next[c.name];
    app.advanced[model.id] = next;
  }

  function setNumber(c: Control, raw: string) {
    let n = Number(raw);
    if (!Number.isFinite(n)) return;
    if (c.min != null) n = Math.max(c.min, n);
    if (c.max != null) n = Math.min(c.max, n);
    if (c.type === "int") n = Math.round(n);
    set(c, n);
  }

  const fmt = (c: Control, v: unknown) => {
    const opt = c.options?.find((o) => o.value === v);
    if (opt) return opt.label;
    if (typeof v === "boolean") return v ? "on" : "off";
    return `${v}${c.unit ? ` ${c.unit}` : ""}`;
  };
  const chipsFit = (c: Control) =>
    (c.options?.length ?? 0) <= 5 && (c.options ?? []).every((o) => o.label.length <= 14);
</script>

{#snippet row(c: Control)}
  {@const v = value(c)}
  {@const dirty = isChanged(c, values[c.name])}
  <div class="ctl" class:dirty>
    <div class="top">
      <span class="name">
        {#if dirty}<span class="dot" aria-label="changed"></span>{/if}
        {c.label}
        {#if c.help}
          <button
            class="q"
            aria-label={`About ${c.label}`}
            aria-expanded={help === c.name}
            onclick={() => (help = help === c.name ? null : c.name)}>?</button
          >
        {/if}
      </span>
      <span class="rec">
        {#if dirty && c.type !== "loras"}
          <button class="linkish" title="Back to recommended" onclick={() => reset(c)}>
            <RotateCcw size={12} /> {fmt(c, c.default)}
          </button>
        {:else if c.type !== "loras" && c.type !== "text"}
          recommended
        {/if}
      </span>
    </div>
    {#if help === c.name}<p class="help">{c.help}</p>{/if}

    {#if c.type === "int" || c.type === "float"}
      <div class="num">
        <input
          type="range"
          min={c.min}
          max={c.max}
          step={c.step ?? (c.type === "int" ? 1 : 0.1)}
          value={v as number}
          oninput={(e) => setNumber(c, e.currentTarget.value)}
          aria-label={c.label}
        />
        <input
          class="input mono"
          type="number"
          min={c.min}
          max={c.max}
          step={c.step ?? (c.type === "int" ? 1 : 0.1)}
          value={v as number}
          onchange={(e) => setNumber(c, e.currentTarget.value)}
          aria-label={`${c.label} value`}
        />
      </div>
    {:else if c.type === "choice"}
      {#if chipsFit(c)}
        <div class="chips">
          {#each c.options ?? [] as o (o.value)}
            <button class="chip" aria-pressed={v === o.value} onclick={() => set(c, o.value)}>{o.label}</button>
          {/each}
        </div>
      {:else}
        <select class="input" value={v} onchange={(e) => {
          const o = c.options?.find((x) => String(x.value) === e.currentTarget.value);
          if (o) set(c, o.value);
        }} aria-label={c.label}>
          {#each c.options ?? [] as o (o.value)}
            <option value={o.value}>{o.label}</option>
          {/each}
        </select>
      {/if}
    {:else if c.type === "bool"}
      <button class="switch" role="switch" aria-checked={!!v} aria-label={c.label} onclick={() => set(c, !v)}></button>
    {:else if c.type === "text"}
      <input class="input" value={(v as string) ?? ""} oninput={(e) => set(c, e.currentTarget.value)} aria-label={c.label} />
    {:else if c.type === "loras"}
      <LoraPicker control={c} value={(v as LoraEntry[]) ?? []} onchange={(list) => set(c, list)} />
    {/if}
  </div>
{/snippet}

{#if shown.length}
  <section class="more" class:open>
    <button class="head" aria-expanded={open} onclick={() => (open = !open)}>
      <span>More controls</span>
      <span class="state">
        {#if changed}<span class="count">{changed} changed</span>{/if}
        <ChevronDown size={16} />
      </span>
    </button>
    {#if open}
      <div class="body">
        <div class="presets">
          {#if naming}
            <input
              class="input"
              placeholder="Name this preset"
              bind:value={presetName}
              onkeydown={(e) => e.key === "Enter" && savePreset()}
              aria-label="Preset name"
            />
            <button class="btn sm" onclick={savePreset} disabled={!presetName.trim()}>Save</button>
            <button class="btn sm ghost" onclick={() => (naming = false)}>Cancel</button>
          {:else}
            <select class="input" value={current} onchange={(e) => usePreset(e.currentTarget.value)} aria-label="Preset">
              <option value="">{changed && !current ? "Your settings" : "Recommended"}</option>
              {#each Object.keys(saved) as name (name)}<option value={name}>{name}</option>{/each}
            </select>
            {#if changed && !current}
              <button class="btn sm ghost" onclick={() => (naming = true)}><Save size={14} /> Save as preset</button>
            {:else if current}
              <button class="btn sm icon ghost" title="Delete this preset" onclick={deletePreset}><Trash size={14} /></button>
            {/if}
          {/if}
        </div>
        {#each basic as c (c.name)}{@render row(c)}{/each}
        {#if expert.length}
          <details class="expert" open={expert.some((c) => isChanged(c, values[c.name]))}>
            <summary>Expert</summary>
            {#each expert as c (c.name)}{@render row(c)}{/each}
          </details>
        {/if}
        {#if changed}
          <button class="linkish all" onclick={() => reset()}><RotateCcw size={12} /> Back to recommended</button>
        {/if}
      </div>
    {/if}
  </section>
{/if}

<style>
  .more {
    border: 1px solid var(--line-2);
    border-radius: var(--r-2);
    background: var(--surface-1);
  }
  .head {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    background: none;
    border: 0;
    color: var(--text);
    font: inherit;
    font-size: 14px;
    cursor: pointer;
  }
  .head :global(svg) {
    transition: transform 0.2s var(--ease);
  }
  .open .head :global(svg) {
    transform: rotate(180deg);
  }
  .state {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .count {
    font-size: 12px;
    color: var(--amber-ink);
  }
  .body {
    padding: 2px 12px 12px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .presets {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .presets .input {
    flex: 1;
    min-width: 0;
    height: 32px;
  }
  .ctl {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-2);
  }
  .name {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--amber);
  }
  .q {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: 1px solid var(--line-3);
    background: none;
    color: var(--text-3);
    font-size: 11px;
    line-height: 1;
    cursor: pointer;
    padding: 0;
  }
  .q[aria-expanded="true"] {
    color: var(--text);
    border-color: var(--text-3);
  }
  .rec {
    font-size: 11px;
    color: var(--text-4);
  }
  .help {
    font-size: 12px;
    color: var(--text-3);
    margin: 0;
  }
  .num {
    display: grid;
    grid-template-columns: 1fr 78px;
    gap: 10px;
    align-items: center;
  }
  .num .input {
    height: 32px;
    padding: 0 8px;
  }
  select.input {
    height: 34px;
  }
  .linkish {
    background: none;
    border: 0;
    padding: 0;
    color: var(--accent-ink);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .all {
    align-self: flex-start;
  }
  .expert {
    border-top: 1px dashed var(--line-2);
    padding-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .expert summary {
    font-size: 13px;
    color: var(--text-3);
    cursor: pointer;
    margin-bottom: 4px;
  }
  .expert[open] summary {
    margin-bottom: 10px;
  }
</style>
