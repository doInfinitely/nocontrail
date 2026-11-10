#!/usr/bin/env node
// Vercel build step for the browser version (also run locally by
// scripts/build-web.js). Assembles site/app/ from the same sources the desktop
// app uses — app/renderer/* with app://local/ URLs made relative and the
// Electron bridge swapped for web-bridge.js, contrail/*.py as the engine — and
// fetches the Pyodide runtime plus the numpy/scipy wheels so everything is
// served from one origin and the page's CSP can forbid every other connection.
// Output goes to site/public (Vercel's output directory, git-ignored); the static
// pages are copied in beside it. Pyodide is fetched, not committed (80 MB), and
// cached there between local runs.
const fs = require("node:fs");
const path = require("node:path");
const { pipeline } = require("node:stream/promises");
const zlib = require("node:zlib");

const PYODIDE_VERSION = "0.27.2";
const WHEELS = ["numpy", "scipy", "openblas"]; // what `import scipy` needs, per pyodide-lock.json

const SITE = __dirname;
const ROOT = path.resolve(SITE, "..");
const PUBLIC = path.join(SITE, "public");            // Vercel's output directory (git-ignored)
const OUT = path.join(PUBLIC, "app");
const STATIC = ["index.html", "audits.html", "favicon.ico", "favicon.svg", "apple-touch-icon.png"];
const RENDERER = path.join(ROOT, "app", "renderer");
const ENGINE = path.join(ROOT, "contrail");

for (const d of [RENDERER, ENGINE]) {
  if (!fs.existsSync(d)) {
    console.error(`build: missing ${d} — the build needs the whole repository, not just site/`);
    process.exit(1);
  }
}
fs.mkdirSync(OUT, { recursive: true });
for (const f of STATIC) fs.copyFileSync(path.join(SITE, f), path.join(PUBLIC, f));
fs.copyFileSync(path.join(SITE, "app", "web-bridge.js"), path.join(OUT, "web-bridge.js"));

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

// pyodide runtime (npm tarball) + wheels (pyodide CDN), cached by version
async function download(url, dest) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} for ${url}`);
  await pipeline(res.body, fs.createWriteStream(dest));
}

function untarTo(tgz, dir) {
  // the npm tarball is a plain ustar archive under gzip; entries live under "package/"
  const buf = zlib.gunzipSync(fs.readFileSync(tgz));
  let off = 0;
  while (off + 512 <= buf.length) {
    const name = buf.subarray(off, off + 100).toString().replace(/\0.*$/, "");
    if (!name) break;
    const size = parseInt(buf.subarray(off + 124, off + 136).toString().replace(/\0.*$/, "").trim(), 8) || 0;
    const type = String.fromCharCode(buf[off + 156]);
    const prefix = buf.subarray(off + 345, off + 500).toString().replace(/\0.*$/, "");
    const full = (prefix ? prefix + "/" : "") + name;
    off += 512;
    if (type === "0" || type === "\0") {
      const rel = full.replace(/^package\//, "");
      const keep = !rel.endsWith(".map") && !rel.endsWith(".d.ts") && !["console.html", "README.md", "package.json"].includes(rel);
      if (keep && !rel.includes("/")) fs.writeFileSync(path.join(dir, rel), buf.subarray(off, off + size));
    }
    off += Math.ceil(size / 512) * 512;
  }
}

(async () => {
  const pyo = path.join(OUT, "pyodide");
  const stamp = path.join(pyo, `.v${PYODIDE_VERSION}`);
  if (!fs.existsSync(stamp) || !fs.existsSync(path.join(pyo, "pyodide.asm.wasm"))) {
    fs.rmSync(pyo, { recursive: true, force: true });
    fs.mkdirSync(pyo, { recursive: true });
    const tgz = path.join(pyo, "pyodide.tgz");
    console.log(`build: fetching pyodide ${PYODIDE_VERSION} runtime`);
    await download(`https://registry.npmjs.org/pyodide/-/pyodide-${PYODIDE_VERSION}.tgz`, tgz);
    untarTo(tgz, pyo);
    fs.rmSync(tgz);
    const lock = JSON.parse(fs.readFileSync(path.join(pyo, "pyodide-lock.json"), "utf8"));
    for (const name of WHEELS) {
      const file = lock.packages[name].file_name;
      console.log(`build: fetching ${file}`);
      await download(`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/${file}`, path.join(pyo, file));
    }
    fs.writeFileSync(stamp, "");
  }
  let bytes = 0;
  for (const f of fs.readdirSync(pyo)) bytes += fs.statSync(path.join(pyo, f)).size;
  console.log(`build: site/public ready — ${pyFiles.length} engine files, pyodide ${(bytes / 1e6).toFixed(0)} MB`);
})().catch((e) => { console.error("build failed:", e.message || e); process.exit(1); });
