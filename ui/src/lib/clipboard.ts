// SPDX-License-Identifier: AGPL-3.0-or-later
// Copy text to the clipboard. `navigator.clipboard` only exists on https and on localhost,
// so a phone reaching IrisEcho over plain HTTP on the network falls back to a hidden textarea.

/** Copies `text`; resolves true when it worked. */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* refused: try the older way */
    }
  }
  if (typeof document === "undefined") return false;
  const box = document.createElement("textarea");
  box.value = text;
  box.setAttribute("readonly", "");
  box.style.position = "fixed";
  box.style.top = "0";
  box.style.left = "-9999px";
  box.style.opacity = "0";
  document.body.appendChild(box);
  const keep = document.activeElement as HTMLElement | null;
  try {
    box.focus();
    box.select();
    box.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    box.remove();
    keep?.focus?.();
  }
}
