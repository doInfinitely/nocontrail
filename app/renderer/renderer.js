// No Contrail renderer: decode a chosen recording locally, resample to mono
// 16 kHz, and run the Python engine (via Pyodide) over sampled windows. Nothing
// is sent anywhere — the app can reach no network.
const API = window.nocontrail;
const SR = 16000;
const WINDOW_S = 90;
const N_WINDOWS = 8;

const el = (id) => document.getElementById(id);
const drop = el("drop"), statusEl = el("status"), bar = el("bar-fill");

let py = null;
let enginePromise = null;
let busy = false;

function setEngineNote(text, done) {
  const n = el("engine-note");
  n.textContent = text;
  n.style.color = done ? "var(--good)" : "";
}

async function engine() {
  if (enginePromise) return enginePromise;
  enginePromise = (async () => {
    setEngineNote("Loading engine…");
    const { loadPyodide } = await import("app://local/pyodide/pyodide.mjs");
    py = await loadPyodide({ indexURL: "app://local/pyodide/" });
    setEngineNote("Loading numpy + scipy…");
    await py.loadPackage(["numpy", "scipy"]);
    try { py.FS.mkdir("/contrail"); } catch {}
    const files = API.engineFiles();
    for (const [name, text] of Object.entries(files)) py.FS.writeFile("/contrail/" + name, text);
    py.runPython(`
import sys
if "/" not in sys.path: sys.path.insert(0, "/")
import numpy as np, json
from contrail.detect import find_tracks, group_flyovers, leak_score

def audit_window(path, sr):
    y = np.fromfile(path, dtype="float32")
    tr = find_tracks(y, sr)
    g = group_flyovers(tr)
    return json.dumps(dict(
        lines=len(tr), flyovers=len(g), leak=round(leak_score(tr), 4),
        groups=[dict(harmonics=x.harmonics, v=round(x.fit.v, 1), miss=round(x.fit.d, 0),
                     tc=round(x.fit.tc, 1), rms=round(x.fit.rms / x.fit.f0, 4)) for x in g],
    ))
`);
    console.log("ENGINE_READY");
    setEngineNote("Engine ready — processed entirely on this machine.", true);
  })();
  return enginePromise;
}

async function decodeMono16k(arrayBuffer) {
  const ac = new AudioContext();
  let decoded;
  try { decoded = await ac.decodeAudioData(arrayBuffer.slice(0)); }
  finally { ac.close(); }
  const length = Math.max(1, Math.ceil(decoded.duration * SR));
  const off = new OfflineAudioContext(1, length, SR); // 1 channel ⇒ downmix to mono
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const rendered = await off.startRendering();
  return { y: rendered.getChannelData(0), durationS: decoded.duration };
}

function pickWindows(y) {
  const W = Math.round(WINDOW_S * SR);
  if (y.length <= W * 1.2) return [{ start: 0, data: y }];
  const usable = y.length - W;
  const out = [];
  for (let i = 0; i < N_WINDOWS; i++) {
    const frac = 0.02 + 0.96 * (i / (N_WINDOWS - 1));
    const start = Math.round(frac * usable);
    out.push({ start, data: y.subarray(start, start + W) });
  }
  return out;
}

async function runEngineOnWindow(data) {
  // subarray shares the decoded buffer; copy the exact window bytes to the FS.
  const copy = new Float32Array(data); // contiguous
  py.FS.writeFile("/win.f32", new Uint8Array(copy.buffer));
  const audit = py.globals.get("audit_window");
  try {
    return JSON.parse(audit("/win.f32", SR));
  } finally {
    audit.destroy();
  }
}

function verdict(maxLeak, planes) {
  if (maxLeak >= 0.5) return { cls: "bad", head: "This recording likely leaks your location.",
    body: "Aircraft Doppler lines strong and clean enough to match against flight data. Scrub before sharing." };
  if (maxLeak >= 0.2 || planes >= 1) return { cls: "warn", head: "Weak aircraft signature found.",
    body: "Some flyover lines are present but not strong enough on their own to place the recording. Worth scrubbing to be safe." };
  return { cls: "good", head: "No usable location leak found.",
    body: "No aircraft-Doppler lines strong enough to localize this recording were detected in the sampled audio." };
}

