<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Compare: the same job again with the same seed, one setting varied, so the difference
     between the versions is that setting alone. The versions arrive side by side in the feed. -->
<script lang="ts">
  import Columns from "@lucide/svelte/icons/columns-3";
  import { showValue } from "../lib/controls";
  import { submit, toast } from "../lib/state.svelte";
  import type { Control, ControlValue, Job, Model } from "../lib/types";

  let { job, model }: { job: Job; model: Model } = $props();

  const comparable = $derived(
    (model.controls ?? []).filter((c) => c.type === "int" || c.type === "float" || c.type === "choice" || c.type === "bool"),
  );
  let name = $state("");
  const control = $derived(comparable.find((c) => c.name === name));
  let picked = $state<ControlValue[]>([]);
  const choices = $derived<{ value: ControlValue; label: string }[]>(
    control?.type === "bool"
      ? [
          { value: false, label: "off" },
          { value: true, label: "on" },
        ]
      : (control?.options ?? []),
  );
  let numbers = $state("");
  let sending = $state(false);
  const seed = $derived(job.params.seed ?? job.outputs.find((o) => o.seed != null)?.seed);

  function suggest(c: Control): string {
    const now = Number(job.params[c.name] ?? c.default);
    const lo = c.min ?? now;
    const hi = c.max ?? now;
    const pts = [lo, now, hi, (lo + now) / 2, (now + hi) / 2]
      .map((v) => (c.type === "int" ? Math.round(v) : Math.round(v * 100) / 100))
      .sort((a, b) => a - b);
    return [...new Set(pts)].join(", ");
  }

  function choose(n: string) {
    name = n;
    const c = comparable.find((x) => x.name === n);
    picked = c?.type === "choice" ? (c.options ?? []).slice(0, 3).map((o) => o.value) : c?.type === "bool" ? [false, true] : [];
    numbers = c && (c.type === "int" || c.type === "float") ? suggest(c) : "";
  }

  function values(): ControlValue[] {
    if (!control) return [];
    if (control.type === "int" || control.type === "float") {
      return [
        ...new Set(
          numbers
            .split(/[,\s]+/)
            .map(Number)
            .filter((v) => Number.isFinite(v) && (control.min == null || v >= control.min) && (control.max == null || v <= control.max))
            .map((v) => (control.type === "int" ? Math.round(v) : v)),
        ),
      ].slice(0, 6);
    }
    return picked.slice(0, 6);
  }

  async function run() {
    const list = values();
    if (!control || list.length < 2 || seed == null) return;
    sending = true;
    for (const v of list) {
      await submit(model, { ...job.params, seed, [control.name]: v });
    }
    sending = false;
    toast(`Queued ${list.length} versions with ${control.label} at ${list.map((v) => showValue(control, v)).join(", ")}.`, "ok");
  }

  function toggle(v: ControlValue) {
    picked = picked.includes(v) ? picked.filter((x) => x !== v) : [...picked, v];
  }
</script>

{#if comparable.length && seed != null}
  <details class="compare">
    <summary><Columns size={14} /> Compare one setting</summary>
    <p class="note">Makes this again with the same seed for each value, so only that setting differs.</p>
    <select class="input" value={name} onchange={(e) => choose(e.currentTarget.value)} aria-label="Setting to compare">
      <option value="">Choose a setting</option>
      {#each comparable as c (c.name)}<option value={c.name}>{c.label}</option>{/each}
    </select>
    {#if control}
      {#if control.type === "int" || control.type === "float"}
        <input class="input mono" bind:value={numbers} aria-label="Values, separated by commas" />
        <p class="note">Values between {control.min} and {control.max}, separated by commas (up to 6).</p>
      {:else}
        <div class="chips">
          {#each choices as o (String(o.value))}
            <button class="chip" aria-pressed={picked.includes(o.value)} onclick={() => toggle(o.value)}>{o.label}</button>
          {/each}
        </div>
      {/if}
      <button class="btn sm" onclick={run} disabled={sending || values().length < 2}>
        Make {values().length} versions
      </button>
    {/if}
  </details>
{/if}

<style>
  .compare {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .compare summary {
    font-size: 13px;
    color: var(--text-3);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .compare[open] summary {
    margin-bottom: 8px;
  }
  .compare > :not(summary) {
    margin-bottom: 8px;
  }
  .input {
    height: 34px;
    width: 100%;
  }
  .note {
    font-size: 12px;
    color: var(--text-4);
    margin: 0 0 8px;
  }
</style>
