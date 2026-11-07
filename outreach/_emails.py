"""Shared helpers for the outreach list tooling. Stdlib only.

Email matching for suppression errs toward suppressing: we lowercase, strip
whitespace, and (for suppression-key purposes) drop any +tag in the local part
and dots in gmail addresses, so a published "Name+inquiries@gmail.com" and a
later "name@gmail.com" collapse to the same key. Addresses are compared by this
key, never mangled in the files themselves.
"""
from __future__ import annotations

import csv
from pathlib import Path

OUTREACH = Path(__file__).resolve().parent
PROSPECTS = OUTREACH / "prospects.csv"
SUPPRESSION = OUTREACH / "suppression.csv"

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize(email: str) -> str:
    """Canonical lowercase address, as typed (what we store / display)."""
    return email.strip().lower()


def suppression_key(email: str) -> str:
    """Generous match key for the do-not-email check (drops +tags; gmail dots)."""
    e = normalize(email)
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    local = local.split("+", 1)[0]
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{domain}"


def read_rows(path: Path) -> list[dict]:
    """CSV rows as dicts, skipping blank lines and # comment lines."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    return list(csv.DictReader(lines))


def load_suppressed_keys() -> set[str]:
    return {suppression_key(r["email"]) for r in read_rows(SUPPRESSION) if r.get("email")}
