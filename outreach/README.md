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

## Setup decisions

- **Postal address (`{{postal_address}}`):** use the business's registered
  public address — `Remy Ochei Inc`, already public as the company's
  registered address. It satisfies the CAN-SPAM physical-address requirement,
  and no PO box / virtual mailbox is needed. (A virtual mailbox stays an
  option if we later want the footer address off the home address.)
- **Sending, NOT via Mailgun:** transactional ESPs (Mailgun, SendGrid,
  Postmark) require recipient consent and ban unsolicited/cold email in their
  acceptable-use policies — a cold batch to scraped addresses risks account
  suspension. Keep Mailgun/primary domain for real mail (audit reports,
  replies, invoices). Send the cold campaign from a dedicated cold-email
  platform on a separate sending subdomain.

## Sending infrastructure (current)

- **Platform:** Instantly, **10 prewarmed mailboxes** across two cold-sending
  domains — **axiomatictrue.com** and **joyfulquantum.com** (~5 each). Neither
  is `nocontrail.audio`, which stays clean for real mail.
- **Warmup:** handled (accounts came prewarmed), but leave Instantly's warmup
  toggled ON continuously so the accounts keep earning engagement while they
  also send cold.
- **Volume:** start ~20–30/day per inbox, ramp toward 40–50; ~300/day ceiling
  across the 10. Add all 10 to the campaign so Instantly rotates automatically.
- **Identity:** domains are brand-neutral, so the From display name carries the
  brand — `Remy — No Contrail <remy@axiomatictrue.com>` — and the footer names
  Remy Ochei Inc. See `template.md` "From — sending identity".
- **List hygiene first:** verify `sendable.csv` (Instantly's verifier or
  NeverBounce/ZeroBounce) before the first send; pipe bounces into
  `add_suppression.py --stdin --reason bounced`. Bounces burn paid accounts fast.
- **Proof point in the email:** the self-test — pseudo-streamed with a mic, no
  soundproofing, pinned own house to ~300 m — plus a YouTube explainer
  (`{{video_url}}`, in production). Don't ship a dead video link; see
  `template.md` for how to handle it before the video is live.

## Generic warmup notes

What to sign up for (both include built-in mailbox warmup, inbox rotation, and
per-inbox send throttling — the things Mailgun won't do for cold outreach):

- **Instantly** (~$37–47/mo) — easiest to start, flat pricing with unlimited
  mailboxes, simple inbox rotation. Good default for a solo operator.
- **Smartlead** (~$39/mo) — best-in-class rotation and deliverability
  controls, more of a power-user tool. Pick this if we scale to many mailboxes.

Either one works; verify current pricing on the vendor site before signing up.
Then, inside whichever tool:

1. **Sending identity:** a separate subdomain (e.g. `get.nocontrail.audio`),
   NOT the primary domain. Either connect a couple of Google Workspace / M365
   mailboxes on it, or buy the platform's managed domains + mailboxes.
2. **Authenticate:** SPF, DKIM, DMARC on the sending subdomain, all passing.
3. **Warm up for 2–4 weeks** before any real send — the tool ramps volume
   automatically (start ~10–20/day). "Warmup" here = mailbox/engagement
   warmup, not Mailgun's dedicated-IP warmup.
4. Only then run `filter_sendable.py` and send to `sendable.csv`, keeping the
   spam-complaint rate under 0.3%.
