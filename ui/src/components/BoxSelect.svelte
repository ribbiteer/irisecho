<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Drag rectangles on a picture; each box is [left, top, right, bottom] as fractions.
  With `colours`, each box is shown in its colour with the circle the model will see
  drawn through its corners (Qwen-Image 2.1 reads coloured circles as "edit here"). -->
<script lang="ts">
  import X from "@lucide/svelte/icons/x";

  let {
    src,
    boxes = $bindable([]),
    max = 1,
    colours = [],
  }: { src: string; boxes?: number[][]; max?: number; colours?: { name: string; css: string }[] } = $props();

  let frame: HTMLDivElement | undefined = $state();
  let start: [number, number] | null = null;
  // The box being drawn, apart from the kept ones until it is big enough to keep.
  let drawing = $state<number[] | null>(null);
  let ratio = $state(1); // width / height of the shown picture
  const maskId = `areas-${Math.random().toString(36).slice(2, 8)}`;

  function at(e: PointerEvent): [number, number] {
    const r = frame!.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    return [x, y];
  }
  function down(e: PointerEvent) {
    if ((e.target as HTMLElement).closest("button")) return;
    e.preventDefault();
    frame!.setPointerCapture(e.pointerId);
    start = at(e);
    drawing = null;
  }
  function move(e: PointerEvent) {
    if (!start) return;
    const [x, y] = at(e);
    const r = (v: number) => Math.round(v * 1000) / 1000;
    drawing = [r(Math.min(start[0], x)), r(Math.min(start[1], y)), r(Math.max(start[0], x)), r(Math.max(start[1], y))];
  }
  function up() {
    start = null;
    const b = drawing;
    drawing = null;
    if (!b || b[2] - b[0] < 0.02 || b[3] - b[1] < 0.02) return; // a tap, not a box
    // One area: a new box replaces it. Several: a new box joins until the last colour,
    // then replaces the newest.
    boxes = max <= 1 ? [b] : boxes.length < max ? [...boxes, b] : [...boxes.slice(0, max - 1), b];
  }
  function remove(i: number) {
    boxes = boxes.filter((_, j) => j !== i);
  }
  const colour = (i: number) => colours[i]?.css ?? "var(--amber)";
  const shown = $derived(drawing ? [...boxes, drawing].slice(-Math.max(max, 1)) : boxes);
</script>

<div
  class="frame"
  bind:this={frame}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  role="application"
  aria-label="Drag a rectangle over each area to change"
>
  <img
    {src}
    alt=""
    draggable="false"
    onload={(e) => {
      const im = e.currentTarget as HTMLImageElement;
      ratio = im.naturalWidth / Math.max(1, im.naturalHeight);
    }}
  />
  <!-- Everything outside the areas dims; the areas stay clear. -->
  <svg class="veil" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
    <defs>
      <mask id={maskId} maskContentUnits="userSpaceOnUse">
        <rect width="1" height="1" fill="white" />
        {#each shown as b, i (i)}
          <rect x={b[0]} y={b[1]} width={b[2] - b[0]} height={b[3] - b[1]} fill="black" />
        {/each}
      </mask>
    </defs>
    {#if shown.length}
      <rect width="1" height="1" fill="rgb(14 11 22 / 0.45)" mask="url(#{maskId})" />
    {/if}
  </svg>
  {#if colours.length}
    <!-- The circle the model is shown: through the box corners, in the box's colour. -->
    <svg class="rings" viewBox="0 0 {ratio} 1" preserveAspectRatio="none" aria-hidden="true">
      {#each shown as b, i (i)}
        <ellipse
          cx={((b[0] + b[2]) / 2) * ratio}
          cy={(b[1] + b[3]) / 2}
          rx={((b[2] - b[0]) / 2) * ratio * Math.SQRT2}
          ry={((b[3] - b[1]) / 2) * Math.SQRT2}
          fill="none"
          stroke={colour(i)}
          stroke-width="2.5"
          stroke-dasharray="6 5"
          vector-effect="non-scaling-stroke"
        />
      {/each}
    </svg>
  {/if}
  {#each shown as b, i (i)}
    <div
      class="box"
      style:left={`${b[0] * 100}%`}
      style:top={`${b[1] * 100}%`}
      style:width={`${(b[2] - b[0]) * 100}%`}
      style:height={`${(b[3] - b[1]) * 100}%`}
      style:--c={colour(i)}
    >
      {#if i < boxes.length}
        <span class="chip-label" class:inside={b[1] < 0.08}>
          {colours[i]?.name ?? "Area"}
          <button class="x" title="Remove this area" aria-label="Remove this area" onclick={() => remove(i)}>
            <X size={12} />
          </button>
        </span>
      {/if}
    </div>
  {/each}
</div>

<style>
  .frame {
    position: relative;
    touch-action: none;
    user-select: none;
    cursor: crosshair;
    border-radius: var(--r-1);
    overflow: hidden;
    border: 1px solid var(--line-2);
  }
  img {
    display: block;
    width: 100%;
    height: auto;
    pointer-events: none;
  }
  .veil,
  .rings {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  .box {
    position: absolute;
    border: 2px solid var(--c);
    background: color-mix(in srgb, var(--c) 14%, transparent);
  }
  .chip-label {
    position: absolute;
    left: -2px;
    top: -2px;
    transform: translateY(-100%);
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 4px 2px 8px;
    border-radius: 6px 6px 6px 0;
    background: var(--c);
    color: #fff;
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
  }
  .chip-label.inside {
    /* a box at the top edge shows its label inside, where the frame does not cut it */
    transform: none;
    border-radius: 0 0 6px 0;
  }
  .x {
    display: inline-grid;
    place-items: center;
    width: 18px;
    height: 18px;
    border: 0;
    border-radius: 4px;
    background: rgb(0 0 0 / 0.25);
    color: #fff;
    cursor: pointer;
    padding: 0;
  }
  .x:hover {
    background: rgb(0 0 0 / 0.45);
  }
</style>
