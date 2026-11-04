// No Contrail desktop app — main process.
// Core promise: audio is processed locally and nothing is uploaded. Bundled
// assets (renderer, engine, Pyodide) are served over a private app:// scheme;
// every other network request is cancelled at the session level, so a bug or a
// dependency cannot exfiltrate audio.
const { app, BrowserWindow, session, dialog, ipcMain, protocol, net } = require("electron");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const RENDERER_DIR = path.join(__dirname, "renderer");
const ENGINE_DIR = path.join(__dirname, "engine");
const PYODIDE_DIR = path.dirname(require.resolve("pyodide/package.json"));

const TYPES = {
  ".mjs": "text/javascript", ".js": "text/javascript", ".json": "application/json",
  ".wasm": "application/wasm", ".html": "text/html", ".css": "text/css",
  ".zip": "application/zip", ".whl": "application/octet-stream", ".map": "application/json",
  ".data": "application/octet-stream", ".ts": "application/octet-stream",
};

protocol.registerSchemesAsPrivileged([{
  scheme: "app",
  privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
}]);

function resolveAppURL(url) {
  // Everything is one origin (app://local) so the renderer, engine and Pyodide
  // are same-origin for CSP/fetch/import. The first path segment selects a root.
  const u = new URL(url);
  if (u.hostname !== "local") return null;
  const parts = decodeURIComponent(u.pathname).replace(/^\/+/, "").split("/");
  let root, rel;
  if (parts[0] === "pyodide") { root = PYODIDE_DIR; rel = parts.slice(1).join("/"); }
  else if (parts[0] === "engine") { root = ENGINE_DIR; rel = parts.slice(1).join("/"); }
  else { root = RENDERER_DIR; rel = parts.join("/"); }
  const abs = path.normalize(path.join(root, rel));
  if (abs !== root && !abs.startsWith(root + path.sep)) return null; // no escaping the root
  return abs;
}

function hardenNetwork(ses) {
  ses.webRequest.onBeforeRequest((details, cb) => {
    const u = details.url;
    const ok = u.startsWith("app://") || u.startsWith("devtools://") || u.startsWith("blob:");
    if (!ok) console.warn("[blocked network]", u.slice(0, 80));
    cb({ cancel: !ok });
  });
}

let mainWin = null;
function createWindow() {
  const win = new BrowserWindow({
    width: 980, height: 760, minWidth: 720, minHeight: 560,
    backgroundColor: "#0B1020", title: "No Contrail",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
    },
  });
  win.removeMenu?.();
  if (process.env.NC_DEBUG) {
    win.webContents.on("console-message", (_e, _lvl, msg) => console.log("[renderer]", msg));
    win.webContents.on("did-fail-load", (_e, code, desc, url) => console.log("[did-fail-load]", code, desc, url));
    win.webContents.on("render-process-gone", (_e, d) => console.log("[render-gone]", JSON.stringify(d)));
  }
  win.loadURL("app://local/index.html");
  mainWin = win;
  return win;
}

ipcMain.handle("selftest-done", async (_e, payload) => {
  console.log("[selftest]", payload);
  if (process.env.NC_SHOT && mainWin) {
    try {
      const img = await mainWin.webContents.capturePage();
      require("node:fs").writeFileSync(process.env.NC_SHOT, img.toPNG());
      console.log("[shot] wrote", process.env.NC_SHOT);
    } catch (e) { console.log("[shot error]", String(e)); }
  }
  if (process.env.NC_SELFTEST) app.exit(payload && payload.ok ? 0 : 3);
});

ipcMain.handle("pick-audio", async () => {
  const r = await dialog.showOpenDialog({
    title: "Choose a recording to audit",
    properties: ["openFile"],
    filters: [{ name: "Audio / video", extensions: ["wav", "mp3", "m4a", "aac", "mp4", "mov", "webm", "ogg", "flac"] }],
  });
  return r.canceled ? null : r.filePaths[0];
});

app.whenReady().then(() => {
  protocol.handle("app", (req) => {
    const abs = resolveAppURL(req.url);
    if (!abs) return new Response("not found", { status: 404 });
    const ext = path.extname(abs).toLowerCase();
    const headers = { "content-type": TYPES[ext] || "application/octet-stream" };
    return net.fetch(pathToFileURL(abs).toString()).then(
      (r) => new Response(r.body, { status: r.status, headers }),
      () => new Response("not found", { status: 404 }),
    );
  });
  hardenNetwork(session.defaultSession);
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
