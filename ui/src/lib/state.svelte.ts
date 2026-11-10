// SPDX-License-Identifier: AGPL-3.0-or-later
// Application state, kept current by the core's event stream.

import { api, ApiError, connectEvents } from "./api";
import { PendingQueue } from "./pending";
import { pollJob } from "./poll";
import type { Bootstrap, ControlValue, Download, Engine, Job, Kind, Model, Settings, System } from "./types";

export type View = "create" | "library" | "models" | "settings";

interface Toast {
  id: number;
  text: string;
  tone: "info" | "ok" | "error";
  action?: { label: string; run: () => void };
}

export const app = $state({
  loaded: false,
  connected: false,
  // This device was removed from the paired list: it will not get back in by reconnecting.
  unpaired: false,
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
  // Counts jobs taken away (here or by an event), so lists kept elsewhere know to look again.
  removals: 0,
  // The jobs the Library is showing, so the lightbox moves through those and not only the newest.
  shown: null as null | Job[],
  credentials: { huggingface: false, huggingface_source: null as "environment" | "saved" | null, keychain: true },
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
  // Advanced control values per model for this session (only the changed ones).
  advanced: {} as Record<string, Record<string, ControlValue>>,
  // A form's "More controls" section opens by itself after a reuse brings values back.
  advancedOpen: false,
  // Saved presets: model id -> preset name -> control values (presets.json in the data folder).
  presets: null as null | Record<string, Record<string, Record<string, ControlValue>>>,
  // What the Library is showing; mirrored in the address so a search is a link.
  library: { q: "", kind: "all" as Kind | "all", model: "", favorites: false, since: "" },
});

let toastId = 0;
export function toast(text: string, tone: Toast["tone"] = "info", ms = 4200, action?: Toast["action"]): number {
  // A message with a button is never merged with another: each Undo belongs to its own delete.
  const same = action ? undefined : app.toasts.find((t) => t.text === text && !t.action);
  if (same) return same.id; // already saying exactly this
  const id = ++toastId;
  app.toasts.push({ id, text, tone, action });
  setTimeout(() => dismissToast(id), ms);
  return id;
}

export function dismissToast(id: number) {
  app.toasts = app.toasts.filter((t) => t.id !== id);
}

export function fail(e: unknown) {
  toast(e instanceof Error ? e.message : String(e), "error", 6000);
}

// When an event last spoke of each job: a snapshot fetched before that is older than the event.
const eventAt = new Map<string, number>();
const BOOTSTRAP_JOBS = 80; // what the core sends in the snapshot

