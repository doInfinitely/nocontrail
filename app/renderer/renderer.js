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
let lastClip = null; // { y, durationS, name }

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

from contrail.scrub import scrub, ScrubConfig

def scrub_clip(path, sr, decoys):
    y = np.fromfile(path, dtype="float32")
    out, rep = scrub(y, sr, ScrubConfig(decoy_count=decoys, seed=0))
    out.astype("float32").tofile("/scrubbed.f32")
    return json.dumps(dict(
        before=dict(lines=len(rep.tracks_before), leak=round(leak_score(rep.tracks_before), 3)),
        after=dict(lines=len(rep.tracks_after), leak=round(leak_score(rep.tracks_after), 3)),
        decoys=len(rep.decoys), speech=round(rep.vad_speech_fraction, 3), n=int(out.size),
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

function encodeWav(float32, sr) {
  const n = float32.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(buf);
  const w = (o, str) => { for (let i = 0; i < str.length; i++) dv.setUint8(o + i, str.charCodeAt(i)); };
  w(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); w(8, "WAVE");
  w(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  w(36, "data"); dv.setUint32(40, n * 2, true);
  let o = 44;
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, float32[i]));
    dv.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7FFF, true);
    o += 2;
  }
  return new Uint8Array(buf);
}

const DECOYS = 8;

async function runScrub(y) {
  const copy = new Float32Array(y);
  py.FS.writeFile("/clip.f32", new Uint8Array(copy.buffer));
  const fn = py.globals.get("scrub_clip");
  let report;
  try { report = JSON.parse(fn("/clip.f32", SR, DECOYS)); } finally { fn.destroy(); }
  const bytes = py.FS.readFile("/scrubbed.f32"); // Uint8Array of float32
  const scrubbed = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
  return { report, wav: encodeWav(scrubbed, SR) };
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
    ${maxLeak > 0 || planes > 0
      ? `<div class="scrub"><button type="button" id="scrub">Scrub &amp; save a clean copy</button>
         <span id="scrub-note" class="muted">Removes the aircraft lines and mixes in decoy flyovers. Output is 16 kHz mono.</span></div>`
      : ``}
    <div id="scrub-result"></div>
    <div class="again"><button type="button" id="again">Audit another</button></div>`;
  r.hidden = false;
  el("again").onclick = reset;
  const scrubBtn = el("scrub");
  if (scrubBtn) scrubBtn.onclick = () => doScrub();
}

async function doScrub() {
  if (!lastClip || busy) return;
  busy = true;
  const btn = el("scrub");
  const note = el("scrub-note");
  if (btn) btn.disabled = true;
  if (note) note.textContent = "Scrubbing… this runs the full clip, so it takes a moment.";
  try {
    const { report, wav } = await runScrub(lastClip.y);
    const base = lastClip.name.replace(/\.[^.]+$/, "") + " (scrubbed).wav";
    const res = await API.saveWav(base, wav);
    const out = el("scrub-result");
    const savedLine = res.saved ? `Saved to <code>${res.path}</code>.` : "Not saved.";
    out.innerHTML = `<div class="verdict good" style="margin-top:16px">
      <h2>Scrubbed.</h2>
      <p>Notched the ${report.before.lines} detected aircraft line(s) and mixed in ${report.decoys} decoy flyover(s) at the same level.
         Voice activity was ${Math.round(report.speech * 100)}% of frames. ${savedLine}</p>
      <p class="score">A detector now sees ${report.after.lines} line(s), most of them decoys a fitter can't tell from a real plane. Removal is capped by what the detector finds — audit the saved file and listen before you rely on it.</p>
    </div>`;
    if (note) note.textContent = "";
  } catch (e) {
    const out = el("scrub-result");
    out.innerHTML = `<p class="err">Could not scrub: ${String(e.message || e)}</p>`;
  } finally {
    if (btn) btn.disabled = false;
    busy = false;
  }
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
    lastClip = { y, durationS, name };
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
    if (API.selftestScrub) {
      const { report, wav } = await runScrub(y);
      if (API.scrubOutPath) await API.selftestWrite(API.scrubOutPath, wav);
      console.log("SELFTEST_SCRUB " + JSON.stringify(report));
    }
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
