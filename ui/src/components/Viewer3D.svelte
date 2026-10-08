<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  A 3D model on screen: drag to turn it, scroll or pinch to zoom, double-click to
  start over. three.js loads the first time a model is opened, not with the app. -->
<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import Iris from "./Iris.svelte";

  type Mode = "textured" | "clay" | "wireframe";

  let {
    url,
    mode = "textured",
    spin = false,
  }: { url: string; mode?: Mode; spin?: boolean } = $props();

  let host = $state<HTMLDivElement>();
  let loaded = $state(0); // 0..1 while downloading
  let ready = $state(false);
  let error = $state("");

  // three.js objects live outside Svelte's reactivity.
  let three: typeof import("three") | null = null;
  let renderer: import("three").WebGLRenderer | null = null;
  let scene: import("three").Scene | null = null;
  let camera: import("three").PerspectiveCamera | null = null;
  let controls: import("three/addons/controls/OrbitControls.js").OrbitControls | null = null;
  let model: import("three").Object3D | null = null;
  let clay: import("three").Material | null = null;
  let wire: import("three").Material | null = null;
  const originals = new Map<import("three").Mesh, import("three").Material | import("three").Material[]>();
  let frame = 0;
  let resize: ResizeObserver | null = null;
  let disposed = false;
  let home = { pos: [0, 0, 0] as [number, number, number], target: [0, 0, 0] as [number, number, number] };

  function render() {
    if (renderer && scene && camera) renderer.render(scene, camera);
  }

  function loop() {
    frame = 0;
    if (!controls) return;
    controls.autoRotate = spin;
    const moving = controls.update();
    render();
    if (spin || moving) frame = requestAnimationFrame(loop);
  }
  function wake() {
    if (!frame) frame = requestAnimationFrame(loop);
  }

  function applyMode(m: Mode) {
    if (!model || !three) return;
    model.traverse((o) => {
      const mesh = o as import("three").Mesh;
      if (!mesh.isMesh) return;
      mesh.material = m === "textured" ? originals.get(mesh)! : m === "clay" ? clay! : wire!;
    });
    wake();
  }

  export function reset() {
    if (!camera || !controls) return;
    camera.position.set(...home.pos);
    controls.target.set(...home.target);
    wake();
  }

  onMount(async () => {
    try {
      const [T, { GLTFLoader }, { OrbitControls }, { RoomEnvironment }] = await Promise.all([
        import("three"),
        import("three/addons/loaders/GLTFLoader.js"),
        import("three/addons/controls/OrbitControls.js"),
        import("three/addons/environments/RoomEnvironment.js"),
      ]);
      if (disposed || !host) return;
      three = T;
      renderer = new T.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.outputColorSpace = T.SRGBColorSpace;
      renderer.toneMapping = T.ACESFilmicToneMapping;
      host.appendChild(renderer.domElement);
      scene = new T.Scene();
      const pmrem = new T.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      pmrem.dispose();
      const key = new T.DirectionalLight(0xffffff, 1.2);
      key.position.set(2, 4, 3);
      scene.add(key);
      camera = new T.PerspectiveCamera(35, 1, 0.01, 100);
      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.autoRotateSpeed = 1.6;
      controls.addEventListener("change", wake);
      clay = new T.MeshStandardMaterial({ color: 0xcfc8dc, roughness: 0.72, metalness: 0 });
      wire = new T.MeshBasicMaterial({ color: 0x9d8ff0, wireframe: true, transparent: true, opacity: 0.55 });

      const fit = () => {
        if (!host || !renderer || !camera) return;
        const { clientWidth: w, clientHeight: h } = host;
        renderer.setSize(w, h, false);
        camera.aspect = w / Math.max(1, h);
        camera.updateProjectionMatrix();
        wake();
      };
      resize = new ResizeObserver(fit);
      resize.observe(host);
      fit();

      const gltf = await new GLTFLoader().loadAsync(url, (e) => {
        if (e.total) loaded = e.loaded / e.total;
      });
      if (disposed) return;
      model = gltf.scene;
      model.traverse((o) => {
        const mesh = o as import("three").Mesh;
        if (mesh.isMesh) originals.set(mesh, mesh.material);
      });
      // Stand it on the floor, centred, and look at it from a three-quarter view.
      const box = new T.Box3().setFromObject(model);
      const size = box.getSize(new T.Vector3());
      const center = box.getCenter(new T.Vector3());
      model.position.set(-center.x, -box.min.y, -center.z);
      scene.add(model);
      const radius = size.length() / 2;
      const grid = new T.GridHelper(Math.max(size.x, size.z) * 2.4, 12, 0x5b4f86, 0x3a3150);
      (grid.material as import("three").Material).transparent = true;
      (grid.material as import("three").Material).opacity = 0.45;
      scene.add(grid);
      const distance = radius / Math.sin((camera.fov * Math.PI) / 360) * 1.05;
      const yaw = (35 * Math.PI) / 180;
      const pitch = (20 * Math.PI) / 180;
      const target: [number, number, number] = [0, size.y / 2, 0];
      home = {
        target,
        pos: [
          Math.sin(yaw) * Math.cos(pitch) * distance,
          target[1] + Math.sin(pitch) * distance,
          Math.cos(yaw) * Math.cos(pitch) * distance,
        ],
      };
      camera.near = distance / 100;
      camera.far = distance * 20;
      camera.updateProjectionMatrix();
      controls.minDistance = radius * 0.4;
      controls.maxDistance = distance * 4;
      reset();
      applyMode(mode);
      ready = true;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  });

  $effect(() => applyMode(mode));
  $effect(() => {
    void spin;
    wake();
  });

  onDestroy(() => {
    disposed = true;
    cancelAnimationFrame(frame);
    resize?.disconnect();
    controls?.dispose();
    const seen = new Set<unknown>();
    scene?.traverse((o) => {
      const mesh = o as import("three").Mesh;
      if (!mesh.isMesh && !(o as import("three").LineSegments).isLineSegments) return;
      mesh.geometry?.dispose();
      for (const m of [mesh.material, originals.get(mesh)].flat()) {
        if (!m || seen.has(m)) continue;
        seen.add(m);
        for (const value of Object.values(m)) {
          if (value && (value as import("three").Texture).isTexture) (value as import("three").Texture).dispose();
        }
        (m as import("three").Material).dispose();
      }
    });
    clay?.dispose();
    wire?.dispose();
    scene?.environment?.dispose();
    renderer?.dispose();
    renderer?.forceContextLoss();
    renderer?.domElement.remove();
  });
</script>

<div class="viewer" bind:this={host} ondblclick={reset} role="img" aria-label="3D model. Drag to turn it, scroll to zoom, double-click to reset.">
  {#if !ready && !error}
    <div class="loading">
      <Iris size={72} bars={40} active progress={loaded || null} />
      <span>{loaded ? `Loading the model · ${Math.round(loaded * 100)}%` : "Opening the viewer"}</span>
    </div>
  {/if}
  {#if error}
    <p class="error">The model could not be shown: {error}</p>
  {/if}
</div>

<style>
  .viewer {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 240px;
    touch-action: none;
    cursor: grab;
  }
  .viewer:active {
    cursor: grabbing;
  }
  .viewer :global(canvas) {
    display: block;
    width: 100%;
    height: 100%;
  }
  .loading {
    position: absolute;
    inset: 0;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 12px;
    color: var(--text-3);
    font-size: 13px;
  }
  .error {
    position: absolute;
    inset: 0;
    display: grid;
    place-content: center;
    color: var(--text-3);
    padding: 24px;
    text-align: center;
  }
</style>
