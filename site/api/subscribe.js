// Adds a launch-notification signup to a Mailgun mailing list.
// Env: MAILGUN_API_KEY, MAILGUN_LIST_ADDRESS, optional MAILGUN_API_BASE
// (use https://api.eu.mailgun.net for an EU-region Mailgun account).
// Addresses are never logged.

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function parseBody(body) {
  if (body && typeof body === "object") return body;
  try {
    return JSON.parse(body || "{}") || {};
  } catch {
    return {};
  }
}

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "method_not_allowed" });
  }

  const { email, website } = parseBody(req.body);

  // Honeypot: real visitors never fill this hidden field. Report success so
  // bots get no signal, and store nothing.
  if (website) return res.status(200).json({ ok: true });

  const address = typeof email === "string" ? email.trim() : "";
  if (address.length > 254 || !EMAIL.test(address)) {
    return res.status(400).json({ error: "invalid_email" });
  }

  const { MAILGUN_API_KEY, MAILGUN_LIST_ADDRESS, MAILGUN_API_BASE } = process.env;
  if (!MAILGUN_API_KEY || !MAILGUN_LIST_ADDRESS) {
    return res.status(503).json({ error: "signups_closed" });
  }

  const base = MAILGUN_API_BASE || "https://api.mailgun.net";
  const url = `${base}/v3/lists/${encodeURIComponent(MAILGUN_LIST_ADDRESS)}/members`;
  const auth = Buffer.from(`api:${MAILGUN_API_KEY}`).toString("base64");

  try {
    // upsert stays off so a repeat signup can't resubscribe someone who
    // has unsubscribed.
    const upstream = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Basic ${auth}` },
      body: new URLSearchParams({ address, subscribed: "yes", upsert: "no" }),
    });
    if (upstream.ok) return res.status(200).json({ ok: true });

    const detail = await upstream.text();
    if (upstream.status === 400 && /already exists/i.test(detail)) {
      return res.status(200).json({ ok: true });
    }
    console.error("mailgun rejected signup: status", upstream.status);
  } catch (err) {
    console.error("mailgun request failed:", err.name);
  }
  return res.status(502).json({ error: "upstream_failed" });
};
