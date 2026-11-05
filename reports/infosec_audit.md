# Aircraft-Doppler location-leak audit — technical findings

**Subject:** 26 archived VODs (135.7 h), consenting individual, internal audit
**Tooling:** `contrail` detector + `scripts/audit_video.py`, run over the full archive
**Date:** 2026-11-04
**Classification:** internal. Contains no coordinates, flyover timestamps, or per-flyover
geometry. See *Deliberate omissions*. Do not add those fields if this is ever circulated.

---

## Bottom line

A measurable aircraft-Doppler signature is present in the archive. **Per 90-second window the
signal is faint** (peak leak score 0.42 of 1.0; no window reached even two clean flyovers). Taken
at face value that reads as "low risk."

**That read is wrong, and the reason is the headline finding:** the leak score is a per-window
measure and the audit sampled only **3.8% of the footage**. Neither the score nor the sample
captures the thing that actually locates a person — **flyovers accumulated across a whole stream.**
On a first-order extrapolation, **18 of 26 streams individually contain enough flyovers over their
full duration to cross the detector's own trilateration bar.** The low score is an artifact of how
and how little we measured, not evidence of safety.

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
2. **We sampled ~2% of each stream.** `audit_video.py` listens to 8 windows of 90 s. Across the
   archive that is **5.2 h listened out of 135.7 h (3.8%)**, and in that sliver the detector found
   **35 flyovers**.

Extrapolating each stream's in-sample flyover rate to its full duration (uniform-rate estimate):

| | |
|---|---|
| Coverage actually audited | **3.8%** of 135.7 h |
| Flyover detections in that sample | **35** |
| Est. flyover detections across all full streams | **~1,090** (order of magnitude, not a count) |
| Streams whose full audio likely exceeds the 3-flyover bar | **18 of 26** |
| Per-window peak leak (what the streamer report shows) | **0.42** |

The gap between "0.42, faint" and "18 of 26 streams over the bar" is the entire point. **The
per-window score and the 2% sample both systematically understate location exposure.** For a
location-leak audit, that understatement is the finding.

### Caveat on the extrapolation
The ~1,090 figure counts *detections*, not *clean* flyovers. Trilateration needs flyovers that are
long (≈40 s+) and deep (≈15 Hz+ drop); most in-sample detections were shorter and shallower, which
is why per-window scores stayed low. So the number overstates *usable* flyovers. It does **not**
follow that risk is low: crossing the bar needs only **3 clean flyovers in a whole stream**, and a
stream with dozens of detections has many chances to contain them. The honest statement is *the
clean-flyover count per full stream is unknown and is very unlikely to be the near-zero the
per-window score implies.* Resolving it is the top recommendation below.

---

## Audio exposure

| Measure | Value |
|---|---|
| Streams audited | 26 |
| Total footage | 135.7 h |
| Streams with ≥1 detected flyover in sample | **18 of 26** |
| Windows with a flyover | 30 of 208 scored |
| Max flyovers in any single window | 2 |
| Peak per-window leak score | 0.42 |

Eight streams showed no signal in-sample; two of those are near-zero-length/broken files
(effectively untested), so the "clean" count is at most six, not eight.

Per-stream leak scores and flyover-window counts are in `audits/*.json` (`max_leak`,
`windows_with_flyover`). They are safe to read internally. The per-flyover `groups` arrays in those
files are **not** safe to circulate — see omissions.

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
- **The sample undercounts by design.** 3.8% coverage with a per-window metric cannot estimate
  whole-stream accumulation. This is a limitation of the audit, not of the subject's exposure.

---

## Recommendations

**Measurement (do first — it decides severity):**
1. **Run the whole stream, not a sample, and accumulate.** Process full VODs and count *clean*
   flyovers (≥40 s, ≥15 Hz drop) across each entire stream. This converts the ~1,090-detection
   estimate into an actual clean-flyover count per stream and settles whether the 18-of-26
   accumulation estimate is real. Keep the output as counts only — no geometry, no timestamps.
2. **Report whole-stream accumulation, not just per-window peak**, in future audits. A per-window
   maximum is the wrong headline for a trilateration threat.

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
| **Delete VODs** | Already-published exposure | **Yes for high-count streams.** Caveat: deletion is **not retroactive** — third parties who downloaded a VOD keep it. Lowers ongoing, not past, risk. |
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