function showResults(name, durationS, windows) {
  const scored = windows.filter((w) => "leak" in w);
  const maxLeak = scored.reduce((m, w) => Math.max(m, w.leak), 0);
  const planes = scored.reduce((m, w) => Math.max(m, w.flyovers), 0);
  const v = verdict(maxLeak, planes);
  const rows = scored.filter((w) => w.flyovers > 0).map((w) => {
    const g = w.groups.map((x) => `v=${x.v} m/s, miss ${x.miss} m`).join("; ");
    return `<tr><td class="num">${(w.startS / 60).toFixed(1)} min</td><td class="num">${w.leak.toFixed(2)}</td><td>${w.flyovers}</td><td>${g}</td></tr>`;
  }).join("");
  const r = el("results");
  r.innerHTML = `
    <div class="verdict ${v.cls}">
      <h2>${v.head}</h2>
      <p>${v.body}</p>
      <p class="score">${name} · ${(durationS / 60).toFixed(1)} min · ${scored.length} window(s) sampled · peak leak ${maxLeak.toFixed(2)} / 1.00</p>
    </div>
    ${rows ? `<table><thead><tr><th>at</th><th>leak</th><th>flyovers</th><th>per flyover (relative to mic)</th></tr></thead><tbody>${rows}</tbody></table>`
           : `<p class="muted" style="margin-top:16px">No flyover candidates in any sampled window.</p>`}
    <p class="muted" style="margin-top:14px;font-size:13px">Validated on synthetic audio only. Treat as indicative. Parameters are relative to the microphone — speed and miss distance, not a position on Earth.</p>
    <div class="again"><button type="button" id="again">Audit another</button></div>`;
  r.hidden = false;
  el("again").onclick = reset;
}

function reset() {
  el("results").hidden = true;
  el("progress").hidden = true;
  drop.classList.remove("busy");
  busy = false;
}

async function handleBuffer(arrayBuffer, name) {
  if (busy) return;
  busy = true;
  reset();
  busy = true;
  drop.classList.add("busy");
  el("results").hidden = true;
  const prog = el("progress");
  prog.hidden = false;
  const setStatus = (t) => { statusEl.textContent = t; };
  const setBar = (f) => { bar.style.width = Math.round(f * 100) + "%"; };
  try {
    setStatus("Preparing engine…"); setBar(0.05);
    await engine();
    setStatus("Decoding audio…"); setBar(0.1);
    const { y, durationS } = await decodeMono16k(arrayBuffer);
    const windows = pickWindows(y);
    const results = [];
    for (let i = 0; i < windows.length; i++) {
      setStatus(`Analysing window ${i + 1} of ${windows.length}…`);
      setBar(0.1 + 0.85 * (i / windows.length));
      // let the UI paint before the blocking engine call
      await new Promise((r) => setTimeout(r, 15));
      const out = await runEngineOnWindow(windows[i].data);
      out.startS = windows[i].start / SR;
      results.push(out);
    }
    setBar(1);
    prog.hidden = true;
    showResults(name, durationS, results);
  } catch (e) {
    prog.hidden = true;
    const r = el("results");
    r.innerHTML = `<p class="err">Could not audit this file: ${String(e.message || e)}</p><div class="again"><button type="button" id="again">Try another</button></div>`;
    r.hidden = false;
    el("again").onclick = reset;
  } finally {
    drop.classList.remove("busy");
    busy = false;
  }
}

async function pickAndAudit() {
  const path = await API.pickAudio();
  if (!path) return;
  const buf = API.readAudioFile(path);
  handleBuffer(buf, API.basename(path));
}

el("pick").addEventListener("click", (e) => { e.stopPropagation(); pickAndAudit(); });
drop.addEventListener("click", pickAndAudit);
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") pickAndAudit(); });
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  const f = e.dataTransfer.files[0];
  if (f) f.arrayBuffer().then((b) => handleBuffer(b, f.name));
});

// Self-test mode: audit a file path from the environment and report via IPC.
async function selftest(path) {
  try {
    await engine();
    const buf = API.readAudioFile(path);
    const { y, durationS } = await decodeMono16k(buf);
    const windows = pickWindows(y);
    const results = [];
    for (const w of windows) {
      const out = await runEngineOnWindow(w.data);
      out.startS = w.start / SR;
      results.push(out);
    }
    const maxLeak = results.reduce((m, w) => Math.max(m, w.leak || 0), 0);
    const planes = results.reduce((m, w) => Math.max(m, w.flyovers || 0), 0);
    el("progress").hidden = true;
    drop.hidden = true;
    el("intro").hidden = true;
    showResults(API.basename(path), durationS, results);
    console.log("SELFTEST_RESULT " + JSON.stringify({ maxLeak, planes, windows: results.length, durationS: Math.round(durationS) }));
    await new Promise((r) => setTimeout(r, 120));
    await API.selftestDone({ ok: true, maxLeak, planes });
  } catch (e) {
    console.log("SELFTEST_ERROR " + (e.message || e));
    await API.selftestDone({ ok: false, error: String(e.message || e) });
  }
}

if (API.selftestPath) {
  selftest(API.selftestPath);
} else {
  // Warm the engine up front so the first audit is fast.
  engine().catch((e) => setEngineNote("Engine failed to load: " + (e.message || e)));
}
