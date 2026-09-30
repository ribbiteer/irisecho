// SPDX-License-Identifier: AGPL-3.0-or-later
// Bridges to the desktop shell when the UI runs inside it; browser fallbacks otherwise.

declare global {
  interface Window {
    __TAURI__?: {
      core: { invoke: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> };
    };
  }
}

export const inShell = () => typeof window !== "undefined" && !!window.__TAURI__;

// The folder the person chose, or null if they closed the picker. Throws if the
// picker could not be opened at all, so the caller can say so instead of doing nothing.
export async function pickFolder(): Promise<string | null> {
  if (!inShell()) return null;
  return await window.__TAURI__!.core.invoke<string | null>("pick_folder");
}

export function openExternal(url: string) {
  if (inShell()) {
    window.__TAURI__!.core.invoke("open_external", { url }).catch(() => window.open(url, "_blank"));
  } else {
    window.open(url, "_blank", "noopener");
  }
}

// Links that leave the app open in the system browser, never inside the app window.
export function routeExternalLinks() {
  document.addEventListener("click", (e) => {
    const a = (e.target as HTMLElement).closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a) return;
    const href = a.getAttribute("href") ?? "";
    if (/^https?:\/\//.test(href) && !href.startsWith(location.origin)) {
      e.preventDefault();
      openExternal(href);
    }
  });
}
