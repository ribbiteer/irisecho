// SPDX-License-Identifier: AGPL-3.0-or-later
// Talks to the local IrisEcho core. The session cookie is set when the page
// loads; every change also carries the X-IrisEcho header (see server.py).

import type { Job } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public needs: string[] = [],
    /** The rest of the error body: `status` on a job conflict, `pair` on a refused device. */
    public data: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function api<T = any>(
  path: string,
  options: { method?: string; body?: unknown; form?: FormData; keepalive?: boolean } = {},
): Promise<T> {
  const method = options.method ?? (options.body !== undefined || options.form ? "POST" : "GET");
  const headers: Record<string, string> = { "X-IrisEcho": "1" };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body, credentials: "same-origin", keepalive: options.keepalive });
  } catch {
    throw new ApiError("IrisEcho is not responding. Is it still running?", 0);
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    let needs: string[] = [];
    let extra: Record<string, unknown> = {};
    try {
      const data = await res.json();
      message = (typeof data.error === "string" && data.error) || (typeof data.detail === "string" && data.detail) || message;
      needs = Array.isArray(data.needs) ? data.needs : [];
      extra = data && typeof data === "object" ? data : {};
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, res.status, needs, extra);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const outputUrl = (job: Job, n = 0, download = false) =>
  `/api/jobs/${job.id}/outputs/${n}${download ? "?download=true" : ""}`;

export const uploadUrl = (id: string) => `/api/uploads/${id}`;

export const previewUrl = (job: Job, n = 0) => `/api/jobs/${job.id}/outputs/${n}/preview`;

export const exportUrl = (job: Job, n: number, format: string, heightMm?: number) =>
  `/api/jobs/${job.id}/outputs/${n}/export?format=${format}${heightMm ? `&height_mm=${heightMm}` : ""}`;

/** A job's output as a new upload, so another job can use it. */
export async function outputToUpload(job: Job, n = 0): Promise<string> {
  const res = await fetch(outputUrl(job, n), { credentials: "same-origin" });
  if (!res.ok) throw new Error("That picture is no longer available.");
  const blob = await res.blob();
  const form = new FormData();
  form.append("file", new File([blob], "picture.png", { type: blob.type || "image/png" }));
  const up = await api<{ id: string }>("/uploads", { form });
  return up.id;
}

export function connectEvents(
  onEvent: (e: { type: string; data: any }) => void,
  onStatus: (connected: boolean) => void,
  onAuthLost: () => void = () => {},
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let delay = 500;
  let timer: number | undefined;

  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/events`);
    ws.onopen = () => {
      delay = 500;
      onStatus(true);
    };
    ws.onmessage = (m) => {
      try {
        const event = JSON.parse(m.data);
        if (event.type !== "ping") onEvent(event);
      } catch {
        /* ignore */
      }
    };
    ws.onclose = (ev) => {
      onStatus(false);
      if (closed) return;
      // 4401: the core closed it because this device was removed. A refused reconnect shows
      // no code, so a plain request tells whether the device is still known.
      if (ev.code === 4401) {
        closed = true;
        onAuthLost();
        return;
      }
      timer = window.setTimeout(async () => {
        try {
          await api("/system");
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) {
            closed = true;
            onAuthLost();
            return;
          }
        }
        open();
      }, delay);
      delay = Math.min(delay * 1.7, 8000);
    };
  };
  open();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    ws?.close();
  };
}
