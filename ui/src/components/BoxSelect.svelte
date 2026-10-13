<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Drag a rectangle on a picture; the box is [left, top, right, bottom] as fractions. -->
<script lang="ts">
  let { src, box = $bindable(null) }: { src: string; box?: number[] | null } = $props();

  let frame: HTMLDivElement | undefined = $state();
  let start: [number, number] | null = null;

  function at(e: PointerEvent): [number, number] {
    const r = frame!.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    return [x, y];
  }
  function down(e: PointerEvent) {
    e.preventDefault();
    frame!.setPointerCapture(e.pointerId);
    start = at(e);
    box = null;
  }
  function move(e: PointerEvent) {
    if (!start) return;
    const [x, y] = at(e);
    const r = (v: number) => Math.round(v * 1000) / 1000;
    box = [r(Math.min(start[0], x)), r(Math.min(start[1], y)), r(Math.max(start[0], x)), r(Math.max(start[1], y))];
  }
  function up() {
    start = null;
    if (box && (box[2] - box[0] < 0.02 || box[3] - box[1] < 0.02)) box = null; // a tap, not a box
  }
</script>

<div
  class="frame"
  bind:this={frame}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  role="application"
  aria-label="Drag a rectangle over the area to change"
>
  <img {src} alt="" draggable="false" />
  {#if box}
    <div
      class="box"
      style:left={`${box[0] * 100}%`}
      style:top={`${box[1] * 100}%`}
      style:width={`${(box[2] - box[0]) * 100}%`}
      style:height={`${(box[3] - box[1]) * 100}%`}
    ></div>
  {/if}
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
  .box {
    position: absolute;
    border: 2px solid var(--amber);
    background: rgb(242 165 65 / 0.18);
    box-shadow: 0 0 0 9999px rgb(14 11 22 / 0.45);
  }
</style>
