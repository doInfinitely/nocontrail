// Emails an audit-service request to the team inbox via Mailgun.
// Env: MAILGUN_API_KEY, and a sending domain from either MAILGUN_SENDING_DOMAIN
// or the host part of MAILGUN_LIST_ADDRESS. Optional: AUDIT_INBOX
// (default hello@nocontrail.audio), MAILGUN_API_BASE (https://api.eu.mailgun.net
// for an EU account). Requester details are forwarded to the inbox, never logged.

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function parseBody(body) {
  if (body && typeof body === "object") return body;
  try {
    return JSON.parse(body || "{}") || {};
  } catch {
    return {};
  }
}

function clean(v, max) {
  return (typeof v === "string" ? v : "").trim().slice(0, max);
}

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }

  const body = parseBody(req.body);

  // Honeypot: real visitors never fill this hidden field. Report success so
  // bots get no signal, and send nothing.
  if (body.website) return res.status(200).json({ ok: true });

  const email = clean(body.email, 254);
  if (!EMAIL.test(email)) {
    return res.status(400).json({ error: "invalid_email" });
  }
  const name = clean(body.name, 120);
  const channel = clean(body.channel, 300);
  const notes = clean(body.notes, 4000);

  const { MAILGUN_API_KEY, MAILGUN_SENDING_DOMAIN, MAILGUN_LIST_ADDRESS,
          MAILGUN_API_BASE, AUDIT_INBOX } = process.env;
  const domain = MAILGUN_SENDING_DOMAIN ||
    (MAILGUN_LIST_ADDRESS && MAILGUN_LIST_ADDRESS.includes("@")
      ? MAILGUN_LIST_ADDRESS.split("@")[1] : "");
  if (!MAILGUN_API_KEY || !domain) {
    return res.status(503).json({ error: "requests_closed" });
  }
  const inbox = AUDIT_INBOX || "hello@nocontrail.audio";

  const base = MAILGUN_API_BASE || "https://api.mailgun.net";
  const url = `${base}/v3/${encodeURIComponent(domain)}/messages`;
  const auth = Buffer.from(`api:${MAILGUN_API_KEY}`).toString("base64");

  const text =
    `New audit request\n\n` +
    `Name/handle: ${name || "(none)"}\n` +
    `Email:       ${email}\n` +
    `Channel:     ${channel || "(none)"}\n\n` +
    `Notes:\n${notes || "(none)"}\n`;

  try {
    const upstream = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Basic ${auth}` },
      body: new URLSearchParams({
        from: `No Contrail site <postmaster@${domain}>`,
        to: inbox,
        "h:Reply-To": email,
        subject: `Audit request${name ? ` — ${name}` : ""}`,
        text,
      }),
    });
    if (upstream.ok) return res.status(200).json({ ok: true });
    console.error("mailgun rejected audit request: status", upstream.status);
  } catch (err) {
    console.error("audit request send failed:", err && err.name);
  }
  return res.status(502).json({ error: "send_failed" });
};
