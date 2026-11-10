// SPDX-License-Identifier: AGPL-3.0-or-later
// The ranges a model declares for takes, music length and video seconds (registry
// settings.limits, sent as `limits` with each model). A model without them keeps the
// values the forms always had.

import type { Model } from "./types";

export interface Range {
  min: number;
  max: number;
}

/** The model's declared range for `name`, or `fallback` when it declares none. */
export function rangeOf(model: Model | undefined, name: "takes" | "duration" | "seconds", fallback: Range): Range {
  const l = model?.limits?.[name];
  if (!l || !Number.isFinite(l.min) || !Number.isFinite(l.max) || l.max < l.min) return fallback;
  return { min: l.min, max: l.max };
}

/** The take counts to offer: 1 up to the model's most (4 when it declares none). */
export function takeChoices(model: Model | undefined): number[] {
  const { min, max } = rangeOf(model, "takes", { min: 1, max: 4 });
  const out: number[] = [];
  for (let n = Math.max(1, Math.ceil(min)); n <= Math.floor(max) && out.length < 12; n++) out.push(n);
  return out.length ? out : [1];
}

/** `value` held inside the range. */
export function within(value: number, r: Range): number {
  return Math.min(r.max, Math.max(r.min, value));
}
