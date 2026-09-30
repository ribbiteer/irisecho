<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import Iris from "./components/Iris.svelte";
  import Lightbox from "./components/Lightbox.svelte";
  import Toasts from "./components/Toasts.svelte";
  import TopBar from "./components/TopBar.svelte";
  import CreateView from "./views/CreateView.svelte";
  import LibraryView from "./views/LibraryView.svelte";
  import ModelsView from "./views/ModelsView.svelte";
  import SettingsView from "./views/SettingsView.svelte";
  import { app } from "./lib/state.svelte";
</script>

{#if !app.loaded}
  <div class="splash">
    <Iris size={96} active={!app.failed} />
    {#if app.failed === "reload"}
      <p>This window lost its connection to IrisEcho.</p>
      <button class="btn" onclick={() => location.reload()}>Reconnect</button>
    {:else if app.failed}
      <p>{app.failed}</p>
      <button class="btn" onclick={() => location.reload()}>Try again</button>
    {/if}
  </div>
{:else}
  <div class="shell">
    <TopBar />
    <main>
      {#if app.view === "create"}
        <CreateView />
      {:else if app.view === "library"}
        <LibraryView />
      {:else if app.view === "models"}
        <ModelsView />
      {:else}
        <SettingsView />
      {/if}
    </main>
  </div>
  {#if !app.connected}
    <div class="offline" role="status">Reconnecting to IrisEcho…</div>
  {/if}
  {#if app.lightbox}
    <Lightbox />
  {/if}
{/if}
<Toasts />

<style>
  .splash {
    height: 100%;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 18px;
    color: var(--text-2);
  }
  .shell {
    height: 100%;
    display: grid;
    grid-template-rows: auto 1fr;
  }
  main {
    min-height: 0;
    overflow: hidden;
  }
  .offline {
    position: fixed;
    left: 50%;
    top: 64px;
    transform: translateX(-50%);
    background: var(--surface-3);
    border: 1px solid var(--line-2);
    padding: 8px 14px;
    border-radius: 99px;
    font-size: 13px;
    color: var(--amber-ink);
    box-shadow: var(--shadow-2);
    z-index: 50;
  }
</style>
