<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import AudioRow from "./AudioRow.svelte";
  import ImageTile from "./ImageTile.svelte";
  import Iris from "./Iris.svelte";
  import Welcome from "./Welcome.svelte";
  import { app } from "../lib/state.svelte";
  import type { Studio } from "../lib/studios";

  let { studio }: { studio: Studio } = $props();

  const jobs = $derived(app.jobs.filter((j) => j.kind === studio.kind));
  const visual = $derived(!studio.sound);
  // First run: nothing made and nothing set up yet.
  const firstRun = $derived(!app.jobs.length && !app.models.some((m) => m.ready));
</script>

<section class="feed" aria-label="Results">
  {#if firstRun}
    <Welcome />
  {:else if !jobs.length}
    <div class="empty">
      <Iris size={120} bars={56} />
      <h3>{visual ? "Your pictures will show up here" : "Your recordings will show up here"}</h3>
      <p>{studio.hint}. Everything is made on this computer and saved to your library.</p>
    </div>
  {:else if visual}
    <div class="grid">
      {#each jobs as job (job.id)}
        <ImageTile {job} />
      {/each}
    </div>
  {:else}
    <div class="list">
      {#each jobs as job (job.id)}
        <AudioRow {job} />
      {/each}
    </div>
  {/if}
</section>

<style>
  .feed {
    min-height: 0;
    overflow-y: auto;
    padding: 22px 26px 40px;
  }
  .empty {
    height: 100%;
    max-width: 420px;
    margin: 0 auto;
  }
  .empty :global(svg) {
    opacity: 0.9;
    margin-bottom: 10px;
  }
  @media (max-width: 820px) {
    .feed {
      overflow: visible;
      padding: 16px 14px 32px;
    }
    .empty {
      height: auto;
      padding: 28px 0;
    }
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px;
    align-items: start;
  }
  .list {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 980px;
  }
</style>
