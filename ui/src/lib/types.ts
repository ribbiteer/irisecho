// SPDX-License-Identifier: AGPL-3.0-or-later

export type Kind = "image" | "edit" | "video" | "upscale" | "voice" | "clone" | "music" | "sfx";
export type ModelKind = Kind | "prompt"; // "prompt" is a helper, not a studio

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
  options: { voices?: VoiceOption[]; [key: string]: unknown };
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
  type: "image" | "audio" | "video" | "text";
  text?: string;
  width?: number;
  height?: number;
  duration?: number;
  seed?: number;
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
