#!/usr/bin/env python3
"""Produce the sendable list: active prospects minus everyone suppressed.

    python3 outreach/filter_sendable.py                 # write sendable.csv + report
    python3 outreach/filter_sendable.py -o out.csv      # choose output path
    python3 outreach/filter_sendable.py --dry-run        # just the counts

Run this before EVERY send. It is the one gate between your list and the
outbox. A row goes out only if its status is 'active' and its address is not
on the global suppression list. Duplicate addresses within prospects.csv are
collapsed (first kept). Nothing is ever mutated in place.
"""
from __future__ import annotations

import argparse
import csv
import sys

from _emails import PROSPECTS, load_suppressed_keys, normalize, read_rows, suppression_key


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=str(PROSPECTS.parent / "sendable.csv"),
                    help="output CSV (default outreach/sendable.csv)")
    ap.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    a = ap.parse_args()

    prospects = read_rows(PROSPECTS)
    suppressed = load_suppressed_keys()

    sendable: list[dict] = []
    seen: set[str] = set()
    n_suppressed = n_paused = n_dup = n_noemail = 0

    for r in prospects:
        email = normalize(r.get("email", ""))
        if not email or "@" not in email:
            n_noemail += 1
            continue
        if (r.get("status", "active").strip().lower() or "active") != "active":
            n_paused += 1
            continue
        key = suppression_key(email)
        if key in suppressed:
            n_suppressed += 1
            continue
        if key in seen:
            n_dup += 1
            continue
        seen.add(key)
        row = dict(r)
        row["email"] = email
        sendable.append(row)

    print(f"prospects read:        {len(prospects)}")
    print(f"  suppressed (opt-out/bounce/complaint): {n_suppressed}")
    print(f"  paused:              {n_paused}")
    print(f"  duplicate address:   {n_dup}")
    print(f"  missing/bad email:   {n_noemail}")
    print(f"SENDABLE:              {len(sendable)}")

    if a.dry_run:
        return 0

    fields = list(prospects[0].keys()) if prospects else ["email", "handle", "source",
                                                           "date_added", "status", "notes"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in sendable:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
