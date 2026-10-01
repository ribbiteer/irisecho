<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Iris from "./Iris.svelte";
  import { app, engineById, modelById, type View } from "../lib/state.svelte";

  const all: { id: View; label: string }[] = [
    { id: "create", label: "Create" },
    { id: "library", label: "Library" },
    { id: "models", label: "Models" },
    { id: "settings", label: "Settings" },
  ];
  // Models and settings belong to the computer; a paired phone only makes and browses.
  const tabs = $derived(app.remote ? all.slice(0, 2) : all);

  const running = $derived(app.jobs.find((j) => j.status === "running"));
  const waiting = $derived(app.jobs.filter((j) => j.status === "queued").length);
  const resident = $derived(app.system?.resident ? engineById(app.system.resident) : undefined);
  const loaded = $derived(app.system?.loaded_model ? modelById(app.system.loaded_model) : undefined);
  const gpu = $derived(app.system?.hardware.gpus[0]);
  const activeDownloads = $derived(
    Object.values(app.downloads).filter((d) => ["queued", "downloading", "verifying"].includes(d.state)).length,
  );
  const installing = $derived(app.engines.find((e) => e.installing || e.state === "installing"));

  const status = $derived.by(() => {
    if (running) {
      const m = modelById(running.model);
      return {
        title: running.message ?? `${m?.name ?? running.model}`,
        sub: waiting ? `${waiting} waiting` : (m?.name ?? ""),
        progress: running.progress,
        active: true,
      };
    }
    if (installing) return { title: `Installing ${installing.name}`, sub: "one-time setup", progress: null, active: true };
    if (activeDownloads)
      return { title: "Downloading models", sub: `${activeDownloads} file${activeDownloads === 1 ? "" : "s"}`, progress: null, active: true };
    return {
      title: loaded ? `${loaded.name} is loaded` : resident ? `${resident.name} is loaded` : "Ready",
      sub: gpu ? gpu.name.replace("NVIDIA GeForce ", "") : app.system?.hardware.backend === "mps" ? "Apple Silicon" : "CPU",
      progress: null,
      active: false,
    };
  });
</script>

<header>
  <div class="brand">
    <Iris size={30} bars={36} />
    <span class="word">IrisEcho</span>
  </div>

  <nav aria-label="Sections">
    {#each tabs as t (t.id)}
      <button class="tab" aria-current={app.view === t.id ? "page" : undefined} onclick={() => (app.view = t.id)}>
        {t.label}
      </button>
    {/each}
  </nav>

  <button class="status" class:busy={status.active} onclick={() => (app.view = "library")} title="GPU activity">
    <Iris size={26} bars={30} active={status.active} progress={status.progress} catchlight={false} />
    <span class="text">
      <strong>{status.title}</strong>
      <small>{status.sub}</small>
    </span>
  </button>
</header>

<style>
  header {
    height: 58px;
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    padding: 0 16px 0 18px;
    border-bottom: 1px solid var(--line);
    background: linear-gradient(to bottom, var(--surface-1), var(--bg));
    -webkit-app-region: drag;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .word {
    font-size: 19px;
    font-weight: 700;
    font-stretch: 88%;
    letter-spacing: -0.02em;
    font-variation-settings: "opsz" 72;
  }
  nav {
    display: flex;
    gap: 2px;
    padding: 4px;
    background: var(--surface-1);
    border: 1px solid var(--line);
    border-radius: 99px;
    -webkit-app-region: no-drag;
  }
  .tab {
    border: 0;
    background: transparent;
    color: var(--text-3);
    height: 32px;
    padding: 0 16px;
    border-radius: 99px;
    font-weight: 560;
    cursor: pointer;
    transition:
      color 0.15s,
      background 0.15s;
  }
  .tab:hover {
    color: var(--text);
  }
  .tab[aria-current="page"] {
    background: var(--surface-4);
    color: var(--text);
  }
  .status {
    justify-self: end;
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: 320px;
    height: 42px;
    padding: 0 14px 0 8px;
    border-radius: 99px;
    border: 1px solid var(--line);
    background: var(--surface-1);
    cursor: pointer;
    -webkit-app-region: no-drag;
    text-align: left;
  }
  .status.busy {
    border-color: rgb(107 91 214 / 0.35);
  }
  .text {
    display: flex;
    flex-direction: column;
    line-height: 1.15;
    min-width: 0;
  }
  .text strong {
    font-size: 12.5px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .text small {
    font-size: 11.5px;
    color: var(--text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  @media (max-width: 820px) {
    header {
      grid-template-columns: auto 1fr auto;
      gap: 8px;
      height: 52px;
      padding: 0 10px;
    }
    .word,
    .text {
      display: none;
    }
    nav {
      justify-self: center;
    }
    .tab {
      padding: 0 12px;
      height: 36px;
    }
    .status {
      padding: 0 8px;
      height: 40px;
    }
  }
  /* A phone: the status button already shows the mark, and the four tabs need the room. */
  @media (max-width: 520px) {
    header {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .brand {
      display: none;
    }
    nav {
      justify-self: start;
      min-width: 0;
    }
    .tab {
      padding: 0 9px;
      font-size: 13.5px;
    }
  }
</style>
