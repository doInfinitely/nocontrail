"""Render a folder of per-VOD audit JSON into a one-page HTML report for the
streamer. Written for a non-technical reader: verdict first, one line per
stream with a timestamp they can go listen to, physics in an appendix.

    contrail report leak_reports/ --name Fareeha -o fareeha_audit.html
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

GREEN, AMBER, RED = 0.25, 0.5, 1.0   # leak-score bands


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _band(x: float) -> tuple[str, str, str]:
    if x < GREEN:
        return "clear", "#1F8A4C", "#E3F5EA"
    if x < AMBER:
        return "faint", "#B7791F", "#FBF1DC"
    return "leak", "#B42318", "#FCE4E2"


def _date_from(name: str) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else name[:10]


def _title_from(name: str) -> str:
    t = re.sub(r"^\d{4}-\d{2}-\d{2}\s*-\s*", "", name)
    t = re.sub(r"\s*\[v\d+\]\.(json|mp4)$", "", t)
    return t


def _plausible(g: dict) -> bool:
    """A real flyover: plausible speed and either harmonics or a tight fit.
    One rule, shared with the CLI and the app (contrail.accumulate)."""
    from .accumulate import plausible_params
    return plausible_params(g["v_mps"], g["miss_m"], len(g.get("harmonics", [])), g.get("rms_frac", 1))


def _clean(g: dict, min_dur: float = 40.0, min_drop: float = 15.0) -> bool:
    """Long and deep enough to carry usable geometry. Older audit JSON has no
    duration/drop per group; those count as clean if plausible."""
    return g.get("dur_s", min_dur) >= min_dur and g.get("drop_hz", min_drop) >= min_drop


def _load_full_coverage(folder: Path | None) -> dict | None:
    """Totals from full-coverage JSON (contrail audit --json, or accumulate.py)."""
    if not folder:
        return None
    clean = plaus = 0
    seconds = 0.0
    n = 0
    for p in sorted(Path(folder).glob("*.json")):
        with open(p) as f:
            rep = json.load(f)
        files = rep.get("files", [rep])          # `contrail audit --json` wraps per-file summaries
        for r in files:
            if "clean_flyovers" not in r:
                continue
            n += 1
            clean += r["clean_flyovers"]
            plaus += r.get("clean_plausible_flyovers", 0)
            seconds += r.get("duration_s", 0.0)
    if not n:
        return None
    return {"files": n, "hours": seconds / 3600, "clean": clean, "plausible": plaus}


def build(folder: Path, name: str, auditor: str = "", accumulate_dir: Path | None = None) -> str:
    reports = []
    for p in sorted(folder.glob("*.json")):
        with open(p) as f:
            rep = json.load(f)
        # a window skipped for a stream-gap/empty extract has {"error": ...}
        # instead of leak/groups; treat it as a scored-but-empty window so the
        # rest of the builder can assume every window has leak + groups.
        for w in rep.get("windows", []):
            w.setdefault("leak", 0.0)
            w.setdefault("flyovers", 0)
            w.setdefault("groups", [])
        reports.append(rep)

    total_h = sum(r["duration_s"] for r in reports) / 3600
    n_win = sum(r["windows_sampled"] for r in reports)
    win_s = reports[0]["window_s"] if reports else 90
    listened_h = n_win * win_s / 3600
    frac = listened_h / total_h if total_h else 0
    peak = max((r["max_leak"] for r in reports), default=0.0)
    hit_windows = sum(1 for r in reports for w in r["windows"] if w["groups"])
    confirmed = sum(1 for r in reports for w in r["windows"] for g in w["groups"] if _plausible(g))
    # Exposure accumulates. Count the clean, plausible flyovers across every
    # stretch we listened to, turn that into a rate, and project it over the
    # whole archive; fold in full-coverage counts when we have them.
    n_clean = sum(1 for r in reports for w in r["windows"] for g in w["groups"] if _plausible(g) and _clean(g))
    rate_h = n_clean / listened_h if listened_h else 0.0
    projected = rate_h * total_h
    full = _load_full_coverage(accumulate_dir)
    accumulated = full["plausible"] if full else n_clean

    # The verdict must track PLAUSIBLE detections, not the raw leak score: a
    # single physically-impossible fit (e.g. airliner speed at rooftop height)
    # can push one window's score past 0.5 and cry wolf. Band on the strongest
    # window that actually contains a plausible flyover.
    peak_plausible = max(
        (w["leak"] for r in reports for w in r["windows"] if any(_plausible(g) for g in w["groups"])),
        default=0.0,
    )
    verdict_word, verdict_fg, verdict_bg = _band(peak_plausible)
    if accumulated >= 3:
        # Three clean flyovers at different geometries is what trilateration
        # needs, and they accumulate across an archive however low each
        # stretch scores on its own. This outranks the per-stretch band.
        verdict_word, verdict_fg, verdict_bg = _band(RED)
        verdict = "Enough clean flyovers to locate you have accumulated across your archive."
        if full:
            explain = (f"Listening to every second of {full['files']} stream(s) ({full['hours']:.1f} h) found "
                       f"{full['clean']} clean flyovers, {full['plausible']} of them physically confirmed. Three at "
                       f"different angles is enough to triangulate, and an attacker uses the whole archive, not one "
                       f"stream. No single stretch needs to score high for this; most of yours don't. Scrubbing the "
                       f"flagged VODs and changing the setup are both worth doing.")
        else:
            explain = (f"In the {listened_h:.1f} h we listened to, {n_clean} clean, physically plausible flyovers "
                       f"accumulated — about {rate_h:.1f} per hour of footage, so roughly {projected:.0f} across your "
                       f"{total_h:.0f} h archive. Three at different angles is enough to triangulate, and an attacker "
                       f"uses the whole archive, not one stream. Scrubbing the flagged VODs and changing the setup "
                       f"are both worth doing.")
    elif peak_plausible < GREEN:
        verdict = "No usable location leak found in this sample."
        explain = (f"Nothing in the {n_win} stretches we listened to had the signature an attacker "
                   f"would need. The few candidates were single lines with no harmonic confirmation, "
                   f"or fits at physically impossible speeds — what a chance match to game or music "
                   f"audio looks like, not a real flyover.")
    elif peak_plausible < AMBER:
        verdict = "Faint traces, probably not usable, worth a closer look."
        explain = ("At least one stretch showed a plausible flyover. On its own that is not enough to "
                   "locate you, but it means the mic is picking up the room. See the flagged timestamps.")
    else:
        verdict = "A usable leak is present. Act before the next stream."
        explain = ("At least one stretch contains a flyover signature clear enough to match against "
                   "flight data. The flagged timestamps show where. Scrubbing those VODs and changing "
                   "the setup are both worth doing.")

    rows = []
    for r in reports:
        worst = max(r["windows"], key=lambda w: w["leak"]) if r["windows"] else None
        word, fg, bg = _band(r["max_leak"])
        when = _hms(worst["start_s"]) if worst and worst["groups"] else "—"
        n_hits = sum(1 for w in r["windows"] if w["groups"])
        rows.append(f"""
        <tr>
          <td class="date">{_date_from(r['file'])}</td>
          <td class="title">{html.escape(_title_from(r['file']))}</td>
          <td class="num">{r['duration_s']/3600:.1f} h</td>
          <td><span class="pill" style="color:{fg};background:{bg}">{word}</span></td>
          <td class="num">{r['max_leak']:.2f}</td>
          <td class="num">{n_hits}/{r['windows_sampled']}</td>
          <td class="mono">{when}</td>
        </tr>""")

    appendix = []
    for r in reports:
        for w in r["windows"]:
            for g in w["groups"]:
                tag = "plausible" if _plausible(g) else "unlikely"
                appendix.append(
                    f"<tr><td class='date'>{_date_from(r['file'])}</td><td class='mono'>{_hms(w['start_s'])}</td>"
                    f"<td class='num'>{g['v_mps']:.0f} m/s</td><td class='num'>{g['miss_m']:.0f} m</td>"
                    f"<td class='num'>{len(g.get('harmonics', []))}</td><td class='num'>{g.get('rms_frac', 0)*100:.2f}%</td>"
                    f"<td>{tag}</td></tr>")
    appendix_html = "".join(appendix) or "<tr><td colspan='7' class='muted'>No candidates in any window.</td></tr>"

    by = f" · by {html.escape(auditor)}" if auditor else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Location-leak audit — {html.escape(name)}</title>
<style>
:root{{--ink:#141413;--muted:#6B6B66;--line:#E6E4DE;--bg:#FFFFFF}}
@media (prefers-color-scheme:dark){{:root{{--ink:#ECEBE6;--muted:#9A9890;--line:#2C2B28;--bg:#141413}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:820px;margin:0 auto;padding:40px 20px 64px}}
h1{{font-size:28px;margin:0 0 4px;letter-spacing:-0.01em}}
h2{{font-size:18px;margin:36px 0 10px}}
.sub{{color:var(--muted);font-size:14px;margin:0 0 28px}}
.verdict{{border-radius:12px;padding:20px 22px;margin:0 0 8px}}
.verdict strong{{display:block;font-size:20px;margin-bottom:6px}}
.verdict p{{margin:0}}
.facts{{display:flex;flex-wrap:wrap;gap:8px 28px;color:var(--muted);font-size:14px;margin:14px 0 0}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:var(--muted);font-weight:500;padding:8px 8px 8px 0;border-bottom:1px solid var(--line)}}
td{{padding:10px 8px 10px 0;border-bottom:1px solid var(--line);vertical-align:top}}
td.title{{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.date,.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;white-space:nowrap}}
.pill{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}}
.muted{{color:var(--muted)}}
.wrap{{overflow-x:auto}}
ul{{padding-left:20px}} li{{margin:6px 0}}
details{{margin-top:12px}} summary{{cursor:pointer;color:var(--muted)}}
</style></head><body><main>
<h1>Location-leak audit — {html.escape(name)}</h1>
<p class="sub">Aircraft-Doppler check on recent VODs · {date.today().isoformat()}{by} · private, for you only</p>

<div class="verdict" style="background:{verdict_bg};color:{verdict_fg}">
  <strong>{verdict}</strong>
  <p style="color:var(--ink)">{explain}</p>
</div>
<div class="facts">
  <span><b>{len(reports)}</b> streams, <b>{total_h:.0f} h</b> total</span>
  <span>listened to <b>{listened_h:.1f} h</b> (about 1 minute in every {round(1/frac) if frac else '∞'})</span>
  <span>peak stretch score <b>{peak:.2f}</b> / 1.00 (raw; <b>{peak_plausible:.2f}</b> after discarding impossible fits)</span>
  <span>{hit_windows} stretches with any candidate, <b>{confirmed}</b> plausible</span>
  <span><b>{n_clean}</b> clean flyovers in the sample — about <b>{rate_h:.1f}</b> per hour, so roughly <b>{projected:.0f}</b> across the archive</span>
  {f'<span>full coverage of {full["files"]} stream(s), {full["hours"]:.1f} h: <b>{full["clean"]}</b> clean flyovers, <b>{full["plausible"]}</b> confirmed</span>' if full else ''}
</div>

<h2>Per stream</h2>
<div class="wrap"><table>
<tr><th>date</th><th>stream</th><th class="num">length</th><th>status</th><th class="num">score</th><th class="num">flagged</th><th>listen at</th></tr>
{''.join(rows)}
</table></div>
<p class="muted" style="font-size:13px">Score is for one {win_s:.0f}-second stretch: 0 is silence, 1 is a textbook flyover. Below 0.25 is noise. Around 0.5 is where matching to flight data starts to work. It does not add up across stretches; the count of clean flyovers does, and that count is what locates a person. "Flagged" is how many of the sampled stretches showed anything; "listen at" is the worst one, if you want to hear it yourself.</p>

<h2>What this means for you</h2>
<ul>
  <li>The technique needs a plane passing within a couple of kilometres <i>and</i> a mic that picks up the room, not just your voice. On this sample, your setup doesn't produce a usable signal.</li>
  <li>Twitch's compression thins out exactly the low hum this relies on, so VODs are already a harder target than your raw recordings. If you post raw mic audio anywhere (podcasts, YouTube uploads from local files), that's where to be more careful.</li>
  <li>Noise reduction and high-pass filters do <b>not</b> remove this. A noise gate on your mic does help, because it cuts the room sound when you're not talking.</li>
</ul>

<h2>Honest limits</h2>
<ul>
  <li>We sampled, not listened end to end. A quiet moment with a window open could be missed.</li>
  <li>The detector has been validated on synthetic test audio, not yet on a large set of real recordings with known flyovers. Treat this as a strong indication, not a guarantee.</li>
  <li>This checks one technique. It says nothing about reflections, visible windows, delivery boxes, or anything else in frame.</li>
</ul>

<details><summary>Appendix: every candidate the detector raised</summary>
<div class="wrap"><table>
<tr><th>date</th><th>at</th><th class="num">speed</th><th class="num">miss distance</th><th class="num">harmonics</th><th class="num">fit error</th><th>reading</th></tr>
{appendix_html}
</table></div>
<p class="muted" style="font-size:13px">A real airliner flies 70–250 m/s; anything above ~180 m/s within a few hundred metres is physically silly and means the fitter matched something else. Two or more harmonics, or a fit error under 0.2%, is what a genuine flyover looks like.</p>
</details>

<p class="muted" style="font-size:13px;margin-top:40px">Generated with <a href="https://nocontrail.audio" style="color:inherit">No Contrail</a>, open source, runs locally. Nothing from your audio was uploaded anywhere.</p>
</main></body></html>
"""


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="contrail report")
    ap.add_argument("folder")
    ap.add_argument("--name", required=True, help="who the report is for")
    ap.add_argument("--by", default="", help="auditor name")
    ap.add_argument("-o", "--output", default="audit.html")
    ap.add_argument("--accumulate", default=None,
                    help="folder of full-coverage JSON (contrail audit --json / accumulate.py) to fold in")
    a = ap.parse_args(argv)
    out = build(Path(a.folder), a.name, a.by, Path(a.accumulate) if a.accumulate else None)
    Path(a.output).write_text(out)
    print(f"wrote {a.output}")
    return 0
