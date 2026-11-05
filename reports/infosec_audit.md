# Aircraft-Doppler location-leak audit — technical findings

**Subject:** 26 archived VODs (135.7 h), consenting individual, internal audit
**Tooling:** `contrail` detector + `scripts/audit_video.py` (sampled) and `scripts/accumulate.py` (full-coverage), run over the full archive
**Date:** 2026-11-05
**Classification:** internal. Contains no coordinates, flyover timestamps, or per-flyover
geometry. See *Deliberate omissions*. Do not add those fields if this is ever circulated.

---

## Bottom line

A measurable aircraft-Doppler signature is present in the archive. **Per 90-second window the
signal is faint** (peak leak score 0.42 of 1.0; no window reached even two clean flyovers). Taken
at face value that reads as "low risk."

**That read is wrong, and the reason is the headline finding:** the leak score is a per-window
measure, and what actually locates a person is **clean flyovers accumulated across a whole stream.**
The earlier audit sampled only 3.8% of the footage; we have since **processed all 135.7 h at full
coverage** (`accumulate.py`, counts only). The result is no longer an extrapolation:

> **22 of 26 streams individually contain at least 3 clean flyovers — the detector's own
> trilateration bar — over their full duration. The archive holds 264 clean flyovers in total.**

Of the 4 streams that do not cross the bar, **3 are broken/near-zero-length files** (0.0–0.3 h,
effectively untested); only **one** genuine stream (2026-09-11, 2 clean) falls just short. So of
the 23 streams with real content, **22 cross the bar.** The low per-window score was an artifact of
how and how little we first measured, not evidence of safety.

Treat the audit as a **floor on exposure, not a ceiling.**

---

## The accumulation risk (primary finding)

Locating a microphone from flyovers is trilateration: three or more clean flyovers, **at different
geometries and therefore different times**, constrain a position. The detector's own docstring says
as much — `leak_score` divides by 3 because "three or more clean, long flyovers is enough to
trilaterate."

But the score only ever looks **inside one 90-second window**:

```
leak_score = min(1, Σ_flyovers[ min(1, duration/40s) × min(1, drop/15Hz) ] / 3)
```

Two consequences make the per-window number misleading as a risk measure:

1. **It never sums across time.** Three clean flyovers spread across a 10-hour stream — the exact
   shape of a real locating attack — score low in every individual window and never combine. The
   metric is structurally blind to the attack it describes.
2. **The first pass sampled ~2% of each stream.** `audit_video.py` listens to 8 windows of 90 s —
   5.2 h out of 135.7 h (3.8%). That cannot see whole-stream accumulation.

We therefore ran the full archive at **100% coverage** with `accumulate.py`, which tiles each entire
file, de-duplicates flyovers across tile boundaries, and counts only the **clean** ones
(≥40 s duration, ≥15 Hz drop) — the long, deep flyovers that carry usable geometry. Output is counts
only: no timestamps, no geometry, no location. The measured result:

| | |
|---|---|
| Coverage | **100%** of 135.7 h (all 26 streams, 0 tile errors) |
| Flyover detections, full archive | **1,142** |
| **Clean flyovers (≥40 s, ≥15 Hz), full archive** | **264** |
| **Streams crossing the 3-clean-flyover trilateration bar** | **22 of 26** |
| Streams below the bar | 4 — of which **3 are broken/near-zero files**; 1 real stream (2 clean) |
| Per-window peak leak (what the streamer report shows) | **0.42** |

The gap between "0.42, faint" and "22 of 26 streams over the bar" is the entire point. **The
per-window score and the 2% sample both systematically understated location exposure.** The
measured count came in **above** the earlier 18-of-26 extrapolation. For a location-leak audit, that
understatement — and the fact that nearly every substantive stream independently crosses the bar —
is the finding.

These are *clean* flyovers, not raw detections: the 264 figure already applies the ≥40 s / ≥15 Hz
filter, so each one is a flyover long and deep enough to fit well and contribute geometry. Crossing
the bar needs only 3 of them in a single stream; most streams have many more. Per-stream counts are
in the deletion-priority table below.

---

## Deletion priority (per-stream clean-flyover counts)

