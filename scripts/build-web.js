#!/usr/bin/env node
// Assemble the browser version of the app into site/app/ from the same sources
// the desktop app uses: app/renderer/* (with app://local/ URLs made relative and
// the Electron bridge swapped for web-bridge.js), contrail/*.py as the engine,
// and the Pyodide distribution from app/node_modules/pyodide (maps and docs
// dropped). Everything is served from one origin so the page's CSP can forbid
// every other connection. Output is generated, not committed; the deploy
// uploads it.
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "site", "app");
const RENDERER = path.join(ROOT, "app", "renderer");
const ENGINE = path.join(ROOT, "contrail");
const PYODIDE = path.dirname(require.resolve("pyodide/package.json", { paths: [path.join(ROOT, "app")] }));

fs.mkdirSync(OUT, { recursive: true });

// renderer, with URLs made relative and the bridge swapped in
for (const f of ["index.html", "renderer.js", "style.css"]) {
  let s = fs.readFileSync(path.join(RENDERER, f), "utf8").replaceAll("app://local/", "./");
  if (f === "index.html") {
    s = s.replace('<script type="module" src="./renderer.js"></script>', '<script type="module" src="./web-bridge.js"></script>');
    s = s.replace("The app can reach no network.", "This page's security policy forbids any connection except to load itself.");
    s = s.replace(
      "audio never leaves this window.</p>",
      "audio never leaves this window. Check it yourself: wait for “Engine ready”, turn off your Wi-Fi, then drop the file in.</p>",
    );
    s = s.replace("<title>No Contrail</title>",
      '<title>No Contrail — audit in your browser</title>\n<meta name="viewport" content="width=device-width, initial-scale=1">');
  }
  fs.writeFileSync(path.join(OUT, f), s);
}

// engine
const eng = path.join(OUT, "engine");
fs.rmSync(eng, { recursive: true, force: true });
fs.mkdirSync(eng);
const pyFiles = fs.readdirSync(ENGINE).filter((f) => f.endsWith(".py"));
for (const f of pyFiles) fs.copyFileSync(path.join(ENGINE, f), path.join(eng, f));
fs.writeFileSync(path.join(eng, "manifest.json"), JSON.stringify(pyFiles));

// pyodide runtime + the two wheels the engine needs (prewarmed into the package dir)
const pyo = path.join(OUT, "pyodide");
fs.mkdirSync(pyo, { recursive: true });
const skip = (f) => f.endsWith(".map") || f.endsWith(".d.ts") || f === "console.html" || f === "README.md" || f === "ffi.d.ts";
let bytes = 0;
for (const f of fs.readdirSync(PYODIDE)) {
  if (skip(f) || fs.statSync(path.join(PYODIDE, f)).isDirectory()) continue;
  fs.copyFileSync(path.join(PYODIDE, f), path.join(pyo, f));
  bytes += fs.statSync(path.join(pyo, f)).size;
}
console.log(`site/app ready: ${pyFiles.length} engine files, pyodide ${(bytes / 1e6).toFixed(0)} MB`);
