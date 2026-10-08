<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
  import AudioLines from "@lucide/svelte/icons/audio-lines";
  import Box from "@lucide/svelte/icons/box";
  import Clapperboard from "@lucide/svelte/icons/clapperboard";
  import ImageIcon from "@lucide/svelte/icons/image";
  import Maximize from "@lucide/svelte/icons/maximize-2";
  import MicVocal from "@lucide/svelte/icons/mic-vocal";
  import Music from "@lucide/svelte/icons/music-4";
  import Wand from "@lucide/svelte/icons/wand-sparkles";
  import Zap from "@lucide/svelte/icons/zap";
  import Composer from "../components/Composer.svelte";
  import Feed from "../components/Feed.svelte";
  import { STUDIOS } from "../lib/studios";
  import { app } from "../lib/state.svelte";
  import type { Kind } from "../lib/types";

  const ICONS: Record<Kind, typeof ImageIcon> = {
    image: ImageIcon,
    edit: Wand,
    video: Clapperboard,
    upscale: Maximize,
    model3d: Box,
    voice: AudioLines,
    clone: MicVocal,
    music: Music,
    sfx: Zap,
  };

  const studios = $derived(STUDIOS.filter((s) => app.models.some((m) => m.kind === s.kind)));
  const current = $derived(studios.find((s) => s.kind === app.studio) ?? studios[0]);
  const firstSound = $derived(studios.findIndex((s) => s.sound));
</script>

<div class="create" class:sound={current?.sound}>
  <nav class="rail" aria-label="Studios">
    {#each studios as s, i (s.kind)}
      {#if i === firstSound && i > 0}<span class="sep" aria-hidden="true"></span>{/if}
      {@const Icon = ICONS[s.kind]}
      <button
        class="studio"
        class:sound={s.sound}
        aria-current={current?.kind === s.kind ? "page" : undefined}
        onclick={() => (app.studio = s.kind)}
        title={s.hint}
      >
        <span class="glyph"><Icon size={19} strokeWidth={1.8} /></span>
        <span class="name">{s.label}</span>
      </button>
    {/each}
  </nav>

  {#if current}
    {#key current.kind}
      <Composer studio={current} />
      <Feed studio={current} />
    {/key}
  {/if}
</div>

<style>
  .create {
    height: 100%;
    display: grid;
    grid-template-columns: 84px minmax(360px, 420px) 1fr;
    min-height: 0;
  }
  .rail {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 14px 0;
    border-right: 1px solid var(--line);
    overflow-y: auto;
  }
  .sep {
    width: 28px;
    height: 1px;
    background: var(--line-2);
    margin: 8px 0;
  }
  .studio {
    --accent: var(--violet);
    --accent-ink: var(--violet-ink);
    width: 68px;
    padding: 9px 0 7px;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--text-3);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 5px;
    cursor: pointer;
    transition:
      color 0.15s,
      background 0.15s;
  }
  .studio.sound {
    --accent: var(--teal);
    --accent-ink: var(--teal-ink);
  }
  .studio:hover {
    color: var(--text);
    background: var(--surface-1);
  }
  .glyph {
    width: 38px;
    height: 32px;
    display: grid;
    place-items: center;
    border-radius: 10px;
    transition: background 0.2s;
  }
  .studio[aria-current="page"] {
    color: var(--text);
  }
  .studio[aria-current="page"] .glyph {
    background: color-mix(in oklab, var(--accent) 26%, transparent);
    color: var(--accent-ink);
  }
  .name {
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.01em;
  }

  /* Phones: studios are tabs across the top, then the composer, then the results. */
  @media (max-width: 820px) {
    .create {
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
    }
    .rail {
      flex: none;
      flex-direction: row;
      justify-content: flex-start;
      align-items: stretch;
      gap: 2px;
      padding: 6px 8px;
      border-right: 0;
      border-bottom: 1px solid var(--line);
      overflow-x: auto;
      overflow-y: hidden;
      position: sticky;
      top: 0;
      z-index: 5;
      background: var(--bg);
    }
    .studio {
      width: auto;
      min-width: 62px;
      padding: 6px 8px 5px;
    }
    .sep {
      width: 1px;
      height: 32px;
      margin: 4px 6px;
      align-self: center;
    }
  }
</style>
