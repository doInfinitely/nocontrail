// Bridge between the isolated renderer and the local filesystem. The renderer
// gets only: the engine source text, a reader for a chosen audio file, the
// native file picker, and the platform. Everything else (Pyodide, renderer
// assets) is served over app://. No network is reachable from here.
const { contextBridge, ipcRenderer } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const engineDir = path.join(__dirname, "engine", "contrail");

contextBridge.exposeInMainWorld("nocontrail", {
  engineFiles: () => {
    const out = {};
    for (const f of fs.readdirSync(engineDir)) {
      if (f.endsWith(".py")) out[f] = fs.readFileSync(path.join(engineDir, f), "utf8");
    }
    return out;
  },
  readAudioFile: (p) => {
    const buf = fs.readFileSync(p);
    return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  },
  basename: (p) => path.basename(p),
  pickAudio: () => ipcRenderer.invoke("pick-audio"),
  platform: process.platform,
  selftestPath: process.env.NC_SELFTEST || null,
  selftestDone: (payload) => ipcRenderer.invoke("selftest-done", payload),
});
