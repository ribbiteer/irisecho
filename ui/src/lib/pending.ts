// SPDX-License-Identifier: AGPL-3.0-or-later
// Things waiting to be done after a moment in which they can still be taken back
// (a delete with an Undo). Each is committed when its delay runs out or when the queue
// is flushed, and never twice.

export interface Pending<T> {
  item: T;
  timer: ReturnType<typeof setTimeout>;
}

export class PendingQueue<T> {
  private waiting = new Map<string, Pending<T>>();

  constructor(
    private delay: number,
    private commit: (id: string, item: T) => void,
  ) {}

  has(id: string): boolean {
    return this.waiting.has(id);
  }

  ids(): string[] {
    return [...this.waiting.keys()];
  }

  /** Starts the wait for `id`; adding it again restarts the wait. */
  add(id: string, item: T): void {
    const old = this.waiting.get(id);
    if (old) clearTimeout(old.timer);
    const timer = setTimeout(() => this.commitOne(id), this.delay);
    this.waiting.set(id, { item, timer });
  }

  /** Takes `id` back: nothing is committed. Returns what was waiting, if anything. */
  undo(id: string): T | undefined {
    const p = this.waiting.get(id);
    if (!p) return undefined;
    clearTimeout(p.timer);
    this.waiting.delete(id);
    return p.item;
  }

  /** Commits everything now (the page is going away). Returns the ids that were waiting. */
  flush(): string[] {
    const ids = this.ids();
    for (const id of ids) this.commitOne(id);
    return ids;
  }

  private commitOne(id: string): void {
    const p = this.waiting.get(id);
    if (!p) return;
    clearTimeout(p.timer);
    this.waiting.delete(id);
    this.commit(id, p.item);
  }
}
