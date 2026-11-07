# outreach

Cold-email list tooling for the No Contrail audit service, built around one
rule: **a global, permanent suppression list that you subtract before every
send.** Stdlib Python only — the lists are plain CSVs you can edit by hand.

## Files

| File | What it is |
|---|---|
| `prospects.csv` | the send list — who you *might* email |
| `suppression.csv` | the permanent do-not-email list — append-only, never deleted |
| `filter_sendable.py` | writes `sendable.csv` = active prospects minus everyone suppressed |
| `add_suppression.py` | records an opt-out / bounce / complaint |
| `template.md` | the compliant email (subject, body, required footer, headers) |
| `_emails.py` | shared address-normalization helpers |

`sendable.csv` is generated output — don't edit it, and it's fine to gitignore.

## The loop

```sh
# 1. add prospects to prospects.csv (by hand, or append from your scraper)
#    — always fill `source` with the URL where the address was published.

# 2. before every send, regenerate the sendable list:
python3 outreach/filter_sendable.py        # -> outreach/sendable.csv

# 3. send to sendable.csv only, using template.md.

# 4. the day any opt-out / bounce / complaint arrives, suppress it:
python3 outreach/add_suppression.py person@example.com --reason unsubscribed
#    bounces/complaints from your sender's export, piped in:
cat bounces.txt | python3 outreach/add_suppression.py --stdin --reason bounced
```

## Why this is the compliant shape

- **Suppression is global and permanent.** Once suppressed, an address is
  filtered from *every* future send regardless of which source re-adds it.
  Matching is generous (case, +tags, gmail dots collapse) so a re-scraped
  variant can't sneak a suppressed person back in.
- **`source` per contact** is your proof each address was published for
  inquiries — the strongest footing for cold outreach to recipients whose
  jurisdiction you can't know.
- **Opt-outs must be honored promptly** (within 10 business days under US
  CAN-SPAM; treat it as same-day). `add_suppression.py` is the whole obligation.
- The email itself must carry a **physical postal address** and a **working
  opt-out** — see `template.md`.

Note on geography: US recipients are covered by CAN-SPAM (what this is built
for). The EU/UK (GDPR + ePrivacy) and Canada (CASL) require prior consent for
unsolicited email and are a different legal regime — if you knowingly add
recipients there, handle them separately. This tooling is operational hygiene,
not legal advice.
