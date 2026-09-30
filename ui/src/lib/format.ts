// SPDX-License-Identifier: AGPL-3.0-or-later

export function bytes(n: number): string {
  if (!n) return "0 MB";
  const gb = n / 2 ** 30;
  if (gb >= 1) return `${gb >= 10 ? gb.toFixed(0) : gb.toFixed(1)} GB`;
  const mb = n / 2 ** 20;
  return `${mb >= 10 ? mb.toFixed(0) : mb.toFixed(1)} MB`;
}

export function speed(bps: number): string {
  return bps ? `${bytes(bps)}/s` : "";
}

export function eta(remaining: number, bps: number): string {
  if (!bps) return "";
  const s = remaining / bps;
  if (s < 60) return "under a minute";
  const m = Math.round(s / 60);
  return m < 60 ? `about ${m} min` : `about ${(m / 60).toFixed(1)} h`;
}

export function duration(seconds?: number | null): string {
  if (seconds == null || !isFinite(seconds)) return "";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function clock(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ago(ts: number): string {
  const s = Date.now() / 1000 - ts;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function elapsed(job: { started: number | null; finished: number | null }): string {
  if (!job.started || !job.finished) return "";
  return duration(job.finished - job.started);
}
