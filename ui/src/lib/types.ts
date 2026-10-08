// SPDX-License-Identifier: AGPL-3.0-or-later

export type Kind = "image" | "edit" | "video" | "upscale" | "model3d" | "voice" | "clone" | "music" | "sfx";
// Helpers, not studios: "prompt" writes prompts, "views3d" makes the views Pixal3D builds from.
export type ModelKind = Kind | "prompt" | "views3d";

export interface License {
  id: string;
  name: string;
  url: string;
  commercial: boolean;
  accept: boolean;
  accepted: boolean;
  summary: string;
  condition: string;
  gated: boolean;
}

export interface FileStatus {
  id: string;
  state: "ready" | "linked" | "partial" | "missing";
  size: number;
  have: number;
  path: string;
}

export interface Download {
  id: string;
  size: number;
  state: "queued" | "downloading" | "verifying" | "done" | "error" | "needs_token" | "needs_access" | "cancelled";
  done: number;
  speed: number;
  error: string;
  page: string;
}

export interface VoiceOption {
  id: string;
  label: string;
  group: string;
}

export interface Model {
  id: string;
  name: string;
  kind: ModelKind;
  engine: string;
  summary: string;
  prompt_style: string | null;
  page: string;
  license: License;
  available: boolean;
  preview: boolean;
  ready: boolean;
  needs: ("unsupported" | "license" | "engine" | "download")[];
  size: number;
  have: number;
  files: FileStatus[];
  downloads: Download[];
  installing: boolean;
  options: { voices?: VoiceOption[]; video?: VideoOptions; model3d?: Model3dOptions; [key: string]: unknown };
}

export type VideoSize = "standard" | "large" | "720p";

/** What a video model takes and makes (registry settings.video). */
export interface VideoOptions {
  frames: ("start" | "end")[];
  needs_frame?: boolean;
  sizes: VideoSize[];
  fps: number;
  sound: boolean;
  /** Seconds of work per second of video on a 12 GB card, by size. */
  pace: Partial<Record<VideoSize, number>>;
}

/** What a 3D model builds from, and seconds of work on a 12 GB card by detail. */
export interface Model3dOptions {
  views: ("front" | "back")[];
  pace: { standard: number; high: number };
}

/** What IrisEcho measured on a finished 3D model (mesh3d.inspect). */
export interface MeshInfo {
  faces?: number;
  extents?: [number, number, number]; // x, y (up), z in model units
  textured?: boolean;
  watertight?: boolean;
  volume?: number;
  parts?: number;
  thickness?: number;
  printable?: boolean;
  reasons?: string[];
  error?: string;
}

export interface Engine {
  id: string;
  name: string;
  installed: boolean;
  supported: boolean;
  state: "idle" | "installing" | "starting" | "ready" | "busy" | "error";
  detail: string;
  installing?: boolean;
  resident?: boolean;
  log?: string;
}

export interface Output {
  path: string;
  type: "image" | "audio" | "video" | "text" | "model3d";
  text?: string;
  width?: number;
  height?: number;
  duration?: number;
  seed?: number;
  // 3D models
  preview?: string;
  mesh?: MeshInfo;
  resolution?: number;
  // views made for Pixal3D
  role?: "front" | "back" | "level";
  source?: "yours" | "made";
  matched?: boolean;
  mirror_iou?: number;
  similarity?: number;
}

export interface Job {
  id: string;
  model: string;
  kind: Kind;
  params: Record<string, any>;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";
  progress: number | null;
  message: string | null;
  error: string | null;
  created: number;
  started: number | null;
  finished: number | null;
  outputs: Output[];
  favorite: boolean;
}

export interface Gpu {
  name: string;
  vram_mb: number;
  driver: string;
  compute_cap: number;
}

export interface System {
  hardware: {
    system: string;
    machine: string;
    ram_mb: number;
    tier: string;
    backend: "cuda" | "mps" | "cpu";
    quant: string | null;
    cuda_tag: string | null;
    gpus: Gpu[];
    vram_mb: number;
  };
  vram: { used_mb: number; free_mb: number }[]; // live, whole card; empty without nvidia-smi
  queue: { current: string | null; waiting: number };
  resident: string | null;
  loaded_model: string | null;
  data_dir: string;
}

export interface Settings {
  models_dir: string;
  outputs_dir: string;
  linked_model_dirs: string[];
  accepted_licenses: string[];
  strip_metadata: boolean;
  lan_enabled: boolean;
  models_path: string;
  outputs_path: string;
}

export interface Bootstrap {
  version: string;
  remote: boolean;
  system: System;
  settings: Settings;
  engines: Engine[];
  models: Model[];
  jobs: Job[];
  downloads: Download[];
  scan?: { state: "scanning" | "idle" };
  guide?: { cli: string; path: string };
  credentials: { huggingface: boolean; keychain: boolean };
}

export interface PairedDevice {
  id: string;
  name: string;
  created: number;
  last_seen: number;
}

export interface LanStatus {
  enabled: boolean;
  active: boolean;
  port: number | null;
  error: string;
  addresses: string[];
  urls: string[];
  devices: PairedDevice[];
}

export interface PairCode {
  code: string;
  expires_in: number;
  url: string;
  address: string;
  qr: string; // an SVG made by the core
}
