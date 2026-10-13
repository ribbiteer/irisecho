<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Add-ons (LoRAs) for one job: files from the models/loras folder (or a linked folder)
     that fit the model's family, each with a strength. -->
<script lang="ts">
  import Plus from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";
  import { api } from "../lib/api";
  import type { Control, LoraEntry } from "../lib/types";

  interface LoraFile {
    file: string;
    family: string;
    size: number;
    triggers: string[];
    pair_hint?: "high" | "low" | "";
  }

  let { control, value, onchange }: { control: Control; value: LoraEntry[]; onchange: (v: LoraEntry[]) => void } =
    $props();

  let files = $state<LoraFile[] | null>(null);
  let error = $state("");

  $effect(() => {
    const family = control.family ?? "";
    api<LoraFile[]>(`/loras?family=${encodeURIComponent(family)}`)
      .then((list) => (files = list))
      .catch((e) => (error = String(e?.message ?? e)));
  });

  const fitting = $derived((files ?? []).filter((f) => f.family === control.family));
  const unknown = $derived((files ?? []).filter((f) => f.family === "unknown"));
  const highs = $derived(fitting.filter((f) => f.pair_hint !== "low"));
  const lows = $derived(fitting.filter((f) => f.pair_hint !== "high"));

  function update(i: number, patch: Partial<LoraEntry>) {
    onchange(value.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  }
  function remove(i: number) {
    onchange(value.filter((_, j) => j !== i));
  }
  function add() {
    if (control.pair) {
      const high = highs[0]?.file;
      onchange([...value, { high, low: partner(high), strength: 1 }]);
    } else {
      onchange([...value, { file: fitting[0]?.file ?? unknown[0]?.file, strength: 1 }]);
    }
  }
  /** The low-noise file that goes with a high-noise one, by name. */
  function partner(high?: string): string | undefined {
    if (!high) return lows[0]?.file;
    const guess = high.replace(/high[_-]?noise/i, (m) => m.replace(/high/i, "low")).replace(/(^|[_-])high(?=[_.-])/i, "$1low");
    return lows.find((f) => f.file === guess)?.file ?? lows.find((f) => f.file !== high)?.file;
  }
  const triggers = (name?: string) => (files ?? []).find((f) => f.file === name)?.triggers ?? [];
  const short = (name: string) => name.replace(/\.safetensors$/i, "");
</script>

<div class="loras">
  {#if error}
    <p class="note">Could not list add-ons: {error}</p>
  {:else if files && !fitting.length && !unknown.length}
    <p class="note">
      No add-ons for this model yet. Put LoRA files (<code>.safetensors</code>) in the <code>loras</code> folder inside your
      models folder, or in a linked models folder, and they appear here.
    </p>
  {/if}

  {#each value as e, i (i)}
    <div class="entry">
      {#if control.pair}
        <label class="pick">
          <span>High noise</span>
          <select class="input" value={e.high ?? ""} onchange={(ev) => update(i, { high: ev.currentTarget.value || undefined, low: e.low ?? partner(ev.currentTarget.value) })}>
            <option value="">None</option>
            {#each highs as f (f.file)}<option value={f.file}>{short(f.file)}</option>{/each}
          </select>
        </label>
        <label class="pick">
          <span>Low noise</span>
          <select class="input" value={e.low ?? ""} onchange={(ev) => update(i, { low: ev.currentTarget.value || undefined })}>
            <option value="">None</option>
            {#each lows as f (f.file)}<option value={f.file}>{short(f.file)}</option>{/each}
          </select>
        </label>
      {:else}
        <select class="input" value={e.file ?? ""} onchange={(ev) => update(i, { file: ev.currentTarget.value })} aria-label="Add-on file">
          {#each fitting as f (f.file)}<option value={f.file}>{short(f.file)}</option>{/each}
          {#if unknown.length}
            <optgroup label="Not recognised (may not fit)">
              {#each unknown as f (f.file)}<option value={f.file}>{short(f.file)}</option>{/each}
            </optgroup>
          {/if}
        </select>
      {/if}
      <div class="strength">
        <input type="range" min="-2" max="2" step="0.05" value={e.strength} oninput={(ev) => update(i, { strength: Number(ev.currentTarget.value) })} aria-label="Strength" />
        <span class="mono">{e.strength.toFixed(2)}</span>
        <button class="btn sm icon ghost" title="Remove" onclick={() => remove(i)}><X size={14} /></button>
      </div>
      {#if triggers(e.file ?? e.high).length}
        <p class="note">Trigger words: <span class="mono">{triggers(e.file ?? e.high).slice(0, 6).join(", ")}</span></p>
      {/if}
    </div>
  {/each}

  {#if value.length < 3 && (fitting.length || unknown.length)}
    <button class="btn sm ghost add" onclick={add}><Plus size={14} /> Add-on</button>
  {/if}
  {#if value.length}
    <p class="note">Add-ons carry their own licences. Strength 1 is the maker's intent; 0.6–0.9 is often gentler.</p>
  {/if}
</div>

<style>
  .loras {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .entry {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    border: 1px solid var(--line);
    border-radius: var(--r-1);
  }
  .pick {
    display: grid;
    grid-template-columns: 78px 1fr;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--text-3);
  }
  select.input {
    height: 34px;
  }
  .strength {
    display: grid;
    grid-template-columns: 1fr 44px auto;
    gap: 8px;
    align-items: center;
    font-size: 12px;
  }
  .note {
    font-size: 12px;
    color: var(--text-3);
    margin: 0;
  }
  .add {
    align-self: flex-start;
  }
</style>
