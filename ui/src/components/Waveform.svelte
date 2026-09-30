<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  A player drawn as vertical bars, like the fibers of the mark: played bars
  take the fiber gradient, the rest stay dim. Click or drag to seek. -->
<script lang="ts" module>
  // Only one clip plays at a time across the whole app.
  let playing: HTMLAudioElement | null = null;
  const peakCache = new Map<string, number[]>();
</script>

<script lang="ts">
  import Pause from "@lucide/svelte/icons/pause";
  import Play from "@lucide/svelte/icons/play";
  import { onMount } from "svelte";
  import { clock } from "../lib/format";

  let {
    src,
    bars = 96,
    height = 44,
    duration = 0,
  }: { src: string; bars?: number; height?: number; duration?: number } = $props();

  let audio: HTMLAudioElement;
  let peaks = $state<number[]>([]);
  let current = $state(0);
  let total = $state(0);
  let isPlaying = $state(false);
  const uid = `wf-${Math.random().toString(36).slice(2, 8)}`;

  onMount(() => {
    const cached = peakCache.get(src);
    if (cached) {
      peaks = cached;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const buf = await (await fetch(src, { credentials: "same-origin" })).arrayBuffer();
        const ctx = new AudioContext();
        const decoded = await ctx.decodeAudioData(buf);
        ctx.close();
        const data = decoded.getChannelData(0);
        const step = Math.max(1, Math.floor(data.length / bars));
        const out: number[] = [];
        for (let i = 0; i < bars; i++) {
          let sum = 0;
          const start = i * step;
          const end = Math.min(data.length, start + step);
          for (let j = start; j < end; j++) sum += data[j] * data[j];
          out.push(Math.sqrt(sum / Math.max(1, end - start)));
        }
        const max = Math.max(...out, 1e-6);
        const norm = out.map((v) => Math.max(0.06, Math.pow(v / max, 0.7)));
        peakCache.set(src, norm);
        if (!cancelled) peaks = norm;
      } catch {
        if (!cancelled) peaks = Array(bars).fill(0.2);
      }
    })();
    return () => {
      cancelled = true;
    };
  });

  function toggle() {
    if (audio.paused) {
      if (playing && playing !== audio) playing.pause();
      playing = audio;
      audio.play();
    } else {
      audio.pause();
    }
  }

  function seek(e: PointerEvent) {
    const el = e.currentTarget as HTMLElement;
    const rect = el.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    if (total) audio.currentTime = frac * total;
    if (audio.paused) toggle();
  }

  const played = $derived(total ? current / total : 0);
  const gap = 1.6;
</script>

<div class="wave">
  <button class="play" onclick={toggle} aria-label={isPlaying ? "Pause" : "Play"}>
    {#if isPlaying}<Pause size={16} fill="currentColor" />{:else}<Play size={16} fill="currentColor" />{/if}
  </button>
  <div class="bars" style:height={`${height}px`} role="slider" tabindex="0" aria-label="Position" aria-valuenow={Math.round(current)} aria-valuemin={0} aria-valuemax={Math.round(total)} onpointerdown={seek}
    onkeydown={(e) => {
      if (e.key === "ArrowRight") audio.currentTime = Math.min(total, audio.currentTime + 2);
      if (e.key === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 2);
      if (e.key === " ") { e.preventDefault(); toggle(); }
    }}>
    <svg width="100%" height="100%" viewBox={`0 0 ${bars * 4} 100`} preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id={uid} x1="0" x2="0" y1="1" y2="0">
          <stop offset="0" stop-color="var(--amber)" />
          <stop offset="0.5" stop-color="var(--teal)" />
          <stop offset="1" stop-color="var(--violet)" />
        </linearGradient>
      </defs>
      {#each peaks as p, i (i)}
        <rect
          x={i * 4 + gap / 2}
          y={50 - p * 48}
          width={4 - gap}
          height={p * 96}
          rx="1.2"
          fill={i / peaks.length < played ? `url(#${uid})` : "rgb(243 240 250 / 0.2)"}
        />
      {/each}
    </svg>
  </div>
  <span class="time mono">{clock(isPlaying || current ? current : total || duration)}</span>
  <audio
    bind:this={audio}
    {src}
    preload="metadata"
    bind:currentTime={current}
    bind:duration={total}
    onplay={() => (isPlaying = true)}
    onpause={() => (isPlaying = false)}
    onended={() => {
      isPlaying = false;
      current = 0;
    }}
  ></audio>
</div>

<style>
  .wave {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }
  .play {
    width: 38px;
    height: 38px;
    flex: none;
    border-radius: 50%;
    border: 0;
    background: var(--accent);
    color: #fff;
    display: grid;
    place-items: center;
    cursor: pointer;
    transition: transform 0.1s;
  }
  .play:active {
    transform: scale(0.94);
  }
  .bars {
    flex: 1;
    min-width: 0;
    cursor: pointer;
  }
  .bars svg {
    display: block;
  }
  .time {
    font-size: 12px;
    color: var(--text-3);
    min-width: 36px;
    text-align: right;
  }
</style>
