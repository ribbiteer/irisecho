<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import Heart from "@lucide/svelte/icons/heart";
  import Search from "@lucide/svelte/icons/search";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import X from "@lucide/svelte/icons/x";
  import AudioRow from "../components/AudioRow.svelte";
  import ImageTile from "../components/ImageTile.svelte";
  import Iris from "../components/Iris.svelte";
  import { api } from "../lib/api";
  import { STUDIOS } from "../lib/studios";
  import { app, deleteJobs, fail, gone } from "../lib/state.svelte";
  import type { Job } from "../lib/types";

  const lib = app.library;
  const SINCE = [
    ["", "Any time"],
    ["1", "Today"],
    ["7", "This week"],
    ["30", "This month"],
  ] as const;
  const PAGE = 60;

  const filtered = $derived(
    lib.q.trim() !== "" || lib.model !== "" || lib.since !== "" || lib.kind !== "all" || lib.favorites,
  );
  // The core is asked for everything it has kept, a page at a time, so work older than the
  // newest jobs is there too. Live events still update what is shown (see `source`).
  let found = $state<Job[]>([]);
  let total = $state(0); // how many the core has for these filters
  let made = $state(0); // finished ones, with no filter
  let loaded = $state(false);
  let searching = $state(false);
  let more = $state(false);
  let token = 0;
  let lastFilters = "";

  function filterParams(): URLSearchParams {
    const p = new URLSearchParams();
    if (lib.q.trim()) p.set("q", lib.q.trim());
    if (lib.kind !== "all") p.set("kind", lib.kind);
    if (lib.model) p.set("model", lib.model);
    if (lib.favorites) p.set("favorite", "true");
    if (lib.since) p.set("after", String(Date.now() / 1000 - Number(lib.since) * 86400));
    return p;
  }

  // The core answers at most 500 at a time; further pages start after the last one seen.
  async function fetchJobs(p: URLSearchParams, want: number, after?: Job): Promise<Job[]> {
    const out: Job[] = [];
    let last = after;
    while (out.length < want) {
      const size = Math.min(500, want - out.length);
      const q = new URLSearchParams(p);
      q.set("limit", String(size));
      if (last) q.set("before", String(last.created));
      const page = await api<Job[]>(`/jobs?${q}`);
      out.push(...page);
      if (page.length < size) break;
      last = page[page.length - 1];
    }
    return out;
  }

  async function search() {
    const mine = ++token;
    const p = filterParams();
    const key = [lib.q.trim(), lib.kind, lib.model, lib.since, lib.favorites].join("|");
    // The same filters again (a job changed): look at as many as are already shown.
    const want = key === lastFilters ? Math.max(PAGE, found.length) : PAGE;
    searching = true;
    try {
      const [jobs, count, finished] = await Promise.all([
        fetchJobs(p, want),
        api<{ total: number }>(`/jobs/count?${p}`),
        filtered ? null : api<{ total: number }>(`/jobs/count?${p}&status=done`),
      ]);
      if (mine !== token) return;
      found = jobs;
      total = count.total;
      if (finished) made = finished.total;
      lastFilters = key;
      loaded = true;
    } catch (e) {
      if (mine === token) fail(e);
    } finally {
      if (mine === token) searching = false;
    }
  }

  async function showMore() {
    if (more || !found.length) return;
    const mine = token;
    more = true;
    try {
      const next = await fetchJobs(filterParams(), PAGE, found[found.length - 1]);
      if (mine !== token) return;
      const have = new Set(found.map((j) => j.id));
      found = [...found, ...next.filter((j) => !have.has(j.id))];
    } catch (e) {
      fail(e);
    } finally {
      more = false;
    }
  }

  // Look again when a filter changes (after a pause while typing), and when jobs
  // arrive, finish, are favourited or are deleted.
  const signature = $derived(app.jobs.map((j) => `${j.id}${j.status}${j.favorite}`).join());
  $effect(() => {
    void [lib.q, lib.kind, lib.model, lib.since, lib.favorites, signature, app.removals];
    const t = setTimeout(search, lib.q ? 220 : 0);
    return () => clearTimeout(t);
  });

  // What is shown: the core's list with the page's live copies laid over it (progress,
  // favorites), without anything taken away here and not yet gone from the core.
  const source = $derived.by(() => {
    void app.removals;
    const live = new Map(app.jobs.map((j) => [j.id, j]));
    return (loaded ? found : app.jobs).filter((j) => !gone.has(j.id)).map((j) => live.get(j.id) ?? j);
  });
  const hidden = $derived(found.filter((j) => gone.has(j.id)).length);
  const count = $derived(Math.max(source.length, (loaded ? total : source.length) - hidden));
  const canMore = $derived(loaded && found.length < total);

  // The lightbox moves through what is on this page.
  $effect(() => {
    app.shown = source;
    return () => (app.shown = null);
  });
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

  // Select: pick several finished or failed jobs and delete them together (one Undo for all).
  let selecting = $state(false);
  let picked = $state(new Set<string>());
  const deletable = (j: Job) => j.status !== "queued" && j.status !== "running";
  const pickable = $derived(source.filter(deletable));
  const chosen = $derived(pickable.filter((j) => picked.has(j.id)));

  function toggle(job: Job) {
    if (!deletable(job)) return;
    const next = new Set(picked);
    if (!next.delete(job.id)) next.add(job.id);
    picked = next;
  }
  function stopSelecting() {
    selecting = false;
    picked = new Set();
  }
  function deleteChosen() {
    if (!chosen.length) return;
    deleteJobs(chosen);
    stopSelecting();
  }
