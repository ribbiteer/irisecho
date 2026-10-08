// SPDX-License-Identifier: AGPL-3.0-or-later
// Application state, kept current by the core's event stream.

import { api, ApiError, connectEvents } from "./api";
import type { Bootstrap, Download, Engine, Job, Kind, Model, Settings, System } from "./types";

export type View = "create" | "library" | "models" | "settings";

interface Toast {
  id: number;
  text: string;
  tone: "info" | "ok" | "error";
}

export const app = $state({
  loaded: false,
  connected: false,
  failed: "",
  version: "",
  // True on a paired phone or other device: no models, settings or folders here.
  remote: false,
  system: null as System | null,
  settings: null as Settings | null,
  engines: [] as Engine[],
  models: [] as Model[],
  jobs: [] as Job[],
  downloads: {} as Record<string, Download>,
  credentials: { huggingface: false, keychain: true },
  // The command line of this installation and its guide for scripts and agents.
  guide: { cli: "", path: "" },
  engineLog: {} as Record<string, string>,
  scan: null as null | {
    state?: string;
    file?: string;
    done?: number;
    total?: number;
    overall_done?: number;
    overall_total?: number;
    found?: number;
    error?: string;
  },
  view: "create" as View,
  studio: "image" as Kind,
  chosen: {} as Record<string, string>, // kind -> model id
  lightbox: null as null | { job: Job; index: number },
  toasts: [] as Toast[],
  // A job to prefill the composer with ("use these settings again").
  reuse: null as null | Job,
  // What the Library is showing; mirrored in the address so a search is a link.
  library: { q: "", kind: "all" as Kind | "all", model: "", favorites: false, since: "" },
});

let toastId = 0;
export function toast(text: string, tone: Toast["tone"] = "info", ms = 4200) {
  if (app.toasts.some((t) => t.text === text)) return; // already saying exactly this
  const id = ++toastId;
  app.toasts.push({ id, text, tone });
  setTimeout(() => {
    app.toasts = app.toasts.filter((t) => t.id !== id);
  }, ms);
}

export function fail(e: unknown) {
  toast(e instanceof Error ? e.message : String(e), "error", 6000);
}

export async function refresh() {
  try {
    const b = await api<Bootstrap>("/bootstrap");
    app.version = b.version;
    app.remote = b.remote;
    if (b.remote && (app.view === "models" || app.view === "settings")) app.view = "create";
    app.system = b.system;
    app.settings = b.settings;
    app.engines = b.engines;
    app.models = b.models;
    app.jobs = b.jobs;
    app.credentials = b.credentials;
    app.guide = b.guide ?? { cli: "", path: "" };
    app.downloads = Object.fromEntries(b.downloads.map((d) => [d.id, d]));
    // A scan that began before this page loaded, or ended while it was disconnected.
    const scanning = b.scan?.state === "scanning";
    if (scanning !== (app.scan?.state === "scanning")) app.scan = scanning ? { state: "scanning" } : null;
    app.loaded = true;
    app.failed = "";
  } catch (e) {
    app.failed = e instanceof ApiError && e.status === 401 ? "reload" : (e as Error).message;
  }
}

let modelsTimer: number | undefined;
export function refreshModels(delay = 0) {
  window.clearTimeout(modelsTimer);
  modelsTimer = window.setTimeout(async () => {
    try {
      app.models = await api<Model[]>("/models");
    } catch {
      /* the next event will retry */
    }
  }, delay);
}

async function refreshEngines() {
  try {
    app.engines = await api<Engine[]>("/engines");
    app.system = await api<System>("/system");
  } catch {
    /* ignore */
  }
}

// Updates can arrive out of order (a submit response after the websocket has
// already said the job is running), so a job's status never moves backwards.
const RANK: Record<Job["status"], number> = { queued: 0, running: 1, done: 2, failed: 2, cancelled: 2, interrupted: 2 };

