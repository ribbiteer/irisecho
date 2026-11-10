// SPDX-License-Identifier: AGPL-3.0-or-later
// Advanced controls: the per-model values a form adds to a job when "Show advanced controls" is on.

import { api } from "./api";
import { app } from "./state.svelte";
import type { Control, ControlValue, Job, Model } from "./types";

/** The values set for a model in this session (only those that differ from recommended). */
export function valuesFor(model: Model): Record<string, ControlValue> {
  return (app.advanced[model.id] ??= {});
}

export function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function isChanged(c: Control, value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (c.type === "loras") return Array.isArray(value) && value.length > 0;
  return !same(value, c.default);
}

/** Whether a control applies to the job as the form has it now (`needs` another param). */
export function applies(c: Control, context: Record<string, unknown>): boolean {
  if (!c.needs) return true;
  const v = context[c.needs];
  return v !== undefined && v !== null && v !== "" && v !== false;
}

/** The advanced params to send: nothing at all unless advanced controls are switched on. */
export function controlParams(model: Model, context: Record<string, unknown> = {}): Record<string, ControlValue> {
  if (!app.settings?.advanced || !model.controls?.length) return {};
  const values = valuesFor(model);
  const out: Record<string, ControlValue> = {};
  for (const c of model.controls) {
    const v = values[c.name];
    if (isChanged(c, v) && applies(c, context)) out[c.name] = v;
  }
  return out;
}

/** "Use these settings again": copy a job's advanced values into the form. Returns how many. */
export function restoreControls(model: Model, params: Record<string, unknown>): number {
  const values: Record<string, ControlValue> = {};
  for (const c of model.controls ?? []) {
    const v = params[c.name];
    if (v !== undefined && v !== null && isChanged(c, v)) values[c.name] = v as ControlValue;
  }
  app.advanced[model.id] = values;
  return Object.keys(values).length;
}

/** A seed for the i-th job of a batch: the fixed one counting up, or a fresh random one. */
export function nextSeed(fixed: boolean, seed: number, i: number): number {
  return usableSeed(fixed, seed) ? Number(seed) + i : Math.floor(Math.random() * 2 ** 50);
}

/** A fixed seed is only used when the field holds a number: an emptied field means no seed, not seed 0. */
export function usableSeed(fixed: boolean, seed: unknown): boolean {
  return fixed && seed !== null && seed !== undefined && seed !== "" && Number.isFinite(Number(seed));
}

/** The seed part of a job's params: the fixed seed, or nothing so the core draws one. */
export function seedParam(fixed: boolean, seed: unknown): { seed?: number } {
  return usableSeed(fixed, seed) ? { seed: Number(seed) } : {};
}

/**
 * The shared part of "use these settings again": advanced values come back (and the
 * section opens when there are any) and a recorded seed becomes the fixed seed.
 */
export function reuseCommon(model: Model, job: Job): { seed: number | null } {
  if (restoreControls(model, job.params) && app.settings?.advanced) app.advancedOpen = true;
  const recorded = job.params.seed ?? job.outputs?.[0]?.seed;
  return { seed: typeof recorded === "number" ? recorded : null };
}

/**
 * Read the settings written into a picture (by IrisEcho, or A1111's layout) and bring them
 * into the form like "use these settings again". Returns a message for the person.
 */
export async function settingsFromPicture(file: File, kind: Job["kind"]): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const up = await api<{ id: string }>("/uploads", { form });
  const found = await api<{ model: string | null; params: Record<string, unknown>; source: string }>(
    `/uploads/${up.id}/settings`,
  );
  const model = found.model ? app.models.find((m) => m.id === found.model) : undefined;
  if (model && model.kind === kind) app.chosen[kind] = model.id;
  const target = model && model.kind === kind ? model.id : (app.chosen[kind] ?? found.model ?? "");
  app.reuse = {
    id: "",
    model: target,
    kind,
    params: found.params,
    status: "done",
    progress: null,
    message: null,
    error: null,
    created: Date.now() / 1000,
    started: null,
    finished: null,
    outputs: [],
    favorite: false,
  };
  if (found.source === "irisecho") return model ? `Settings from ${model.name} brought back.` : "Settings brought back.";
  return "Prompt, seed and steps brought back from the picture's A1111 settings.";
}

/** A plain rendering of a control value for the result's Settings panel. */
export function showValue(c: Control | undefined, v: unknown): string {
  if (Array.isArray(v)) {
    return v
      .map((l) => {
        const e = l as { file?: string; high?: string; low?: string; strength?: number };
        const name = e.file ?? [e.high, e.low].filter(Boolean).join(" + ");
        return `${name.replace(/\.safetensors$/i, "")} × ${e.strength ?? 1}`;
      })
      .join(", ");
  }
  if (typeof v === "boolean") return v ? "on" : "off";
  const opt = c?.options?.find((o) => o.value === v);
  if (opt) return opt.label;
  return `${v}${c?.unit ? ` ${c.unit}` : ""}`;
}

/**
 * "Use these settings again" for the seed too: a recorded seed becomes the fixed seed, and a
 * job that had none puts the form back to a random one. `setSeed` receives the recorded seed or null.
 */
export function applyReuse(model: Model, job: Job, setSeed: (seed: number | null) => void): void {
  setSeed(reuseCommon(model, job).seed);
}

/** The most jobs one "one file per line" press may queue. */
export const MAX_BATCH = 40;

/** The lines to make one job each from; with `perLine` off, the whole text is one. Lines starting with # are notes. */
export function splitLines(text: string, perLine: boolean): string[] {
  if (!perLine) return text.trim() ? [text.trim()] : [];
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
}

/** Ctrl+Enter (Cmd+Enter) anywhere in a form makes it go. */
export function ctrlEnter(run: () => void): (e: KeyboardEvent) => void {
  return (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      run();
    }
  };
}

/** Submits each item in turn and stops at the first one that does not go through. Returns how many did. */
export async function submitEach<T>(items: T[], send: (item: T, i: number) => Promise<unknown>): Promise<number> {
  let sent = 0;
  for (const [i, item] of items.entries()) {
    if (!(await send(item, i))) break;
    sent++;
  }
  return sent;
}
