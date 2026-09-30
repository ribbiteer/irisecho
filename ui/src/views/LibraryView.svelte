<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Heart from "@lucide/svelte/icons/heart";
  import Search from "@lucide/svelte/icons/search";
  import X from "@lucide/svelte/icons/x";
  import AudioRow from "../components/AudioRow.svelte";
  import ImageTile from "../components/ImageTile.svelte";
  import Iris from "../components/Iris.svelte";
  import { api } from "../lib/api";
  import { STUDIOS } from "../lib/studios";
  import { app, fail } from "../lib/state.svelte";
  import type { Job } from "../lib/types";

  const lib = app.library;
  const SINCE = [
    ["", "Any time"],
    ["1", "Today"],
    ["7", "This week"],
    ["30", "This month"],
  ] as const;

  const filtered = $derived(
    lib.q.trim() !== "" || lib.model !== "" || lib.since !== "" || lib.kind !== "all" || lib.favorites,
  );
  // With no filter the live list is shown; with one, the core searches everything it has kept.
  let found = $state<Job[]>([]);
  let searching = $state(false);
  let token = 0;

  async function search() {
    const mine = ++token;
    const p = new URLSearchParams({ limit: "300" });
    if (lib.q.trim()) p.set("q", lib.q.trim());
    if (lib.kind !== "all") p.set("kind", lib.kind);
    if (lib.model) p.set("model", lib.model);
    if (lib.favorites) p.set("favorite", "true");
    if (lib.since) p.set("after", String(Date.now() / 1000 - Number(lib.since) * 86400));
    searching = true;
    try {
      const jobs = await api<Job[]>(`/jobs?${p}`);
      if (mine === token) found = jobs;
    } catch (e) {
      if (mine === token) fail(e);
    } finally {
      if (mine === token) searching = false;
    }
  }

  // Search again when a filter changes (after a pause while typing), and when jobs
  // finish, are favourited or deleted.
  const signature = $derived(app.jobs.map((j) => `${j.id}${j.status}${j.favorite}`).join());
  $effect(() => {
    void [lib.q, lib.kind, lib.model, lib.since, lib.favorites, signature];
    if (!filtered) return;
    const t = setTimeout(search, lib.q ? 220 : 0);
    return () => clearTimeout(t);
  });

  const source = $derived(filtered ? found : app.jobs);
  const kinds = $derived(STUDIOS.filter((s) => app.jobs.some((j) => j.kind === s.kind)));
  const modelNames = $derived(
    [...new Set(app.jobs.map((j) => j.model))]
      .map((id) => ({ id, name: app.models.find((m) => m.id === id)?.name ?? id }))
      .sort((a, b) => a.name.localeCompare(b.name)),
  );
  const soundKinds = new Set(STUDIOS.filter((s) => s.sound).map((s) => s.kind));
  const pictures = $derived(source.filter((j) => !soundKinds.has(j.kind)));
  const sounds = $derived(source.filter((j) => soundKinds.has(j.kind)));
  const active = $derived(app.jobs.filter((j) => j.status === "running" || j.status === "queued").length);

  function clear() {
    Object.assign(lib, { q: "", kind: "all", model: "", favorites: false, since: "" });
  }
</script>

