<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import Download from "@lucide/svelte/icons/download";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import FolderSearch from "@lucide/svelte/icons/folder-search";
  import Link from "@lucide/svelte/icons/link-2";
  import Plus from "@lucide/svelte/icons/plus";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Trash from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import Iris from "../components/Iris.svelte";
  import SetupPanel from "../components/SetupPanel.svelte";
  import { api } from "../lib/api";
  import { bytes, speed } from "../lib/format";
  import { inShell, pickFolder } from "../lib/shell";
  import { STUDIOS } from "../lib/studios";
  import { app, engineById, fail, modelProgress, refreshModels, toast } from "../lib/state.svelte";
  import type { Model } from "../lib/types";

  let expanded = $state<string | null>(null);
  let newDir = $state("");

  // Helpers are not a studio of their own; they work inside the others.
  const HELPERS = { kind: "prompt", label: "Helpers", verb: "", hint: "", sound: false } as const;
  const groups = $derived(
    [...STUDIOS, HELPERS]
      .map((s) => ({
        studio: s,
        // the 3D studio's view maker is listed with the other helpers
        models: app.models.filter((m) => m.kind === s.kind || (s.kind === "prompt" && m.kind === "views3d")),
      }))
      .filter((g) => g.models.length),
  );
  const onDisk = $derived(
    app.models.reduce((sum, m) => sum + m.files.filter((f) => f.state === "ready").reduce((a, f) => a + f.size, 0), 0),
  );
  const linkedCount = $derived(new Set(app.models.flatMap((m) => m.files.filter((f) => f.state === "linked").map((f) => f.id))).size);
  const scanning = $derived(app.scan?.state === "scanning");
  const scanPercent = $derived(
    app.scan?.overall_total ? Math.min(100, Math.round(((app.scan.overall_done ?? 0) / app.scan.overall_total) * 100)) : null,
  );
  // What the linked folders gave, model by model, and what each still needs before it can be
  // used. Derived from the models themselves, so it stays true after a restart.
  const found = $derived(
    app.models
      .filter((m) => m.available && m.files.some((f) => f.state === "linked"))
      .map((m) => {
        const engine = engineById(m.engine);
        const installing = m.installing || engine?.state === "installing";
        const engineOnly = !m.ready && m.needs.every((n) => n === "engine");
        let note = "Ready";
        let tone = "ok";
        if (m.ready) {
          /* nothing left to do */
        } else if (m.needs.includes("download")) {
          note = scanning ? "Still checking" : `${bytes(m.size - modelProgress(m).have)} more to download`;
          tone = "";
        } else if (m.needs.includes("license")) {
          note = "Accept its license";
          tone = "warn";
        } else if (installing) {
          note = "Installing its engine";
          tone = "violet";
        } else if (engine?.state === "error") {
          note = "Its engine did not install";
          tone = "danger";
        } else {
          note = "Needs its engine";
          tone = "violet";
        }
        return { m, note, tone, engine, waiting: engineOnly && !installing, installing: engineOnly && installing };
      }),
  );
  // Engines standing between files that are all here and a model that can be used.
  const uniqueEngines = (rows: typeof found) => [...new Map(rows.flatMap((r) => (r.engine ? [[r.engine.id, r.engine] as const] : []))).values()];
  const enginesToInstall = $derived(uniqueEngines(found.filter((r) => r.waiting)));
  const enginesInstalling = $derived(uniqueEngines(found.filter((r) => r.installing)));
  const names = (engines: { name: string }[]) => engines.map((e) => e.name).join(" and ");
  let finishing = $state(false);

  async function finish() {
    finishing = true;
    try {
      for (const e of enginesToInstall) await api(`/engines/${e.id}/install`, { body: {} });
      refreshModels();
    } catch (e) {
      fail(e);
    } finally {
      finishing = false;
    }
  }

  function show(m: Model) {
    if (!m.ready) expanded = m.id;
    document.getElementById(`model-${m.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function remove(m: Model) {
    try {
      const { freed } = await api<{ freed: number }>(`/models/${m.id}/files`, { method: "DELETE" });
      toast(freed ? `Removed ${m.name} and freed ${bytes(freed)}.` : `${m.name}'s files are shared with other models, so they were kept.`, "ok");
      refreshModels();
    } catch (e) {
      fail(e);
    }
  }

  async function setDirs(dirs: string[]) {
    try {
      app.settings = await api("/settings/linked-dirs", { body: { dirs } });
    } catch (e) {
      fail(e);
    }
  }

  let dirNote = $state("");

  async function addDir(browse = false) {
    dirNote = "";
    let picked: string | null;
    try {
      picked = browse ? await pickFolder() : newDir.trim();
    } catch {
      dirNote = "The folder picker did not open. Paste the folder's path instead.";
      return;
    }
    if (!picked && browse) return; // the person closed the picker
    if (!picked) {
      dirNote = inShell() ? "Choose a folder with Browse, or paste its path." : "Paste the folder's path first.";
      return;
    }
    picked = picked.replace(/^["']|["']$/g, ""); // a path copied as text often keeps its quotes
    if ((app.settings?.linked_model_dirs ?? []).includes(picked)) {
      dirNote = "That folder is already linked.";
      return;
    }
    await setDirs([...(app.settings?.linked_model_dirs ?? []), picked]);
    newDir = "";
  }

  function status(m: Model) {
    const p = modelProgress(m);
    const engine = engineById(m.engine);
    if (m.ready) return { label: "Ready", tone: "ok" };
    if (!m.available) return { label: "Not for this computer", tone: "" };
    if (m.installing || engine?.state === "installing") return { label: "Installing engine", tone: "violet" };
    if (p.active) return { label: `${Math.round((p.have / Math.max(1, p.total)) * 100)}% · ${speed(p.speed)}`, tone: "violet" };
    if (p.blocked) return { label: "Needs attention", tone: "danger" };
    // Weights already on disk (yours, linked): only the engine is left to install.
    if (!m.needs.includes("download") && m.needs.includes("license")) return { label: "Your files found · license to accept", tone: "warn" };
    if (!m.needs.includes("download") && m.needs.includes("engine")) return { label: "Your files found · engine to install", tone: "violet" };
    return { label: bytes(m.size - p.have), tone: "" };
  }
</script>

<div class="models">
  <header>
    <div>
      <h1>Models</h1>
      <p>
        {bytes(onDisk)} downloaded{linkedCount ? ` · ${linkedCount} file${linkedCount === 1 ? "" : "s"} used from your own folders` : ""}.
        Nothing is downloaded until you choose a model.
      </p>
    </div>
  </header>

  <div class="content">
    <div class="columns">
      <div class="catalog">
        {#each groups as g (g.studio.kind)}
          <section class:sound={g.studio.sound}>
            <h2>{g.studio.label}</h2>
            <div class="cards">
              {#each g.models as m (m.id)}
                {@const st = status(m)}
                {@const p = modelProgress(m)}
                <article id={`model-${m.id}`} class="model" class:open={expanded === m.id} class:unavailable={!m.available}>
                  <div class="row">
                    <div class="info">
                      <div class="name">
                        <h3>{m.name}</h3>
                        {#if m.preview}<span class="badge">Preview</span>{/if}
                      </div>
                      <p>{m.summary}</p>
                      <div class="tags">
                        <a class="badge" class:warn={!m.license.commercial} href={m.license.url} target="_blank" rel="noreferrer">
                          {m.license.commercial ? m.license.name : `Non-commercial · ${m.license.name}`}
                        </a>
                        {#if m.license.condition}<span class="badge warn">{m.license.condition}</span>{/if}
                        {#if m.license.gated}<span class="badge violet">Needs a Hugging Face account</span>{/if}
                        <span class="badge">{bytes(m.size)}</span>
                        {#if m.files.some((f) => f.state === "linked")}<span class="badge"><Link size={11} /> Uses your files</span>{/if}
                      </div>
                    </div>
                    <div class="side">
                      <span class="badge {st.tone}">
                        {#if m.ready}<Check size={12} />{/if}
                        {st.label}
                      </span>
                      {#if m.ready && m.files.some((f) => f.state === "ready")}
                        <button class="btn sm ghost danger" onclick={() => remove(m)} title="Remove downloaded files">
                          <Trash size={14} /> Remove
                        </button>
                      {:else if m.ready}
                        <span class="dim small">Using your files</span>
                      {:else if m.available}
                        <button class="btn sm" onclick={() => (expanded = expanded === m.id ? null : m.id)}>
                          {expanded === m.id ? "Close" : p.active ? "Details" : !m.needs.includes("download") ? "Finish setup" : "Set up"}
                        </button>
                      {/if}
                    </div>
                  </div>
                  {#if p.active && !m.ready}
                    <div class="progress"><i style:width={`${(p.have / Math.max(1, p.total)) * 100}%`}></i></div>
                  {/if}
                  {#if expanded === m.id && !m.ready}
                    <div class="expand">
                      <SetupPanel model={m} />
                      <ul class="files">
                        {#each m.files as f (f.id)}
                          {@const d = app.downloads[f.id]}
                          <li>
                            <span class="mono">{f.id.split("/").pop()}</span>
                            <span class="mono dim">
                              {#if f.state === "ready" || f.state === "linked" || d?.state === "done"}
                                {f.state === "linked" ? "from your folder" : "done"}
                              {:else if d && ["downloading", "verifying", "queued"].includes(d.state)}
                                {d.state === "verifying" ? "checking" : `${bytes(d.done)} / ${bytes(f.size)}`}
                              {:else if d?.state === "error" || d?.state === "needs_token" || d?.state === "needs_access"}
                                <span class="err">{d.error}</span>
                              {:else}
                                {bytes(f.size)}
                              {/if}
                            </span>
                            {#if d && ["downloading", "queued"].includes(d.state)}
                              <button class="btn sm icon ghost" title="Stop" onclick={() => api(`/downloads/${f.id}/cancel`, { body: {} })}>
                                <X size={13} />
                              </button>
                            {/if}
                          </li>
                        {/each}
                      </ul>
                    </div>
                  {/if}
                </article>
              {/each}
            </div>
          </section>
        {/each}
      </div>

      <aside class="card link">
        <div class="head">
          <FolderSearch size={20} />
          <h2>Already have models?</h2>
        </div>
        <p>
          Point IrisEcho at a folder you already use, like a ComfyUI <span class="mono">models</span> folder. It finds
          matching files, checks each one is identical, and uses it where it is. Nothing is copied or changed.
        </p>
        <ul class="dirs">
          {#each app.settings?.linked_model_dirs ?? [] as d (d)}
            <li>
              <span class="mono">{d}</span>
              <button
                class="btn sm icon ghost"
                title="Stop using this folder"
                onclick={() => setDirs((app.settings?.linked_model_dirs ?? []).filter((x) => x !== d))}
              >
                <X size={14} />
              </button>
            </li>
          {/each}
        </ul>
        <div class="add">
          <input
            class="input mono"
            placeholder="Paste a folder path"
            aria-label="Folder path"
            bind:value={newDir}
            oninput={() => (dirNote = "")}
            onkeydown={(e) => e.key === "Enter" && addDir()}
          />
          {#if inShell()}
            <button class="btn" onclick={() => addDir(true)}>Browse…</button>
          {/if}
          <button class="btn primary" onclick={() => addDir()}><Plus size={15} /> Add</button>
        </div>
        {#if dirNote}<p class="note" role="alert">{dirNote}</p>{/if}
        <p class="hint">Best: the <span class="mono">models</span> folder inside ComfyUI. A folder above it works too.</p>
        {#if scanning}
          <div class="scan" role="status">
            <Iris size={28} bars={28} active catchlight={false} />
            <span>
              {#if scanPercent === null}
                Looking through your folders…
              {:else}
                Comparing files, {scanPercent}% <span class="dim">· {app.scan?.file?.split("/").pop()}</span>
              {/if}
              <span class="dim block">Large files take a while to check. You can keep using IrisEcho.</span>
            </span>
          </div>
          {#if scanPercent !== null}<div class="progress"><i style:width={`${scanPercent}%`}></i></div>{/if}
        {/if}
        <!-- Models show up here as their files are confirmed, while the rest are still being checked. -->
        {#if app.settings?.linked_model_dirs.length && (found.length || !scanning)}
          <div class="result" role="status">
            {#if found.length}
              <strong><Check size={14} /> {found.length} model{found.length === 1 ? "" : "s"} found in your folders</strong>
              {#if enginesToInstall.length}
                <p>
                  One step left before you can use them: install {names(enginesToInstall)} (a few GB, one time). Engines
                  are the programs that run the models; your model files stay where they are.
                </p>
                <button class="btn primary" disabled={finishing} onclick={finish}>
                  <Download size={15} /> Install {names(enginesToInstall)} to finish
                </button>
              {:else if enginesInstalling.length}
                <div class="scan">
                  <Iris size={28} bars={28} active catchlight={false} />
                  <span>
                    Installing {names(enginesInstalling)}
                    <span class="dim block log mono">{app.engineLog[enginesInstalling[0].id] ?? "Preparing a private Python environment"}</span>
                  </span>
                </div>
              {/if}
              <ul class="found">
                {#each found as r (r.m.id)}
                  <li>
                    <button onclick={() => show(r.m)} title="Show this model">
                      <span class="name">{r.m.name}</span>
                      <span class="badge {r.tone}">{#if r.m.ready}<Check size={11} />{/if}{r.note}</span>
                    </button>
                  </li>
                {/each}
              </ul>
            {:else}
              <strong>Nothing in these folders matched yet</strong>
              <span class="dim">
                IrisEcho only uses files that are exact copies of the models it lists, so merged or re-quantized
                versions are skipped. Try the folder that holds your <span class="mono">diffusion_models</span> and
                <span class="mono">text_encoders</span>.
              </span>
            {/if}
            {#if !scanning}
              <button class="btn sm ghost" onclick={() => api("/settings/rescan", { body: {} })}><RefreshCw size={14} /> Look again</button>
            {/if}
          </div>
        {/if}
        <p class="fine">
          Model licenses apply however you got the file. <a href="https://huggingface.co" target="_blank" rel="noreferrer"
            >Hugging Face <ExternalLink size={11} /></a
          > hosts the originals.
        </p>
      </aside>
    </div>
  </div>
</div>

<style>
  .models {
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  header {
    padding: 26px 32px 18px;
    border-bottom: 1px solid var(--line);
  }
  h1 {
    font-size: 30px;
  }
  header p {
    color: var(--text-3);
  }
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px 48px;
  }
  .columns {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
    gap: 28px;
    align-items: start;
    max-width: 1320px;
  }
  .catalog {
    display: flex;
    flex-direction: column;
    gap: 28px;
  }
  section h2 {
    font-size: 15px;
    color: var(--accent-ink);
    margin-bottom: 10px;
    letter-spacing: 0.01em;
  }
  .cards {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .model {
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: var(--r-3);
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: border-color 0.15s;
  }
  .model.open {
    border-color: var(--line-2);
  }
  .model.unavailable {
    opacity: 0.55;
  }
  .row {
    display: flex;
    gap: 16px;
    align-items: flex-start;
  }
  .info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .name {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h3 {
    font-size: 17px;
  }
  .info p {
    color: var(--text-2);
    font-size: 13.5px;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 2px;
  }
  a.badge {
    text-decoration: none;
  }
  .side {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
  }
  .expand {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 18px;
    padding-top: 12px;
    border-top: 1px solid var(--line);
  }
  .files {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
  }
  .files li {
    display: flex;
    align-items: center;
    gap: 8px;
    justify-content: space-between;
    padding: 5px 8px;
    border-radius: 6px;
    background: var(--surface-2);
  }
  .files li > .mono:first-child {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dim {
    color: var(--text-3);
    white-space: nowrap;
  }
  .small {
    font-size: 12px;
  }
  .err {
    color: var(--danger-ink);
    white-space: normal;
  }
  .link {
    position: sticky;
    top: 0;
    padding: 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .link .head {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--amber-ink);
  }
  .link h2 {
    font-size: 17px;
    color: var(--text);
  }
  .link p {
    color: var(--text-2);
    font-size: 13px;
  }
  .dirs {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .dirs li {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 6px 6px 10px;
    border-radius: 8px;
    background: var(--surface-2);
    font-size: 12px;
  }
  .dirs li .mono {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .add {
    display: flex;
    gap: 6px;
  }
  .add .input {
    font-size: 12px;
    height: 36px;
  }
  .add .btn {
    flex: none;
  }
  .note {
    font-size: 12.5px;
    color: var(--amber-ink);
  }
  .hint {
    font-size: 12px;
    color: var(--text-4);
  }
  .block {
    display: block;
  }
  .result {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    font-size: 13px;
  }
  .result strong {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .result {
    width: 100%;
  }
  .result .btn.primary {
    align-self: stretch;
    height: auto;
    min-height: 38px;
    padding-block: 8px;
    white-space: normal;
  }
  .found {
    list-style: none;
    margin: 4px 0 0;
    padding: 0;
    width: 100%;
    max-height: 280px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .found button {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 5px 6px;
    border: 0;
    border-radius: 6px;
    background: none;
    color: var(--text-2);
    font: inherit;
    font-size: 12.5px;
    text-align: left;
    cursor: pointer;
  }
  .found button:hover {
    background: var(--surface-2);
  }
  .found .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .found .badge {
    flex: none;
  }
  .log {
    max-width: 240px;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 11.5px;
  }
  .scan {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: var(--text-2);
  }
  .fine {
    font-size: 12px !important;
    color: var(--text-3) !important;
  }
</style>
