<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import Copy from "@lucide/svelte/icons/copy";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import FolderOpen from "@lucide/svelte/icons/folder-open";
  import KeyRound from "@lucide/svelte/icons/key-round";
  import Iris from "../components/Iris.svelte";
  import LanCard from "../components/LanCard.svelte";
  import { api } from "../lib/api";
  import { pickFolder } from "../lib/shell";
  import { app, fail, refresh, toast } from "../lib/state.svelte";
  import type { Settings } from "../lib/types";

  let token = $state("");
  let saving = $state(false);
  let editing = $state<"" | "models_dir" | "outputs_dir">("");
  let draft = $state("");

  const hw = $derived(app.system?.hardware);
  const gpu = $derived(hw?.gpus[0]);

  async function saveToken() {
    saving = true;
    try {
      const r = await api<{ account: string }>("/credentials/huggingface", { method: "PUT", body: { token } });
      toast(`Signed in to Hugging Face as ${r.account}.`, "ok");
      token = "";
      await refresh();
    } catch (e) {
      fail(e);
    } finally {
      saving = false;
    }
  }

  async function removeToken() {
    try {
      await api("/credentials/huggingface", { method: "DELETE" });
      await refresh();
    } catch (e) {
      fail(e);
    }
  }

  // What to tell an assistant so it can find and use this IrisEcho by itself.
  const agentNote = $derived(
    `IrisEcho is installed on this computer. It makes images, video, speech, music and sound effects locally. ` +
      `Before using it, read its guide at ${app.guide.path} (or run: ${app.guide.cli} agents).`,
  );

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${what} copied.`, "ok");
    } catch {
      toast("Copying is not available here; select the text instead.", "error");
    }
  }

  async function patch(changes: Partial<Settings>) {
    try {
      app.settings = await api<Settings>("/settings", { method: "PATCH", body: changes });
    } catch (e) {
      fail(e);
    }
  }

  async function startEdit(key: "models_dir" | "outputs_dir") {
    const picked = await pickFolder().catch(() => null); // no picker: type the path instead
    if (picked) {
      await patch({ [key]: picked });
      return;
    }
    editing = key;
    draft = app.settings?.[key === "models_dir" ? "models_path" : "outputs_path"] ?? "";
  }

  async function saveEdit() {
    if (!editing) return;
    await patch({ [editing]: draft.trim() });
    editing = "";
  }
</script>

<div class="settings">
  <header>
    <h1>Settings</h1>
  </header>

  <div class="content">
    <section class="card">
      <h2>Storage</h2>
      {#each [["models_dir", "Models", "models_path", "Downloaded model weights. They are large; a fast drive with room to spare is best."], ["outputs_dir", "Your creations", "outputs_path", "Everything you make, in dated folders."]] as [key, label, pathKey, help] (key)}
        <div class="setting">
          <div class="what">
            <strong>{label}</strong>
            <small>{help}</small>
          </div>
          {#if editing === key}
            <div class="edit">
              <input class="input mono" bind:value={draft} onkeydown={(e) => e.key === "Enter" && saveEdit()} />
              <button class="btn sm primary" onclick={saveEdit}>Save</button>
              <button class="btn sm ghost" onclick={() => (editing = "")}>Cancel</button>
            </div>
          {:else}
            <div class="value">
              <span class="mono path">{app.settings?.[pathKey as "models_path"]}</span>
              <button class="btn sm ghost" onclick={() => api(`/open/${key === "models_dir" ? "models" : "outputs"}`, { body: {} })}>
                <FolderOpen size={14} /> Open
              </button>
              <button class="btn sm" onclick={() => startEdit(key as "models_dir")}>Change</button>
            </div>
          {/if}
        </div>
      {/each}
      <p class="note">Changing the models folder does not move files already downloaded.</p>
    </section>

    <section class="card">
      <h2>Hugging Face</h2>
      <div class="setting">
        <div class="what">
          <strong>Account token</strong>
          <small>
            A few models are gated: their publisher asks you to accept terms on Hugging Face first. A read-only token lets
            IrisEcho download them for you. It is kept in your system's keychain and sent only to Hugging Face.
          </small>
        </div>
        {#if app.credentials.huggingface}
          <div class="value">
            <span class="badge ok"><Check size={12} /> Token saved</span>
            <button class="btn sm ghost danger" onclick={removeToken}>Remove</button>
          </div>
        {:else}
          <div class="edit">
            <input class="input mono" type="password" placeholder="hf_…" bind:value={token} autocomplete="off" />
            <button class="btn sm primary" disabled={!token || saving} onclick={saveToken}><KeyRound size={14} /> Save</button>
          </div>
          <a class="help" href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer">
            Create a read token <ExternalLink size={12} />
          </a>
        {/if}
      </div>
      {#if !app.credentials.keychain}
        <p class="note warn">No system keychain was found, so a token cannot be stored on this computer.</p>
      {/if}
    </section>

    <LanCard />

    {#if app.guide.path}
      <section class="card">
        <h2>Scripts and coding agents</h2>
        <p class="note">
          Everything IrisEcho makes can also be asked for from a command line, so your own scripts, or an assistant such as
          Claude Code working on this computer, can make pictures and sound with it. The guide explains how.
        </p>
        <div class="setting">
          <div class="what">
            <strong>Guide</strong>
            <small class="mono wrap">{app.guide.path}</small>
          </div>
          <div class="value">
            <button class="btn sm ghost" onclick={() => api("/open/guide", { body: {} }).catch(fail)}>
              <FolderOpen size={14} /> Show
            </button>
            <button class="btn sm" onclick={() => copy(agentNote, "A note for your assistant")}>
              <Copy size={14} /> Copy a note for your assistant
            </button>
          </div>
        </div>
        <div class="setting">
          <div class="what">
            <strong>Command line</strong>
            <small class="mono wrap">{app.guide.cli} --help</small>
          </div>
          <button class="btn sm ghost" onclick={() => copy(app.guide.cli, "The command")}><Copy size={14} /> Copy</button>
        </div>
      </section>
    {/if}

    <section class="card">
      <h2>Privacy</h2>
      <div class="setting">
        <div class="what">
          <strong>Keep prompts out of image files</strong>
          <small>Images are saved without embedded prompts or settings. Those stay in your library on this computer.</small>
        </div>
        <button
          class="switch"
          role="switch"
          aria-checked={app.settings?.strip_metadata}
          aria-label="Keep prompts out of image files"
          onclick={() => patch({ strip_metadata: !app.settings?.strip_metadata })}
        ></button>
      </div>
      <p class="note">IrisEcho has no accounts, no analytics and no telemetry. It only goes online to download what you ask for.</p>
    </section>

    <section class="card">
      <h2>This computer</h2>
      <dl>
        <dt>Graphics</dt>
        <dd>
          {#if gpu}{gpu.name} · {Math.round(gpu.vram_mb / 1024)} GB{:else if hw?.backend === "mps"}Apple Silicon (Metal){:else}None found (CPU only){/if}
        </dd>
        <dt>Builds used</dt>
        <dd class="mono">{hw?.backend}{hw?.quant ? ` · ${hw.quant}` : ""}{hw?.cuda_tag ? ` · ${hw.cuda_tag}` : ""}</dd>
        <dt>Memory</dt>
        <dd>{hw ? Math.round(hw.ram_mb / 1024) : "?"} GB</dd>
        <dt>Support</dt>
        <dd>{hw?.tier}</dd>
        <dt>Data folder</dt>
        <dd class="mono path">{app.system?.data_dir}</dd>
      </dl>
    </section>

    <section class="card about">
      <Iris size={56} bars={44} />
      <div>
        <h2>IrisEcho {app.version}</h2>
        <p>
          Free software under the GNU Affero General Public License v3.0 or later. Each model keeps its own license,
          shown next to it on the Models page.
        </p>
        <p class="links">
          <a href="https://github.com/ribbiteer/irisecho" target="_blank" rel="noreferrer">Source code <ExternalLink size={12} /></a>
          <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noreferrer">License <ExternalLink size={12} /></a>
          <button class="linkish" onclick={() => api("/open/logs", { body: {} })}>Open logs</button>
        </p>
      </div>
    </section>
  </div>
</div>

<style>
  .settings {
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
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px 48px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 880px;
  }
  .card {
    padding: 20px 22px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  h2 {
    font-size: 17px;
  }
  .setting {
    display: flex;
    align-items: center;
    gap: 24px;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .what {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1 1 320px;
  }
  .what small {
    color: var(--text-3);
    font-size: 12.5px;
  }
  .value,
  .edit {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .edit .input {
    width: 320px;
    height: 32px;
    font-size: 12.5px;
    padding: 0 10px;
  }
  .path {
    font-size: 12px;
    color: var(--text-2);
    max-width: 380px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .help {
    font-size: 12.5px;
  }
  .wrap {
    overflow-wrap: anywhere;
    user-select: text;
  }
  .note {
    font-size: 12.5px;
    color: var(--text-3);
  }
  .note.warn {
    color: var(--amber-ink);
  }
  dl {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 8px 16px;
    margin: 0;
    font-size: 13.5px;
  }
  dt {
    color: var(--text-3);
  }
  dd {
    margin: 0;
    min-width: 0;
  }
  dd.path {
    max-width: none;
  }
  .about {
    flex-direction: row;
    align-items: center;
    gap: 18px;
  }
  .about p {
    color: var(--text-2);
    font-size: 13px;
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
  .links {
    display: flex;
    gap: 16px;
    margin-top: 6px !important;
  }
</style>