export async function refresh() {
  const asked = Date.now();
  try {
    const b = await api<Bootstrap>("/bootstrap");
    app.version = b.version;
    app.remote = b.remote;
    if (b.remote && (app.view === "models" || app.view === "settings")) app.view = "create";
    app.system = b.system;
    app.settings = b.settings;
    app.engines = b.engines;
    app.models = b.models;
    // Merged, not replaced: an event that arrived while this was on its way is newer than it.
    mergeSnapshot(b.jobs, asked);
    app.credentials = { huggingface_source: null, ...b.credentials };
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

function mergeSnapshot(snapshot: Job[], asked: number) {
  const inSnapshot = new Set(snapshot.map((j) => j.id));
  for (const job of snapshot) {
    if (gone.has(job.id) || (eventAt.get(job.id) ?? 0) >= asked) continue;
    upsertJob(job);
  }
  // A finished job the snapshot lacks (where it would reach back that far) was deleted meanwhile.
  const oldest = snapshot.length >= BOOTSTRAP_JOBS ? Math.min(...snapshot.map((j) => j.created)) : 0;
  const lost = app.jobs.filter(
    (j) => !inSnapshot.has(j.id) && j.created >= oldest && RANK[j.status] === 2 && (eventAt.get(j.id) ?? 0) < asked,
  );
  if (lost.length) {
    const ids = new Set(lost.map((j) => j.id));
    app.jobs = app.jobs.filter((j) => !ids.has(j.id));
    app.removals++;
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

// --- deleting jobs -----------------------------------------------------------
// A delete hides the job at once and shows an Undo; the request goes out when the
// toast has run out, or when the page is being left, so nothing is lost either way.

export const UNDO_MS = 6000;
// One Undo for every delete still waiting: deleting several in a row neither stacks
// toasts over the page nor sends the first ones while the last can still be undone.
let undoToast: number | null = null;

function dropUndoToast() {
  if (undoToast != null) dismissToast(undoToast);
  undoToast = null;
}

/** Jobs taken out of view on this page; a list fetched earlier must not bring them back. */
export const gone = new Set<string>();

/** Drops a job from every list on the page. */
function takeOutOfView(id: string) {
  gone.add(id);
  app.jobs = app.jobs.filter((j) => j.id !== id);
  if (app.lightbox?.job.id === id) app.lightbox = null;
  app.removals++;
}

/** Stops waiting on a delete without sending it (it happened elsewhere, or it was undone). */
function forget(id: string) {
  pendingDeletes.undo(id);
  if (!pendingDeletes.ids().length) dropUndoToast();
}

async function sendDelete(id: string, job: Job, keepalive = false) {
  try {
    await api(`/jobs/${id}`, { method: "DELETE", keepalive });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return; // already gone
    // Kept, for example because a file is open: bring it back and say why.
    gone.delete(id);
    upsertJob({ ...job });
    fail(e);
  }
  app.removals++;
}

const pendingDeletes = new PendingQueue<Job>(UNDO_MS, (id, job) => {
  if (pendingDeletes.ids().length === 0) dropUndoToast();
  sendDelete(id, job);
});

/** Brings back every delete still waiting. */
function undoDeletes() {
  for (const id of pendingDeletes.ids()) {
    const back = pendingDeletes.undo(id);
    if (back) {
      gone.delete(back.id);
      upsertJob(back);
    }
  }
  dropUndoToast();
  app.removals++;
}

/** Takes the jobs out of view and offers one Undo for all that are waiting; the deletes
 * are sent together when the offer runs out. */
export function deleteJobs(jobs: Job[]) {
  const fresh = jobs.filter((j) => !pendingDeletes.has(j.id));
  if (!fresh.length) return;
  const waiting = new Map<string, Job>();
  for (const id of pendingDeletes.ids()) {
    const job = pendingDeletes.undo(id);
    if (job) waiting.set(id, job);
  }
  for (const job of fresh) {
    waiting.set(job.id, job);
    takeOutOfView(job.id);
  }
  for (const [id, job] of waiting) pendingDeletes.add(id, job); // every wait starts again
  dropUndoToast();
  const n = waiting.size;
  undoToast = toast(n === 1 ? "Deleted." : `${n} deleted.`, "info", UNDO_MS, {
    label: n === 1 ? "Undo" : "Undo all",
    run: undoDeletes,
  });
}

/** Takes the job out of view and offers Undo; the delete is sent when the offer runs out. */
export function deleteJob(job: Job) {
  deleteJobs([job]);
}

/** Sends every waiting delete now: the page is being hidden or closed. */
function flushDeletes() {
  dropUndoToast();
  for (const id of pendingDeletes.ids()) {
    const job = pendingDeletes.undo(id);
    if (job) sendDelete(id, job, true);
  }
}

/** A change to one job (cancel, favorite, reveal). A job the core no longer knows is taken out of view. */
export async function jobPost(path: string, body: unknown = {}): Promise<boolean> {
  try {
    await api(path, { body });
    return true;
  } catch (e) {
    const id = /^\/jobs\/([^/]+)/.exec(path)?.[1];
    if (e instanceof ApiError && e.status === 404 && id) {
      takeOutOfView(id);
      toast("That one is already gone.");
    } else {
      fail(e);
    }
    return false;
  }
}

function onEvent(e: { type: string; data: any }) {
  switch (e.type) {
    case "job": {
      const prev = app.jobs.find((j) => j.id === e.data.id);
      eventAt.set(e.data.id, Date.now());
      if (pendingDeletes.has(e.data.id)) break; // it is on its way out; the page already hid it
      upsertJob(e.data);
      if (prev && prev.status !== e.data.status) {
        if (e.data.status === "failed") toast(e.data.error ?? "That did not work.", "error", 7000);
        refreshEngines();
      }
      break;
    }
    case "job.deleted":
      eventAt.set(e.data.id, Date.now());
      // Deleted from somewhere else too (or by this page's own request): nothing left to undo.
      forget(e.data.id);
      takeOutOfView(e.data.id);
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
  connectEvents(
    onEvent,
    (connected) => {
      app.connected = connected;
      // After a reconnect, catch up on anything missed.
      if (connected && !first) refresh();
      first = false;
    },
    () => (app.unpaired = true),
  );
  // Deletes wait for their Undo; leaving the page must not lose them.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushDeletes();
  });
  window.addEventListener("pagehide", flushDeletes);
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
export async function followJob(
  job: Job,
  onStatus: (message: string, progress: number | null) => void,
  signal?: AbortSignal,
): Promise<Job> {
  const ended = await pollJob(job, {
    fetchJob: (id) => api<Job>(`/jobs/${id}`),
    onStatus: (j) => onStatus(j.status === "queued" ? "Waiting for the graphics card" : (j.message ?? "Working"), j.progress),
    signal,
  });
  if (ended.status === "failed") throw new Error(ended.error ?? "That did not work.");
  if (ended.status !== "done") throw new Error("Cancelled.");
  return ended;
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
export async function writePrompt(req: WriteRequest, onStatus: (message: string) => void, signal?: AbortSignal): Promise<string> {
  const job = await api<Job>("/jobs", {
    body: { model: "prompt-writer", params: { mode: req.mode, target: req.target, text: req.text ?? "", images: req.images ?? [] } },
  });
  const ended = await pollJob(job, {
    fetchJob: (id) => api<Job>(`/jobs/${id}`),
    onStatus: (j) => onStatus(j.status === "queued" ? "Waiting for the graphics card" : (j.message ?? "Writing")),
    signal,
  });
  if (ended.status === "failed") throw new Error(ended.error ?? "The prompt writer failed.");
  if (ended.status !== "done") throw new Error("Cancelled.");
  return ended.outputs[0]?.text ?? "";
}
