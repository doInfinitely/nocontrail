#!/usr/bin/env python3
"""Record an opt-out / bounce / complaint on the permanent suppression list.

    python3 outreach/add_suppression.py person@example.com --reason unsubscribed
    python3 outreach/add_suppression.py a@x.com b@y.com --reason bounced
    python3 outreach/add_suppression.py --stdin --reason complained   # one email per line

Append-only: a row is added (dated today) unless an equivalent address is
already suppressed, in which case it is reported and skipped. This is the only
thing you must do to honor an opt-out, and doing it promptly is the legal
obligation -- run it the day a request comes in.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date

from _emails import SUPPRESSION, load_suppressed_keys, normalize, suppression_key

VALID = {"unsubscribed", "complained", "bounced", "manual"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("emails", nargs="*", help="one or more addresses")
    ap.add_argument("--reason", default="unsubscribed", choices=sorted(VALID))
    ap.add_argument("--note", default="")
    ap.add_argument("--stdin", action="store_true", help="also read addresses from stdin, one per line")
    a = ap.parse_args()

    inputs = list(a.emails)
    if a.stdin:
        inputs += [ln.strip() for ln in sys.stdin if ln.strip()]
    if not inputs:
        ap.error("no addresses given (pass them as args or with --stdin)")

    already = load_suppressed_keys()
    today = date.today().isoformat()
    new_rows, skipped = [], []
    batch_keys: set[str] = set()

    for raw in inputs:
        email = normalize(raw)
        if "@" not in email:
            skipped.append((raw, "not an email"))
            continue
        key = suppression_key(email)
        if key in already or key in batch_keys:
            skipped.append((email, "already suppressed"))
            continue
        batch_keys.add(key)
        new_rows.append({"email": email, "reason": a.reason, "date": today, "note": a.note})

    if new_rows:
        write_header = not SUPPRESSION.exists() or SUPPRESSION.stat().st_size == 0
        with SUPPRESSION.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["email", "reason", "date", "note"])
            if write_header:
                w.writeheader()
            for row in new_rows:
                w.writerow(row)

    for email, why in skipped:
        print(f"skip  {email}  ({why})")
    for row in new_rows:
        print(f"added {row['email']}  [{row['reason']}]")
    print(f"\n{len(new_rows)} added, {len(skipped)} skipped -> {SUPPRESSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