</script>

{#snippet pick(job: Job)}
  {#if selecting}
    <button
      class="pick"
      class:on={picked.has(job.id)}
      disabled={!deletable(job)}
      aria-pressed={picked.has(job.id)}
      aria-label={deletable(job) ? "Select" : "Still running"}
      onclick={() => toggle(job)}
    >
      <span class="box">{#if picked.has(job.id)}<Check size={14} />{/if}</span>
    </button>
  {/if}
{/snippet}

<div class="library">
  <header>
    <div>
      <h1>Library</h1>
      <p>
        {filtered
          ? `${count} match${count === 1 ? "" : "es"}${searching ? "…" : ""}`
          : `${loaded ? Math.max(0, made - hidden) : app.jobs.filter((j) => j.status === "done").length} things made on this computer`}{active
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
      {#if source.length && !selecting}
        <button class="chip" onclick={() => (selecting = true)}>Select</button>
      {/if}
    </div>
  </header>

  {#if selecting}
    <div class="selectbar">
      <span>{chosen.length} selected</span>
      <button class="btn sm ghost" onclick={() => (picked = new Set(pickable.map((j) => j.id)))} disabled={!pickable.length}>
        Select all shown
      </button>
      {#if chosen.length}<button class="btn sm ghost" onclick={() => (picked = new Set())}>Clear</button>{/if}
      <span class="spacer"></span>
      <button class="btn sm danger" onclick={deleteChosen} disabled={!chosen.length}>
        <Trash2 size={14} /> Delete{chosen.length ? ` ${chosen.length}` : ""}
      </button>
      <button class="btn sm" onclick={stopSelecting}>Done</button>
    </div>
  {/if}

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
              <div class="item" class:picked={selecting && picked.has(job.id)}>
                <ImageTile {job} />
                {@render pick(job)}
              </div>
            {/each}
          </div>
        </section>
      {/if}
      {#if sounds.length}
        <section class="sound">
          {#if pictures.length}<h2>Sound</h2>{/if}
          <div class="list">
            {#each sounds as job (job.id)}
              <div class="item" class:picked={selecting && picked.has(job.id)}>
                <AudioRow {job} />
                {@render pick(job)}
              </div>
            {/each}
          </div>
        </section>
      {/if}
      {#if canMore}
        <button class="btn more" onclick={showMore} disabled={more}>
          {more ? "Loading…" : `Show more (${Math.max(0, total - found.length)} left)`}
        </button>
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
  .more {
    align-self: center;
  }
  .selectbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 10px 32px;
    border-bottom: 1px solid var(--line);
    background: var(--surface-2);
    font-size: 13.5px;
  }
  .spacer {
    flex: 1;
  }
  .item {
    position: relative;
  }
  /* In Select, the whole tile or row is one button: nothing in it opens or plays. */
  .pick {
    position: absolute;
    inset: 0;
    z-index: 5;
    border: 2px solid transparent;
    border-radius: var(--r-2);
    background: rgb(0 0 0 / 0.08);
    cursor: pointer;
  }
  .pick:disabled {
    cursor: not-allowed;
    background: rgb(0 0 0 / 0.3);
  }
  .item.picked .pick {
    border-color: var(--violet-ink);
    background: rgb(107 91 214 / 0.18);
  }
  .box {
    position: absolute;
    top: 8px;
    left: 8px;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border-radius: 6px;
    border: 1.5px solid var(--text-2);
    background: var(--surface-1);
    color: var(--text);
  }
  .item.picked .box {
    background: var(--violet-ink);
    border-color: var(--violet-ink);
    color: #fff;
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
    .selectbar {
      padding: 10px 14px;
    }
    .content {
      padding: 16px 14px 32px !important;
    }
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    }
  }
</style>
