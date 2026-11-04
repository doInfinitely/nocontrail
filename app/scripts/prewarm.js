// Download and cache the numpy/scipy wheels into node_modules/pyodide so the
// packaged app carries them and fetches nothing at runtime. Safe to re-run.
const { loadPyodide } = require("pyodide");
(async () => {
  const py = await loadPyodide();
  await py.loadPackage(["numpy", "scipy"]);
  console.log("prewarm: numpy + scipy cached locally");
})().catch((e) => { console.error(e); process.exit(1); });
