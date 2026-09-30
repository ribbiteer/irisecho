<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  Shown instead of the action button while a model is not ready: accept its
  license, install its engine, download its files. -->
<script lang="ts">
  import Download from "@lucide/svelte/icons/download";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import Iris from "./Iris.svelte";
  import { api } from "../lib/api";
  import { bytes, eta, speed } from "../lib/format";
  import { app, engineById, fail, modelProgress, refreshModels } from "../lib/state.svelte";
  import type { Model } from "../lib/types";

  let { model }: { model: Model } = $props();

  let accepted = $state(false);
  let busy = $state(false);

  const engine = $derived(engineById(model.engine));
  const progress = $derived(modelProgress(model));
  const installing = $derived(model.installing || engine?.state === "installing");
  const working = $derived(installing || progress.active);
  const needsLicense = $derived(model.needs.includes("license"));
  const remaining = $derived(Math.max(0, model.size - progress.have));
  const needsToken = $derived(model.license.gated && !app.credentials.huggingface && model.needs.includes("download"));

  const lastLines = (text: string) => text.split(/\r?\n/).slice(-3).join("\n");

  async function setUp() {
    busy = true;
    try {
      if (needsLicense) await api(`/licenses/${model.license.id}/accept`, { body: {} });
      await api(`/models/${model.id}/prepare`, { body: {} });
      refreshModels();
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="setup">
  {#if app.remote && !model.ready}
    <p class="muted">{model.name} is not set up yet. Set it up on the computer running IrisEcho.</p>
  {:else if model.needs.includes("unsupported")}
    <p class="muted">{model.name} cannot run on this computer.</p>
  {:else if engine?.state === "error" && !working}
    <div class="blocked">
      <strong>Setting up {engine.name} did not finish.</strong>
      <span class="detail mono">{lastLines(engine.detail)}</span>
      <div class="row">
        <button class="btn sm" onclick={setUp}>Try again</button>
        <button class="btn sm ghost" onclick={() => api("/open/logs", { body: {} })}>Open logs</button>
      </div>
    </div>
  {:else if working}
    <div class="working">
      <Iris size={46} bars={40} active progress={installing ? null : progress.have / Math.max(1, progress.total)} catchlight={false} />
      <div class="lines">
        {#if installing}
          <strong>Installing {engine?.name ?? model.engine}</strong>
          <small class="log mono">{app.engineLog[model.engine] ?? "Preparing a private Python environment"}</small>
        {:else}
          <strong>Downloading {bytes(progress.have)} of {bytes(progress.total)}</strong>
          <small>{speed(progress.speed)}{progress.speed ? " · " : ""}{eta(remaining, progress.speed)}</small>
        {/if}
      </div>
    </div>
    <p class="muted">You can leave this screen; setup keeps going in the background.</p>
  {:else}
    {#if needsToken}
      <div class="gated">
        <strong>{model.name} is shared on request</strong>
        <p>Its publisher asks each person to accept their terms before downloading. Two steps, once:</p>
        <ol>
          <li>
            Sign in to Hugging Face and accept the terms on the
            <a href={model.page} target="_blank" rel="noreferrer">model page <ExternalLink size={12} /></a>.
          </li>
          <li>Create a read token there and add it in <button class="linkish" onclick={() => (app.view = "settings")}>Settings</button>.</li>
        </ol>
      </div>
    {/if}

    {#if progress.blocked}
      <div class="blocked">
        <strong>{progress.blocked.error}</strong>
        {#if progress.blocked.state === "needs_token"}
          <button class="btn sm" onclick={() => (app.view = "settings")}>Add a Hugging Face token</button>
        {:else if progress.blocked.page}
          <a href={progress.blocked.page} target="_blank" rel="noreferrer">Open the model page <ExternalLink size={12} /></a>
        {/if}
        <span class="retry-note">Then press the button below again; finished files are kept.</span>
      </div>
    {/if}

    {#if needsLicense}
      <div class="license">
        <p>
          <strong>{model.license.name}.</strong>
          {model.license.summary}
          <a href={model.license.url} target="_blank" rel="noreferrer">Read it <ExternalLink size={12} /></a>
        </p>
        <label class="check">
          <input type="checkbox" bind:checked={accepted} />
          <span>I accept this license for my use of {model.name}.</span>
        </label>
      </div>
    {/if}

    <button class="btn primary big" disabled={busy || needsToken || (needsLicense && !accepted)} onclick={setUp}>
      <Download size={17} />
      {#if remaining > 0}
        Download {bytes(remaining)} and set up
      {:else}
        Set up {model.name}
      {/if}
    </button>
    <p class="muted">
      {#if model.needs.includes("engine")}First use also installs the {engine?.name ?? model.engine} engine (a few GB).{/if}
      Everything stays on this computer.
    </p>
  {/if}
</div>

<style>
  .setup {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .big {
    height: 46px;
    font-size: 15px;
    border-radius: var(--r-3);
  }
  .muted {
    font-size: 12.5px;
    color: var(--text-3);
    text-align: center;
  }
  .working {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 12px 14px;
    border-radius: var(--r-3);
    background: var(--surface-2);
    border: 1px solid var(--line);
  }
  .lines {
    display: flex;
    flex-direction: column;
    min-width: 0;
    gap: 2px;
  }
  .lines small {
    color: var(--text-3);
    font-size: 12px;
  }
  .log {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .license {
    padding: 12px 14px;
    border-radius: var(--r-2);
    background: rgb(242 165 65 / 0.07);
    border: 1px solid rgb(242 165 65 / 0.25);
    display: grid;
    gap: 10px;
    font-size: 13px;
    color: var(--text-2);
  }
  .license strong {
    color: var(--amber-ink);
  }
  .check {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    cursor: pointer;
    color: var(--text);
  }
  .check input {
    margin-top: 2px;
    accent-color: var(--amber);
  }
  .gated {
    padding: 12px 14px;
    border-radius: var(--r-2);
    background: rgb(107 91 214 / 0.1);
    border: 1px solid rgb(107 91 214 / 0.35);
    display: grid;
    gap: 6px;
    font-size: 13px;
    color: var(--text-2);
  }
  .gated strong {
    color: var(--violet-ink);
  }
  .gated ol {
    margin: 0;
    padding-left: 18px;
    display: grid;
    gap: 4px;
  }
  .linkish {
    border: 0;
    background: none;
    padding: 0;
    color: var(--accent-ink);
    cursor: pointer;
    font: inherit;
  }
  .linkish:hover {
    text-decoration: underline;
  }
  .detail {
    font-size: 11.5px;
    color: var(--text-3);
    white-space: pre-wrap;
    max-height: 5.5em;
    overflow: hidden;
  }
  .row {
    display: flex;
    gap: 8px;
  }
  .retry-note {
    font-size: 12px;
    color: var(--text-3);
  }
  .blocked {
    padding: 12px 14px;
    border-radius: var(--r-2);
    background: rgb(239 106 118 / 0.08);
    border: 1px solid rgb(239 106 118 / 0.3);
    display: grid;
    gap: 8px;
    font-size: 13px;
    justify-items: start;
  }
</style>
