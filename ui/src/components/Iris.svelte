<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  The IrisEcho mark, alive. Bars pulse in a travelling wave while `active`;
  with a `progress` (0..1) the ring fills clockwise, so the mark doubles as the
  progress indicator everywhere work is happening. -->
<script lang="ts">
  let {
    size = 40,
    bars = 48,
    active = false,
    progress = null,
    catchlight = true,
    title = "",
  }: {
    size?: number;
    bars?: number;
    active?: boolean;
    progress?: number | null;
    catchlight?: boolean;
    title?: string;
  } = $props();

  const uid = `iris-${Math.random().toString(36).slice(2, 8)}`;
  const R0 = 20; // where the fibers start (just outside the pupil)
  const MIN = 12;
  const MAX = 27;

  function wave(i: number, n: number) {
    const t = (2 * Math.PI * i) / n;
    const v =
      0.58 + 0.07 * Math.sin(3 * t + 0.6) + 0.13 * Math.sin(8 * t + 1.9) + 0.12 * Math.sin(13 * t + 0.3) + 0.1 * Math.sin(31 * t + 2.2);
    return Math.min(1, Math.max(0, v));
  }

  const fibers = $derived(
    Array.from({ length: bars }, (_, i) => ({
      angle: (360 * i) / bars,
      len: MIN + wave(i, bars) * (MAX - MIN),
      delay: -((i / bars) * 1.6).toFixed(3),
    })),
  );
  const width = $derived(bars > 40 ? 2.2 : 3);
  const lit = $derived(progress == null ? bars : Math.round(Math.max(0, Math.min(1, progress)) * bars));
</script>

<svg
  class="iris"
  class:active
  width={size}
  height={size}
  viewBox="-50 -50 100 100"
  role={title ? "img" : "presentation"}
  aria-label={title || undefined}
>
  <defs>
    <radialGradient id={uid} cx="0" cy="0" r={R0 + MAX} gradientUnits="userSpaceOnUse">
      <stop offset={R0 / (R0 + MAX)} stop-color="var(--amber)" />
      <stop offset={(R0 + MAX * 0.45) / (R0 + MAX)} stop-color="var(--teal)" />
      <stop offset="1" stop-color="var(--violet)" />
    </radialGradient>
  </defs>
  <g fill={`url(#${uid})`}>
    {#each fibers as f, i (i)}
      <g transform={`rotate(${f.angle})`}>
        <rect
          class="fiber"
          class:dim={i >= lit}
          x={-width / 2}
          y={-(R0 + f.len)}
          width={width}
          height={f.len}
          rx={width / 2}
          style={`animation-delay:${f.delay}s`}
        />
      </g>
    {/each}
  </g>
  <circle r={R0 - 3} fill="var(--pupil)" />
  <circle r={R0 - 3} fill="none" stroke="var(--limbus)" stroke-width="1.6" />
  {#if catchlight}
    <circle cx={-(R0 - 3) * 0.36} cy={-(R0 - 3) * 0.38} r={(R0 - 3) * 0.2} fill="var(--sclera)" />
  {/if}
</svg>

<style>
  .iris {
    display: block;
    flex: none;
    overflow: visible;
  }
  .fiber {
    transform-box: fill-box;
    transform-origin: center bottom;
    transition: opacity 0.4s;
  }
  .fiber.dim {
    opacity: 0.16;
  }
  .active .fiber {
    animation: pulse 1.6s ease-in-out infinite;
  }
  @keyframes pulse {
    0%,
    100% {
      transform: scaleY(0.45);
    }
    50% {
      transform: scaleY(1.08);
    }
  }
</style>