Full-coverage clean-flyover counts per stream, ranked. Counts only — no timestamps or geometry.
Dates are the stream dates, already public on the channel. This is the list to work down if she
deletes by priority.

**Tier 1 — extreme (delete first).** Long streams, double-digit clean flyovers; each one alone
far exceeds the trilateration bar.

| Stream | Hours | Clean flyovers |
|---|---:|---:|
| 2026-10-14 | 11.6 | **32** |
| 2026-10-13 | 12.1 | **25** |
| 2026-10-17 | 10.5 | **25** |
| 2026-10-15 (pt 2) | 11.1 | **21** |

**Tier 2 — high.** Comfortably over the bar.

| Stream | Hours | Clean flyovers |
|---|---:|---:|
| 2026-10-30 | 6.1 | 16 |
| 2026-09-30 | 5.0 | 13 |
| 2026-09-19 | 2.8 | 12 |
| 2026-09-23 | 3.3 | 12 |
| 2026-09-26 | 4.2 | 12 |
| 2026-10-20 | 10.2 | 11 |
| 2026-10-23 | 3.5 | 11 |
| 2026-10-16 | 11.6 | 10 |
| 2026-09-25 | 3.0 | 9 |
| 2026-10-09 | 5.5 | 9 |

**Tier 3 — over the bar.** Fewer clean flyovers but still ≥3; still enough to trilaterate.

| Stream | Hours | Clean flyovers |
|---|---:|---:|
| 2026-09-09 | 3.5 | 8 |
| 2026-10-03 | 4.2 | 8 |
| 2026-09-12 | 3.4 | 6 |
| 2026-10-02 | 3.4 | 6 |
| 2026-09-29 | 5.1 | 5 |
| 2026-10-08 | 4.1 | 5 |
| 2026-10-06 | 4.1 | 3 |
| 2026-10-27 | 3.4 | 3 |

**Below the bar.** 2026-09-11 (3.4 h, 2 clean — just under). Three files are broken/near-zero and
effectively untested: 2022-05-03 (0.0 h), 2024-07-13 (0.3 h), 2026-10-15 pt 1 (0.0 h).

Note the hours column: duration is **not** a safe proxy for exposure. 2026-09-19 (2.8 h) carries 12
clean flyovers while 2026-10-16 (11.6 h) carries 10 — a shorter stream can be the higher risk. Delete
by count, not by length.

---

## Audio exposure

Two passes. The **full-coverage** pass (below, right column) is authoritative; the **sampled** pass
is retained only to show how far the per-window view undercounts.

| Measure | Sampled pass (3.8%) | Full-coverage pass (100%) |
|---|---|---|
| Streams processed | 26 | 26 |
| Total footage | 135.7 h | 135.7 h |
| Flyover detections | 35 | **1,142** |
| Clean flyovers (≥40 s, ≥15 Hz) | — (not resolved) | **264** |
| Streams crossing the 3-clean trilateration bar | 18 (extrapolated) | **22 (measured)** |
| Peak per-window leak score | 0.42 | — (per-window metric, not accumulated) |

In the full pass, 23 of 26 streams carry real content (the other 3 are broken/near-zero files), and
**22 of those 23 cross the bar** — only 2026-09-11 (2 clean) falls short. Whole-stream accumulation,
not the per-window score, is what matters.

Per-stream clean-flyover counts are in `accumulate/*.json` (counts only — `clean_flyovers`,
`flyover_detections`, `crosses_trilateration_bar`). These are safe to read and to circulate
internally. The sampled pass's per-flyover `groups` arrays in `audits/*.json` are **not** safe to
circulate — see omissions.

---

## Why ordinary defenses don't apply

- The signature is **pitch drift over time**, not energy in a frequency band. High-pass filters and
  broadband noise reduction leave the drifting line intact.
- Twitch's audio compression thins the low-frequency hum the technique leans on, which is the most
  likely reason the per-window signal is faint here. **This is a property of the distribution path,
  not of the room** — raw mic audio, local recordings, or a higher-bitrate platform would expose
  more. Do not treat the Twitch result as the safe baseline for other outlets.

---

