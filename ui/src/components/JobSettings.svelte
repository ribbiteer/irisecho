<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Every setting a result was made with, and the same job as API JSON or a command. -->
<script lang="ts">
  import Braces from "@lucide/svelte/icons/braces";
  import Terminal from "@lucide/svelte/icons/terminal";
  import CompareSetting from "./CompareSetting.svelte";
  import { isChanged, showValue } from "../lib/controls";
  import { app, toast } from "../lib/state.svelte";
  import type { Job, Model } from "../lib/types";

  let { job, model }: { job: Job; model: Model | undefined } = $props();

  const controls = $derived(model?.controls ?? []);
  const changed = $derived(controls.filter((c) => isChanged(c, job.params[c.name])));
  const recommended = $derived(
    controls.filter((c) => !isChanged(c, job.params[c.name]) && c.type !== "loras" && c.type !== "text"),
  );
  const seed = $derived(job.params.seed ?? job.outputs.find((o) => o.seed != null)?.seed);

  function body(): { model: string; params: Record<string, unknown> } {
    const params: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(job.params)) {
      if (v !== null && v !== undefined && v !== "") params[k] = v;
    }
    if (seed != null) params.seed = seed;
    return { model: job.model, params };
  }

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${what} copied.`, "ok");
    } catch {
      toast("Copying is not available here.", "error");
    }
  }
  const json = () => copy(JSON.stringify(body(), null, 2), "The job as API JSON");
  // The body goes in through stdin; single quotes are escaped the way this computer's shell
  // expects (PowerShell on Windows, a POSIX shell elsewhere).
  const command = () => {
    const cli = app.guide.cli || "irisecho";
    const windows = /win/i.test(navigator.userAgent);
    const raw = JSON.stringify(body());
    const text = windows ? raw.replaceAll("'", "''") : raw.replaceAll("'", "'\\''");
    const run = windows && cli.startsWith('"') ? `& ${cli}` : cli; // PowerShell runs a quoted path with &
    copy(`echo '${text}' | ${run} api POST jobs - --wait`, "The command");
  };
</script>

<details class="all">
  <summary>All settings</summary>
  <dl>
    {#each changed as c (c.name)}
      <div>
        <dt>{c.label}</dt>
        <dd><span class="mono">{showValue(c, job.params[c.name])}</span>
          {#if c.type !== "loras"}<small> (recommended {showValue(c, c.default)})</small>{/if}</dd>
      </div>
    {/each}
    {#if seed != null && job.params.seed == null}
      <div><dt>Seed</dt><dd class="mono">{seed}</dd></div>
    {/if}
    {#if recommended.length}
      <div class="rec"><dt>Recommended</dt><dd>{recommended.map((c) => `${c.label} ${showValue(c, c.default)}`).join(" · ")}</dd></div>
    {/if}
    <div><dt>Model</dt><dd>{model?.name ?? job.model}</dd></div>
  </dl>
  <div class="copy">
    <button class="btn sm ghost" onclick={json}><Braces size={14} /> Copy as API JSON</button>
    {#if !app.remote}
      <button class="btn sm ghost" onclick={command}><Terminal size={14} /> Copy as command</button>
    {/if}
  </div>
  {#if model && app.settings?.advanced && job.status === "done"}
    <CompareSetting {job} {model} />
  {/if}
</details>

<style>
  .all summary {
    font-size: 13px;
    color: var(--text-3);
    cursor: pointer;
  }
  .all[open] summary {
    margin-bottom: 8px;
  }
  dl {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 13px;
    margin: 0;
  }
  dl div {
    display: grid;
    grid-template-columns: 110px 1fr;
    gap: 8px;
  }
  dt {
    color: var(--text-3);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  small,
  .rec dd {
    color: var(--text-4);
    font-size: 12px;
  }
  .copy {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }
</style>