function upsertJob(job: Job) {
  // Working jobs (prompt writing, views for 3D) never appear in the feed or library.
  if ((job.kind as string) === "prompt" || (job.kind as string) === "views3d") return;
  const i = app.jobs.findIndex((j) => j.id === job.id);
  if (i >= 0 && RANK[app.jobs[i].status] > RANK[job.status]) return;
  if (i >= 0) app.jobs[i] = job;
  else app.jobs = [job, ...app.jobs].sort((a, b) => b.created - a.created);
  if (app.lightbox?.job.id === job.id) app.lightbox.job = job;
  if (app.system) {
    app.system.queue.current = job.status === "running" ? job.id : app.system.queue.current === job.id ? null : app.system.queue.current;
  }
}

function onEvent(e: { type: string; data: any }) {
  switch (e.type) {
    case "job": {
      const prev = app.jobs.find((j) => j.id === e.data.id);
      upsertJob(e.data);
      if (prev && prev.status !== e.data.status) {
        if (e.data.status === "failed") toast(e.data.error ?? "That did not work.", "error", 7000);
        refreshEngines();
      }
      break;
    }
    case "job.deleted":
      app.jobs = app.jobs.filter((j) => j.id !== e.data.id);
      break;
    case "download": {
      const prev = app.downloads[e.data.id];
      app.downloads[e.data.id] = e.data;
      if (!prev || prev.state !== e.data.state) refreshModels(150);
      break;
    }
    case "engine": {
      const i = app.engines.findIndex((x) => x.id === e.data.id);
      if (i >= 0) app.engines[i] = { ...app.engines[i], ...e.data };
      if (e.data.log) app.engineLog[e.data.id] = e.data.log;
      if (!e.data.log) refreshModels(100);
      break;
    }
    case "engines":
      refreshEngines();
      break;
    case "models":
      refreshModels(100);
      break;
    case "scan":
      // A new scan starts from nothing; everything after adds to what is known about it.
      app.scan = e.data.state === "scanning" ? e.data : { ...(app.scan ?? {}), ...e.data };
      if (e.data.state === "done") {
        if (e.data.error) toast(`Checking your folders stopped early: ${e.data.error}`, "error", 7000);
        else if (e.data.found) toast(`Found ${e.data.found} model file${e.data.found === 1 ? "" : "s"} you already had.`, "ok");
        else if (app.settings?.linked_model_dirs.length) toast("No new model files found in your folders.");
      }
      break;
  }
}

// Views and studios are mirrored in the address (#/create/voice, #/library, ...)
// so the back button works and every screen has a link.
const VIEWS: View[] = ["create", "library", "models", "settings"];

function readHash() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [view, studio] = path.split("/");
  if (VIEWS.includes(view as View) && !(app.remote && (view === "models" || view === "settings"))) {
    app.view = view as View;
  }
  if (view === "create" && studio) app.studio = studio as Kind;
  if (view === "library") {
    const p = new URLSearchParams(query);
    Object.assign(app.library, {
      q: p.get("q") ?? "",
      kind: (p.get("studio") as Kind | null) ?? "all",
      model: p.get("model") ?? "",
      favorites: p.get("fav") === "1",
      since: p.get("since") ?? "",
    });
  }
}

function libraryQuery(): string {
  const l = app.library;
  const p = new URLSearchParams();
  if (l.q.trim()) p.set("q", l.q.trim());
  if (l.kind !== "all") p.set("studio", l.kind);
  if (l.model) p.set("model", l.model);
  if (l.favorites) p.set("fav", "1");
  if (l.since) p.set("since", l.since);
  const text = p.toString();
  return text ? `?${text}` : "";
}

function writeHash() {
  const next = app.view === "create" ? `#/create/${app.studio}` : app.view === "library" ? `#/library${libraryQuery()}` : `#/${app.view}`;
  if (location.hash === next) return;
  // Typing in the search box should not fill the back button's history.
  const stay = app.view === "library" && location.hash.startsWith("#/library");
  if (stay) history.replaceState(null, "", next);
  else history.pushState(null, "", next);
}