<div class="library">
  <header>
    <div>
      <h1>Library</h1>
      <p>
        {filtered
          ? `${source.length} match${source.length === 1 ? "" : "es"}${searching ? "…" : ""}`
          : `${app.jobs.filter((j) => j.status === "done").length} things made on this computer`}{active
          ? ` · ${active} in progress`
          : ""}.
      </p>
    </div>
    <div class="filters">
      <label class="search">
        <Search size={15} />
        <input
          type="search"
          class="input"
          placeholder="Search prompts and text"
          aria-label="Search the library"
          bind:value={lib.q}
        />
        {#if lib.q}
          <button class="clear" aria-label="Clear the search" onclick={() => (lib.q = "")}><X size={14} /></button>
        {/if}
      </label>
      <div class="chips">
        <button class="chip" aria-pressed={lib.kind === "all"} onclick={() => (lib.kind = "all")}>All</button>
        {#each kinds as s (s.kind)}
          <button class="chip" class:sound={s.sound} aria-pressed={lib.kind === s.kind} onclick={() => (lib.kind = s.kind)}>
            {s.label}
          </button>
        {/each}
      </div>
      <div class="chips">
        {#each SINCE as [id, label] (id)}
          <button class="chip" aria-pressed={lib.since === id} onclick={() => (lib.since = id)}>{label}</button>
        {/each}
        <button class="chip" aria-pressed={lib.favorites} onclick={() => (lib.favorites = !lib.favorites)}>
          <Heart size={14} fill={lib.favorites ? "currentColor" : "none"} /> Favorites
        </button>
      </div>
      {#if modelNames.length > 1}
        <select class="select model" aria-label="Filter by model" bind:value={lib.model}>
          <option value="">Every model</option>
          {#each modelNames as m (m.id)}<option value={m.id}>{m.name}</option>{/each}
        </select>
      {/if}
    </div>
  </header>

  <div class="content">
    {#if !source.length}
      <div class="empty">
        <Iris size={110} bars={56} />
        {#if filtered}
          <h3>{lib.q ? "Nothing matches" : lib.favorites && !lib.q ? "No favorites here" : "Nothing here"}</h3>
          <p>Try different words, or take a filter off.</p>
          <button class="btn" onclick={clear}>Clear search and filters</button>
        {:else}
          <h3>Nothing here yet</h3>
          <p>Pictures and sounds you make will collect here.</p>
          <button class="btn primary" onclick={() => (app.view = "create")}>Start creating</button>
        {/if}
      </div>
    {:else}
      {#if pictures.length}
        <section>
          {#if sounds.length}<h2>Pictures</h2>{/if}
          <div class="grid">
            {#each pictures as job (job.id)}
              <ImageTile {job} />
            {/each}
          </div>
        </section>
      {/if}
      {#if sounds.length}
        <section class="sound">
          {#if pictures.length}<h2>Sound</h2>{/if}
          <div class="list">
            {#each sounds as job (job.id)}
              <AudioRow {job} />
            {/each}
          </div>
        </section>
      {/if}
    {/if}
  </div>
</div>

<style>
  .library {
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 20px;
    flex-wrap: wrap;
    padding: 26px 32px 18px;
    border-bottom: 1px solid var(--line);
  }
  h1 {
    font-size: 30px;
  }
  header p {
    color: var(--text-3);
  }
  .filters {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .search {
    position: relative;
    display: flex;
    align-items: center;
    width: 260px;
  }
  .search :global(svg:first-child) {
    position: absolute;
    left: 12px;
    color: var(--text-3);
    pointer-events: none;
  }
  .search .input {
    height: 34px;
    padding: 0 32px 0 34px;
    border-radius: 999px;
  }
  .search .input::-webkit-search-cancel-button {
    display: none;
  }
  .clear {
    position: absolute;
    right: 8px;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    color: var(--text-3);
  }
  .clear:hover {
    background: var(--surface-3);
    color: var(--text);
  }
  .model {
    width: auto;
    height: 34px;
    padding: 0 10px;
  }
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px 48px;
    display: flex;
    flex-direction: column;
    gap: 30px;
  }
  h2 {
    font-size: 16px;
    color: var(--text-2);
    margin-bottom: 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 12px;
    align-items: start;
  }
  .list {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 980px;
  }
  .empty {
    margin: auto;
  }

  @media (max-width: 820px) {
    header {
      padding: 16px 14px 12px;
    }
    h1 {
      font-size: 26px;
    }
    .filters {
      justify-content: flex-start;
    }
    .search {
      width: 100%;
    }
    .content {
      padding: 16px 14px 32px !important;
    }
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    }
  }
</style>
