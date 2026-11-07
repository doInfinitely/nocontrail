# No Contrail

**Audit your own recordings for aircraft-Doppler location leaks. Then scrub them.**

[nocontrail.audio](https://nocontrail.audio) · engine: `contrail` (Python, MIT) · app: coming

Your audio never leaves your machine.

Aircraft flying overhead — often too far away to hear — leave slowly drifting
tonal lines in the background of a recording. Matching the shape of those
Doppler curves against public ADS-B flight data can pin down where a video was
recorded to roughly a city block. Ordinary noise reduction and high-pass
filtering do not remove it, because the signature is the *pitch drift*, not a
frequency band.

This repo is the defensive side of that problem. `contrail` is the engine: it finds the lines in audio
*you* own, fits them to a physical flyover model, and (soon) gives you a
scrubber that destroys the signature without wrecking the voice.

> **Scope, deliberately.** This repo contains the detector and the physics.
> It does **not** contain a locator, an ADS-B matcher, or anything that turns
> someone else's video into coordinates. The fit here recovers a flyover's
> speed, miss distance and emitted frequency relative to the microphone — not
> the microphone's position on Earth. That is enough to tell you whether a clip
> leaks, and nothing more. Please keep it that way in forks.

## Status: early prototype

Validated only on synthetic clips (synthetic speech + planted flyovers).
On that bench: 14 of 15 planted flyovers recovered across five random clips;
30 of the 32 tracks returned sit on a planted line or harmonic (the other two
are short fragments that drift a few Hz off one); parameter estimates on long
tracks land within a few percent of truth. ~20 s per 90 s clip, unoptimised. No real-world audio has been tested yet. The one miss is a
fundamental sitting inside the talker's pitch band, which is the case VAD
gating (next on the list) is meant to handle.

![synthetic clip spectrogram](demo/spectrogram.png)

## What's here

```
contrail/             the engine (pip install contrail)
contrail/doppler.py   exact retarded-time Doppler model for a straight flyover
contrail/synth.py     synthetic test-clip generator (fake speech + planted planes)
contrail/detect.py    STFT → whitening → sub-bin peak tracking → fragment merge
                      → physical fit → split-and-refit for crossing lines
contrail/scrub.py     gate / notch / decoy tiers, verified by re-detection
contrail/accumulate.py clean-flyover count across a whole recording and an archive
contrail/cli.py       `contrail audit <wav>...`, `contrail scrub <in> <out>`, `contrail report`
scripts/validate.py   plants flyovers, scores recovery, renders the spectrogram
scripts/scrub_bench.py before/after per scrub tier
scripts/group_bench.py scores line-to-plane grouping against planted truth
```

## Scrubbing

Three tiers, all in the same STFT domain the detector uses, so what the
scrubber touches is what a detector sees. Default is gate + notch; decoy is
opt-in.

| tier | what it does | synthetic bench (9 planted lines, 3 clips) |
|---|---|---|
| `gate` | in frames with no voice activity, attenuate < 500 Hz by 40 dB. Scales line and floor together, so a ridge's SNR is unchanged and a fitter sees it exactly as before | 9/9 lines survive |
| `notch` | for each detected aircraft, replace bands that follow its fundamental and every harmonic up to 800 Hz, frame by frame, with floor-level noise; then re-detect and erase again | 0/9 survive |
| `decoy` | mix in 8 synthetic flyovers with random plausible parameters, at the measured level of the real lines | 9/9 survive, 15/24 decoys detected |
| all | | 1/9 survive, 14/24 decoys detected |

What that table says:

* **Gating alone is weak** when the talker's pauses are short. The tracker
  bridges gaps under a second. It gets better with real speech (longer
  pauses) and a real VAD, but don't rely on it by itself.
* **Notch does the work** — but only on lines the detector found, so scrub
  quality is capped by detector recall. That's the argument for keeping the
  two in one repo. The second pass exists for the same reason: once the loud
  lines are gone, the fragments and masked lines the first detection missed
  stand alone and track cleanly, so they get erased too (the single-pass
  bench's one survivor was a line the first detection never found).
* **Fill, don't zero.** A zeroed band leaves a hole whose edges are the most
  prominent "peaks" in the frame, and the hole follows the Doppler curve, so it
  is as trackable as the line was (measured: a hard ±4 Hz notch left a ridge
  4 Hz off the original that the detector fitted as a clean flyover). The band
  is filled with noise at the level of its surroundings instead, with phases
  taken from one time-domain noise signal so the fill survives overlap-add
  resynthesis at the level it was given (independent per-frame phases cancel
  and leave a 5-10 dB trough that traces the same curve).
* **Decoys don't remove anything**; they make a fitter's job ambiguous. On
  their own, a fitter still gets the real lines. Together with notch, what's
  left is mostly fake.

```bash
python -m contrail.cli audit demo/synthetic_leak.wav
python -m contrail.cli scrub demo/synthetic_leak.wav demo/scrubbed.wav --decoys 12
```

The notched audio correlates > 0.9 with the input in the voice band — the
notches are a few Hz wide and well below the formants. Listen anyway.

### Why the detector is shaped this way

* **1 s STFT windows.** Aircraft lines drift at a few Hz/s; voice harmonics
  jump tens of Hz between syllables. Long windows give 1 Hz bins and smear
  speech into a blur while the planes stay sharp.
* **Per-frame spectral whitening** (running median along frequency) so the
  loud voice bands don't swamp a line 24 dB below them.
* **Slope-predictive linking**, downward only. A receding plane never goes
  up in pitch, so the tracker is allowed to assume that.
* **Strongest-first, power-aware linking.** The whitened floor is a forest of
  ~100 six-dB-prominent noise peaks per frame. Served in creation order and
  blind to level, the linker let a one-frame-old noise track steal the peak of
  a 30 dB line the moment it entered earshot, and the line shredded into
  fragments — the detector returned nothing for a plane going over a quiet
  room, at any level, while finding the same plane fine if it was audible from
  frame 0. Tracks are now served loudest first and may not take a peak more
  than 20 dB above their own level (one-sided: a line's own peak jumps 15-25 dB
  when a speech harmonic sits on it, and a track must stay free to follow it).
  A track whose frequency is mostly constant is dropped as a steady tone: a
  60 Hz hum glued to a transient prefix otherwise fits "drop, then flat".
* **Physical fit as the gate.** Every candidate is fitted to the retarded-time
  flyover model (`f0`, ground speed, slant miss distance, time of closest
  approach). Anything that doesn't fit a plane to < 1 Hz RMS is dropped. This
  is what killed the false positives — a smoothness test alone was not enough.
  Note the fit must include propagation delay: at 2.5 km the delay varies by
  ~6 s over a flyover and visibly warps the curve.
* **Merge, then split.** Speech harmonics sweep across the 200–400 Hz band and
  steal peaks, fragmenting lines right at closest approach where they are
  steepest. Fragments are rejoined when a joint fit stays tight. Conversely,
  when two real planes cross, the greedy linker can glue them; a rejected long
  track is split at the point that minimises the two half-fits.

## Run it

```bash
git clone https://github.com/doInfinitely/nocontrail
cd nocontrail && pip install -e .[plot]
python scripts/validate.py demo
```

Writes `demo/synthetic_leak.wav` and `demo/spectrogram.png`, and prints
recovery stats per seed.

```python
from scipy.io import wavfile
from contrail.detect import find_tracks, leak_score

sr, y = wavfile.read("my_clip.wav")
y = y.astype("float32") / 32768
if y.ndim == 2:
    y = y.mean(axis=1)
tracks = find_tracks(y, sr)
for t in tracks:
    print(t)
print("leak score:", leak_score(tracks))
```

## Exposure accumulates

The per-window score never adds up across time, and trilateration does: three
or more clean flyovers at different geometries, anywhere in what a person has
published, constrain a position. Measured on a real 21-hour recording (with the
detector as it was before the linking fix): 52 clean flyovers in total, yet no
single hour scored above 0.41. Someone watching per-clip scores would have read
"moderate" 22 times and never seen the aggregate.

So the number the tools lead with is the **count of clean flyovers** and
whether it has reached the three that trilateration needs:

```bash
python -m contrail.cli audit stream1.wav stream2.wav --json counts.json
```

`audit` covers every second of each file, lists each clean flyover with a
timestamp to listen at, and totals across the files. One implementation,
`contrail/accumulate.py`, backs the CLI, the report (`contrail report
--accumulate counts/`) and the app.

**"Clean" is the attacker's bar, not ours.** The thresholds come from the
blind localisation pipeline that fixed a real recording site to 0.7 km: a tone
enters its fit if it is at least 12 s long, falls by at least 3 % of its
frequency, and fits the Doppler model to under 1.2 Hz rms. Our detector's own
output gates (12 s, 3 Hz, 1.0 Hz rms) already sit there, so clean is close to
detected. The bar this replaced — 40 s and 15 Hz, inherited from the scale the
window score saturates at — kept 2 of 33 detections on a real 40-minute
recording whose window score read 1.00; the attacker had used a 24 s tone from
the same file. A fit no aircraft could have produced (over 300 m/s, or airliner
speed within a few hundred metres) never counts. Flyovers confirmed by two or
more harmonics or a very tight fit are reported as a higher-confidence subset,
never used as a gate: real single-line light-aircraft passes fit at 0.3–1.5 %
and the attacker needs neither.

One guard the attacker's bar lacks: the counted line must stand **7 dB over the
floor** (median whitened level). The detector's false positives are chains of
just-over-prominence noise peaks that can meet duration, drop and fit on paper
— one 16 s "line" at 146→120 Hz fitted to 1.0 Hz rms where the audio was floor
at 1.8 dB — and they sit at 4–6 dB; real passes on the 40-minute recording ran
7–17 dB, their faint high harmonics 4.5–7 dB. The attacker absorbs its own
false positives statistically at the localisation stage; a count has to reject
them up front. `--min-power-db` lowers it if a quiet site warrants.

Two details keep the count honest:

* **Tiles are 300 s and overlap by 90 s, at 8 kHz.** 120 s tiles truncated
  44–59 s passes into views that fell under the bar, and shorter overlaps cut
  one pass into partial views whose fits disagreed (a 75 s pass came out as
  three aircraft under 90 s / 10 s). 8 kHz is plenty for a 50–800 Hz band: the
  same 40 minutes of 48 kHz audio took 968 s against 1453 s at 120 s / 16 kHz.
* **The same aircraft is recognised by its measured lines**, not its fitted
  parameters: over the audio two tiles share, their lines coincide to well
  under a hertz, and a line the grouper split off is caught as a harmonic of
  one already counted. Three real passes stacked inside 95 s on that recording
  (different slopes, speeds and miss distances) stay three.

The per-window score is still printed, labelled as what it is: one window's
score, which does not add up and is not a verdict on the file. The report's
verdict goes red when the accumulated count of clean flyovers reaches three,
however low each stretch scores on its own.

## Roadmap

1. **VAD gating** — tried and shelved. A `DetectConfig.vad` energy-VAD band-gate
   is in the code but **off**, because validating it on real speech (a 135 s
   own-voice recording + planted flyovers) showed it never beats VAD-off: turn
   it up and it deletes the aircraft lines that share the speech band; turn it
   down and it barely fires and adds spurious tracks. Across ~48 min of real
   speech (3 recordings, 12 windows), VAD-off gave the fewest spurious tracks at
   equal recovery (29/36). A targeted rejection was then tried — estimate the
   speaker's pitch per frame and drop only peaks on a voice harmonic — and it
   too lost more real recovery than the spurious tracks it removed (29→26 of 36,
   to cut 18→16 spurious), because aircraft lines cross through the voice
   harmonics. Takeaway: speech-based peak suppression doesn't help here; the
   physical Doppler fit is already the better separator of planes from voice. A
   future attempt needs a learned VAD *and* evidence it beats that fit, not
   another hand-tuned gate.


2. **Group harmonics by flyover** — done, on synthetic clips. Lines join a group
   when their measured frequency ratio snaps to a small integer ratio and one
   flyover model fits them jointly. The leak score now counts planes, not lines.
   Held-out seeds 8-15: 16/16 grouped pairs on the same plane, 16/23 same-plane
   pairs grouped. Missed merges overcount planes, usually where the tracker has
   glued lines together. One-plane clips with several lines scored about 0.33,
   down from as high as 0.95.
3. **Real VAD** (silero) in place of the energy gate; also use it in the
   detector so peaks are only picked from speech-free frames.
4. **Real audio** — the whole thing needs a bench of real recordings with
   known flyovers before anyone trusts the numbers. First data points, from one
   40-minute real room recording: the detector before the linking fix found 1
   ridge; after it, 20 (an independent attack-side run) and 33 detections over
   full coverage here, including the faint falling tone (141→116 Hz, 23 s, fit
   rms 0.4 Hz) the attacker had used. Under the attacker's bar those 33 are 20
   usable passes; with the 7 dB floor and harmonic de-duplication, **12
   distinct passes in 40 minutes**, 18 per hour, every one standing 8–17 dB
   over the floor — against a count of 2 under the old 40 s / 15 Hz bar.
   Recall, not truth: none is ADS-B-verified yet.
5. Optional ADS-B lookup of *your own declared position* to turn "a plane
   passed" into "this plane, at this time", for a stronger leak estimate.

## Background

Technique described by Colsto (Nov 2026), reported by
[TechRadar](https://www.techradar.com/computing/cyber-security/youtuber-discovers-aircraft-noise-trick-that-can-geolocate-your-videos-to-within-100m-doppler-effect-pinpoints-locations-from-just-60-seconds-of-audio).
Colsto deliberately did not release code. This project follows that choice.

## Reporting a problem

security@nocontrail.audio for anything that could put a creator at risk; issues for everything else.

## License

MIT
