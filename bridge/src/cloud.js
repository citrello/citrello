// The cloud copy of a web-app library: when someone is signed in, citrello.app/app saves its library (references,
// projects and the papers' text, gzip-compressed) here, and brings it back in another browser. Stored in an R2
// bucket in the EU (binding CLOUD, jurisdiction "eu"), one object per account; nothing else is kept about it.
// Without the CLOUD binding the endpoints say the cloud copy isn't switched on.

export class CloudError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

const keyOf = (userId) => `library/${userId}.json.gz`;
const MB = 1024 * 1024;

function bucket(env) {
  if (!env.CLOUD) throw new CloudError(503, "cloud_not_set_up", "The cloud copy isn't switched on yet.");
  return env.CLOUD;
}

async function limitFor(env, userId) {
  const u = await env.DB.prepare("SELECT pro FROM users WHERE id = ?").bind(userId).first();
  return Number(u && u.pro ? env.CLOUD_PRO_MB || 1000 : env.CLOUD_FREE_MB || 100) * MB;
}

// what's saved: when, from which browser, how many references, how big
export async function info(env, userId) {
  const limit = await limitFor(env, userId);
  if (!env.CLOUD) return { ready: false, exists: false, limit_bytes: limit };
  const head = await env.CLOUD.head(keyOf(userId));
  if (!head) return { ready: true, exists: false, limit_bytes: limit };
  const m = head.customMetadata || {};
  return { ready: true, exists: true, saved_at: Number(m.saved_at) || 0, items: Number(m.items) || 0,
           texts: Number(m.texts) || 0, device: m.device || "", bytes: head.size, limit_bytes: limit };
}

export async function save(env, userId, request) {
  const b = bucket(env), limit = await limitFor(env, userId);
  const body = await request.arrayBuffer();
  const head = new Uint8Array(body.slice(0, 2));
  if (head[0] !== 0x1f || head[1] !== 0x8b) throw new CloudError(400, "invalid_request", "That isn't a Citrello library.");
  if (body.byteLength > limit) {
    throw new CloudError(413, "cloud_full", `Your library is ${Math.ceil(body.byteLength / MB)} MB, more than the cloud copy's ${limit / MB} MB.`);
  }
  const h = (name, n) => String(request.headers.get(name) || "").slice(0, n);
  const meta = { saved_at: String(Math.floor(Date.now() / 1000)), items: h("X-Items", 12), texts: h("X-Texts", 12),
                 device: h("X-Device", 80) };
  await b.put(keyOf(userId), body, { httpMetadata: { contentType: "application/gzip" }, customMetadata: meta });
  return info(env, userId);
}

export async function load(env, userId) {
  const obj = await bucket(env).get(keyOf(userId));
  if (!obj) throw new CloudError(404, "not_found", "There's no cloud copy of your library yet.");
  return obj;
}

export async function remove(env, userId) {
  if (env.CLOUD) await env.CLOUD.delete(keyOf(userId));
}
