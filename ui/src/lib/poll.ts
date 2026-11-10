// SPDX-License-Identifier: AGPL-3.0-or-later
// Follows a job until it ends, for jobs that are not shown in the feed (prompt writing,
// the views a 3D model is built from). One dropped request does not end the wait, and the
// caller can stop it.

import type { Job } from "./types";

export interface PollOptions {
  fetchJob: (id: string) => Promise<Job>;
  /** Called with each unfinished state the job is seen in. */
  onStatus: (job: Job) => void;
  signal?: AbortSignal;
  /** Milliseconds between looks. */
  interval?: number;
  /** Failed looks in a row before giving up. */
  attempts?: number;
}

export const ENDED: Job["status"][] = ["done", "failed", "cancelled", "interrupted"];

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener("abort", done);
  });
}

/** Resolves with the job once it has ended (in any way); throws when stopped or when it keeps failing. */
export async function pollJob(first: Job, o: PollOptions): Promise<Job> {
  const interval = o.interval ?? 400;
  const attempts = o.attempts ?? 5;
  let job = first;
  let misses = 0;
  for (;;) {
    if (o.signal?.aborted) throw new Error("Cancelled.");
    if (ENDED.includes(job.status)) return job;
    if (misses === 0) o.onStatus(job);
    await pause(misses ? Math.min(4000, interval * 2 ** misses) : interval, o.signal);
    if (o.signal?.aborted) throw new Error("Cancelled.");
    try {
      job = await o.fetchJob(job.id);
      misses = 0;
    } catch (e) {
      if ((e as { status?: number }).status === 404) throw new Error("That job is gone.");
      if (++misses >= attempts) throw e;
    }
  }
}
