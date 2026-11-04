// Copy the validated Python engine into the app bundle so it ships with the app
// and runs locally in Pyodide. Run on install and before start; the copy under
// engine/ is generated and gitignored.
const fs = require("node:fs");
const path = require("node:path");

const src = path.resolve(__dirname, "..", "..", "contrail");
const dst = path.resolve(__dirname, "..", "engine", "contrail");

if (!fs.existsSync(src)) {
  console.error(`sync-engine: cannot find engine source at ${src}`);
  process.exit(1);
}
fs.mkdirSync(dst, { recursive: true });
let n = 0;
for (const f of fs.readdirSync(src)) {
  if (f.endsWith(".py")) {
    fs.copyFileSync(path.join(src, f), path.join(dst, f));
    n++;
  }
}
console.log(`sync-engine: copied ${n} engine files to engine/contrail/`);
