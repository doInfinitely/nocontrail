// Browser stand-in for the desktop app's preload bridge. Same surface the
// renderer expects (window.nocontrail), backed by a file input, a download link
// and fetches from this origin only. The engine source is fetched up front so
// engineFiles() can stay synchronous, then the unchanged renderer is loaded.
const manifest = await (await fetch("./engine/manifest.json")).json();
const engineFiles = {};
await Promise.all(manifest.map(async (f) => { engineFiles[f] = await (await fetch("./engine/" + f)).text(); }));

const picked = new Map(); // name -> ArrayBuffer, filled by pickAudio

function pickAudio() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/*,video/*,.wav,.mp3,.m4a,.mp4,.mkv,.webm,.flac,.ogg,.aac,.mov";
    input.style.display = "none";
    document.body.appendChild(input);
    let done = false;
    const finish = (v) => { if (!done) { done = true; input.remove(); resolve(v); } };
    input.addEventListener("change", async () => {
      const f = input.files && input.files[0];
      if (!f) return finish(null);
      picked.set(f.name, await f.arrayBuffer());
      finish(f.name);
    });
    // the picker was dismissed: the window regains focus with no change event
    window.addEventListener("focus", () => setTimeout(() => finish(null), 400), { once: true });
    input.click();
  });
}

function saveWav(defaultName, bytes) {
  const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = defaultName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return Promise.resolve({ saved: true, path: defaultName });
}

window.nocontrail = {
  engineFiles: () => engineFiles,
  readAudioFile: (name) => {
    const b = picked.get(name);
    picked.delete(name);
    return b;
  },
  basename: (p) => String(p).split(/[\\/]/).pop(),
  pickAudio,
  saveWav,
  platform: "web",
  selftestPath: null,
  selftestScrub: false,
  scrubOutPath: null,
  selftestWrite: async () => {},
  selftestDone: async () => {},
};

await import("./renderer.js");
