// SPDX-License-Identifier: AGPL-3.0-or-later
// Talks to the local IrisEcho core. The session cookie is set when the page
// loads; every change also carries the X-IrisEcho header (see server.py).

import type { Job } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public needs: string[] = [],
  ) {
    super(message);
  }
}

export async function api<T = any>(
  path: string,
  options: { method?: string; body?: unknown; form?: FormData } = {},
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
    res = await fetch(`/api${path}`, { method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError("IrisEcho is not responding. Is it still running?", 0);
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    let needs: string[] = [];
    try {
      const data = await res.json();
      message = data.error ?? data.detail ?? message;
      needs = data.needs ?? [];
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, res.status, needs);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const outputUrl = (job: Job, n = 0, download = false) =>
  `/api/jobs/${job.id}/outputs/${n}${download ? "?download=true" : ""}`;

export const uploadUrl = (id: string) => `/api/uploads/${id}`;

export function connectEvents(
  onEvent: (e: { type: string; data: any }) => void,
  onStatus: (connected: boolean) => void,
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
    ws.onclose = () => {
      onStatus(false);
      if (closed) return;
      timer = window.setTimeout(open, delay);
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
