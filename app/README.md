# app

Desktop app for No Contrail: audit a recording for aircraft-Doppler location
leaks, entirely on your own machine. Nothing is uploaded.

## How it works

- **Electron** shell. The renderer decodes the chosen file with the Web Audio
  API, downmixes to mono and resamples to 16 kHz, then samples windows across
  it.
- **The real engine, unchanged.** The Python `contrail` package runs in the
  renderer via **Pyodide** (numpy + scipy compiled to WASM). The same validated
  code the CLI uses — no reimplementation to drift from it. `scripts/sync-engine.js`
  copies `../contrail/*.py` into the bundle on install.
- **Local only, enforced.** The renderer, engine and Pyodide are served over a
  private `app://local` scheme. Every other network request is cancelled in the
  main process (`hardenNetwork`), so a bug or dependency cannot send audio
  anywhere. The Pyodide wheels are bundled, so nothing is fetched at runtime.

## Run

```bash
cd app
npm install            # installs electron + pyodide; sync-engine runs on postinstall
npm run prewarm        # one-time: cache the numpy/scipy wheels locally for offline use
npm start              # re-syncs the engine and launches the app
```

If `npm install` skips Electron's binary (some environments block install
scripts), run `node node_modules/electron/install.js` once.

## Status

Early. Audit and scrub both run and are verified end-to-end: a planted-leak clip
audits to 1.00 / 5 flyovers and a clean clip to 0; scrubbing that clip notches
the detected lines, mixes in decoys, and saves a 16 kHz mono WAV with the real
lines reduced. The detector itself is still validated only on synthetic audio.
Not yet built: full-resolution (stereo / original-rate) scrub output and
Windows/Linux packages.

## Release

```bash
cd app
npm run dist           # signed DMG + zip for arm64 and x64 in app/dist/
```

Signing uses the Developer ID Application certificate in the login keychain.
Notarization runs in `build/notarize.js` only when Apple credentials are in the
environment (see that file); without them the build is signed but not
notarized, and Gatekeeper will ask the user to open it from System Settings.
Artifacts are named `NoContrail-mac-<arch>.dmg` without a version so the
site's `releases/latest/download/` links stay valid across releases.

### Test / debug env vars

- `NC_DEBUG=1` — forward renderer console and load failures to stdout.
- `NC_SELFTEST=/path/to.wav` — audit that file on launch, print the result, exit.
- `NC_SHOT=/path/to.png` — with a self-test, capture the result screen to PNG.
- `NC_SELFTEST_SCRUB=1` / `NC_SCRUB_OUT=/path/to.wav` — also scrub the self-test clip and write the result.
