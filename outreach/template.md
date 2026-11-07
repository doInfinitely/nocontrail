# Cold outreach — email template

Fill the `{{...}}` placeholders. `{{handle}}` comes from the sendable.csv row;
the rest you set once. Keep the body honest and short — the subject must match
what's inside, and the footer must stay intact on every send.

---

## Subject

```
{{handle}}, your stream may be leaking your location (and how to check)
```

Honest, specific, no clickbait. It says exactly what the email is about.

## Body (plain text)

```
Hi {{handle}},

I run No Contrail. We check recordings for a location leak most streamers
don't know about: aircraft passing overhead leave a Doppler "whoosh" in your
audio, and with a few of those a listener can triangulate roughly where you
record from — even if you never show your location on camera.

I found your inquiry address on your channel and wanted to flag it, since
public VODs are exactly where this shows up.

We offer a paid audit: send us archive audio, we report back within 48 hours
on whether a location is recoverable and how to remove it. Pricing is $2 per
hour of footage ($50 minimum). No account, nothing runs in the cloud — the
tool is a local app you can run yourself if you'd rather.

If that's useful, just reply and I'll send details. If it's not, no worries.

— Remy, No Contrail
https://nocontrail.audio

---
No Contrail · {{postal_address}}
You received this because your contact address is listed publicly on your
{{channel_platform}} channel for inquiries ({{source}}).
Don't want to hear from us again? Reply with "unsubscribe" and you're off the
list for good — or use the one-click link in this email's headers.
```

## Why each footer line is there (don't cut these)

- **"No Contrail · {{postal_address}}"** — a valid physical postal mailing
  address is legally required. A registered mailbox / PO box is fine. Never ship
  without it.
- **"You received this because…{{source}}"** — identifies the message as
  outreach and states, per-recipient, why they got it. {{source}} is the exact
  profile URL from the row — your proof the address was published for inquiries.
- **The opt-out line** — must be present, must work for at least 30 days, and
  must cost the recipient nothing beyond saying so. "Reply unsubscribe" is the
  simplest honest mechanism at low volume; the moment one arrives, run
  `add_suppression.py` that day.

## Headers to set (deliverability + one-click opt-out)

Modern bulk-sender rules (Gmail/Yahoo) effectively require these. Set them in
your sending tool / script:

```
List-Unsubscribe: <mailto:unsubscribe@nocontrail.audio?subject=unsubscribe>, <https://nocontrail.audio/u/{{token}}>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
```

- The `mailto:` works even without the hosted endpoint — mail lands in a box you
  watch, and you suppress from it daily.
- The `https://` one-click link is better once you have an endpoint that records
  the opt-out (ideally writing straight into `suppression.csv`). `{{token}}` is a
  per-recipient id so the click needs no typing.

## Before you send, every time

1. Authenticate the sending domain: **SPF, DKIM, DMARC** all passing.
2. Send from a **subdomain** (e.g. `get.nocontrail.audio`), not your primary mail
   domain, and warm it up slowly so a cold batch can't wreck your real
   deliverability.
3. Run `python3 outreach/filter_sendable.py` and send to **sendable.csv only**.
4. Keep your spam-complaint rate under **0.3%** — if a segment complains, pause
   it, don't push harder.
