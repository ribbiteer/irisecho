// SPDX-License-Identifier: AGPL-3.0-or-later
// The fibers of the mark, pulsing while IrisEcho gets ready.
const g = document.getElementById("fibers");
const n = 48;
for (let i = 0; i < n; i++) {
  const t = (2 * Math.PI * i) / n;
  const v = Math.min(1, Math.max(0, 0.58 + 0.07 * Math.sin(3 * t + 0.6) + 0.13 * Math.sin(8 * t + 1.9) + 0.12 * Math.sin(13 * t + 0.3) + 0.1 * Math.sin(31 * t + 2.2)));
  const len = 12 + v * 15;
  const grp = document.createElementNS("http://www.w3.org/2000/svg", "g");
  grp.setAttribute("transform", `rotate(${(360 * i) / n})`);
  const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  Object.entries({ x: -1.1, y: -(20 + len), width: 2.2, height: len, rx: 1.1, class: "fiber" }).forEach(([k, val]) => r.setAttribute(k, val));
  r.style.animationDelay = `-${((i / n) * 1.6).toFixed(3)}s`;
  grp.appendChild(r);
  g.appendChild(grp);
}

const invoke = (cmd, args) => window.__TAURI__.core.invoke(cmd, args);
const $ = (id) => document.getElementById(id);
let shown = 0;

async function poll() {
  let s;
  try { s = await invoke("boot_status"); } catch { return setTimeout(poll, 400); }
  $("message").textContent = s.message || "Getting ready";
  const log = $("log");
  for (const line of s.lines.slice(shown)) {
    const d = document.createElement("div");
    d.textContent = line;
    log.appendChild(d);
  }
  shown = s.lines.length;
  while (log.children.length > 12) log.removeChild(log.firstChild);
  const failed = s.stage === "error";
  $("error").textContent = failed ? s.message : "";
  $("error").classList.toggle("hidden", !failed);
  $("actions").classList.toggle("hidden", !failed);
  $("message").classList.toggle("hidden", failed);
  $("title").textContent = failed ? "IrisEcho could not start" : "IrisEcho";
  document.querySelectorAll(".fiber").forEach((f) => (f.style.animationPlayState = failed ? "paused" : "running"));
  if (s.stage !== "ready") setTimeout(poll, failed ? 1500 : 300);
}
$("retry").onclick = () => { shown = 0; $("log").textContent = ""; invoke("retry_boot").then(() => setTimeout(poll, 300)); };
$("logs").onclick = () => invoke("open_logs");
poll();
