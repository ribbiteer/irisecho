// SPDX-License-Identifier: AGPL-3.0-or-later
// Whether an element is on (or near) the screen, from one shared observer per margin.
// A phone pays for every video that loads and every mark that animates, seen or not.

type Watch = (visible: boolean) => void;

const observers = new Map<string, { io: IntersectionObserver; watches: Map<Element, Watch> }>();

function observer(margin: string) {
  let o = observers.get(margin);
  if (!o) {
    const watches = new Map<Element, Watch>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) watches.get(e.target)?.(e.isIntersecting);
      },
      { rootMargin: margin },
    );
    o = { io, watches };
    observers.set(margin, o);
  }
  return o;
}

/** Calls `watch` with true when `el` comes within `margin` of the screen and false when it leaves. */
export function inView(el: Element, watch: Watch, margin = "0px"): () => void {
  if (typeof IntersectionObserver === "undefined") {
    watch(true);
    return () => {};
  }
  const o = observer(margin);
  o.watches.set(el, watch);
  o.io.observe(el);
  return () => {
    o.io.unobserve(el);
    o.watches.delete(el);
  };
}