export function start() {
  readHash();
  window.addEventListener("popstate", readHash);
  $effect.root(() => {
    $effect(() => {
      void app.view;
      void app.studio;
      void libraryQuery();
      writeHash();
    });
  });
  refresh();
  let first = true;
  connectEvents(onEvent, (connected) => {
    app.connected = connected;
    // After a reconnect, catch up on anything missed.
    if (connected && !first) refresh();
    first = false;
  });
}

// --- derived helpers ---------------------------------------------------------

export const modelsOf = (kind: Kind) => app.models.filter((m) => m.kind === kind);

export function chosenModel(kind: Kind): Model | undefined {
  const list = modelsOf(kind).filter((m) => m.available);
  return list.find((m) => m.id === app.chosen[kind]) ?? list.find((m) => m.ready) ?? list[0];
}

export const modelById = (id: string) => app.models.find((m) => m.id === id);
export const engineById = (id: string) => app.engines.find((e) => e.id === id);

export function modelProgress(m: Model): { have: number; total: number; speed: number; active: boolean; blocked?: Download } {
  let have = 0;
  let speed = 0;
  let active = false;
  let blocked: Download | undefined;
  for (const f of m.files) {
    const d = app.downloads[f.id];
    if (f.state === "ready" || f.state === "linked") {
      have += f.size;
    } else if (d) {
      have += d.state === "done" ? d.size : d.done;
      speed += d.speed;
      if (["queued", "downloading", "verifying"].includes(d.state)) active = true;
      if (["error", "needs_token", "needs_access"].includes(d.state)) blocked = d;
    } else {
      have += f.have;
    }
  }
  return { have, total: m.size, speed, active, blocked };
}

export async function submit(model: Model, params: Record<string, unknown>): Promise<Job | null> {
  try {
    const job = await api<Job>("/jobs", { body: { model: model.id, params } });
    upsertJob(job);
    return job;
  } catch (e) {
    fail(e);
    return null;
  }
}

/** Follow a job that is not shown in the feed until it ends; resolves with the finished job. */
export async function followJob(job: Job, onStatus: (message: string, progress: number | null) => void): Promise<Job> {
  for (;;) {
    if (job.status === "done") return job;
    if (job.status === "failed") throw new Error(job.error ?? "That did not work.");
    if (job.status === "cancelled" || job.status === "interrupted") throw new Error("Cancelled.");
    onStatus(job.status === "queued" ? "Waiting for the graphics card" : (job.message ?? "Working"), job.progress);
    await new Promise((r) => setTimeout(r, 400));
    job = await api<Job>(`/jobs/${job.id}`);
  }
}

// --- prompt writer -----------------------------------------------------------

export const promptWriter = () => app.models.find((m) => m.id === "prompt-writer");

export interface WriteRequest {
  mode: "improve" | "describe" | "improve_image";
  target: string; // the model the prompt is for
  text?: string;
  images?: string[];
}

// Runs a prompt-writer job and resolves with the text. The job goes through the
// same queue as everything else, so it waits for the graphics card like any job.
export async function writePrompt(req: WriteRequest, onStatus: (message: string) => void): Promise<string> {
  let job = await api<Job>("/jobs", {
    body: { model: "prompt-writer", params: { mode: req.mode, target: req.target, text: req.text ?? "", images: req.images ?? [] } },
  });
  for (;;) {
    if (job.status === "done") return job.outputs[0]?.text ?? "";
    if (job.status === "failed") throw new Error(job.error ?? "The prompt writer failed.");
    if (job.status === "cancelled" || job.status === "interrupted") throw new Error("Cancelled.");
    onStatus(job.status === "queued" ? "Waiting for the graphics card" : (job.message ?? "Writing"));
    await new Promise((r) => setTimeout(r, 400));
    job = await api<Job>(`/jobs/${job.id}`);
  }
}