## Detector limitations (bearing on confidence)

- **Validated on synthetic audio only.** Per the `contrail` README: 14/15 planted flyovers
  recovered on synthetic clips, no real-world validation. Detections here are a strong indication,
  not ground truth, in both directions — false negatives are plausible, so absence of signal in a
  stream is weak evidence of safety.
- **0.5 is not a calibrated threshold.** The "around 0.5 is where matching starts to work" line in
  the streamer report is explanatory prose, not a measured operating point. Do not use the 0.42
  peak as "below the danger line."
- **The sampled pass undercounted by design.** 3.8% coverage with a per-window metric could not
  estimate whole-stream accumulation; the full-coverage pass resolved it, and the true count came in
  *above* the extrapolation. Where the detector itself has blind spots (synthetic-only validation,
  above), the full-coverage counts are still a floor, not a ceiling.

---

## Recommendations

**Measurement (done):**
1. ~~Run the whole stream and accumulate.~~ **Done** — all 135.7 h processed at full coverage
   (`accumulate.py`, counts only). Result: 264 clean flyovers, **22 of 26 streams over the bar**. The
   18-of-26 extrapolation was, if anything, optimistic. Per-stream counts drive the deletion-priority
   table above.
2. **Report whole-stream accumulation, not just per-window peak**, in future audits. A per-window
   maximum is the wrong headline for a trilateration threat — this audit is the case study.

**Mitigation for the subject:**
3. **Noise gate on the mic** removes room tone during speech pauses, where the lines are cleanest —
   more effective here than any post filter.
4. **`contrail scrub` before publishing raw audio.** The notch tier removes detected lines; pair it
   with the decoy tier so a fitter can't lock onto the survivors. Gating alone is weak.
5. **Treat raw/local recordings and non-Twitch outlets as higher risk** than these VODs.

---

## Decision guidance (delete / move / soundproof)

The subject is using this audit to decide whether to delete VODs, expedite a move, or soundproof.
These measure different things; keep them separate.

- **The accumulation count measures exploitability of *published audio*** — whether the material to
  locate the subject exists in public VODs. It does **not** establish that anyone has located them,
  or that their *current address* is recoverable.

| Decision | What it addresses | Call |
|---|---|---|
| **Soundproof / mic noise gate** | Future exposure | **Do it regardless.** Lowest-regret, cheap, attacks the signature at its source. Signal is present in the audio. |
| **Delete VODs** | Already-published exposure | **Yes — and it's most of the archive.** 22 of 26 streams cross the bar; work the deletion-priority table top-down (Tier 1 first). Caveat: deletion is **not retroactive** — third parties who downloaded a VOD keep it. Lowers ongoing, not past, risk. |
| **Expedite move** | Whether *current location* is already derivable | **Audit can't decide this.** High counts mean the ingredients are public — reason not to dismiss a move — but not proof the exact spot is recoverable. |

**Confirming actual locatability** (for the move decision) is a *self-test the subject runs locally*:
score their real coordinates against nearby decoys (`doppler/pipeline/truth_check.py`). If the true
location beats the decoys, the audio discriminates their position and the move is well-founded. This
requires their real location and must stay on their own machine — the auditor does not collect,
handle, or process it. Note this is a **known-location self-check**, not the Lower-48 location
*search*, which this engagement does not run against the subject.

**Escalation:** if the move is driven by a specific person or threat, that exceeds what audio
analysis settles. A digital-safety / anti-stalking organization or law enforcement should run a
threat assessment **in parallel**, not after these numbers.

A subject-facing plain-language version of this guidance is in `reports/for_kairuichan.md`.

---

## Deliberate omissions

This report excludes, by design, everything that would help convert a detection into a position:
per-flyover **speed, miss distance, and closest-approach geometry**; **within-stream timestamps**
of flyovers; and any ADS-B, map, coordinate, or region data (none was used — the detector is
audio-only). Stream **dates** are retained because they are already public on the subject's channel
and a date alone is not a location.

If this document is ever escalated to a shareable artifact, it goes out at this altitude —
aggregate findings and the accumulation argument — and the `audits/*.json` `groups` fields stay
internal.
