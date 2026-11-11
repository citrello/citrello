// Claude for "What your sources say" (a pro feature): the app sends a cited sentence with short passages of the
// papers' full text that it picked on the computer; Claude says whether they support it, with the exact words. It
// can also look on the web for published papers that support a sentence. Nothing is stored here except a monthly
// count per account. The API key is a secret of this Worker (ANTHROPIC_API_KEY); the model is AI_MODEL.
import Anthropic from "@anthropic-ai/sdk";

const VERDICTS = ["supports", "partly", "contradicts", "not_found"];
const month = () => new Date().toISOString().slice(0, 7);
const clip = (s, n) => String(s ?? "").slice(0, n);

export const isApiError = (e) => e instanceof Anthropic.APIError;

export class AiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function client(env) {
  if (!env.ANTHROPIC_API_KEY) throw new AiError(503, "ai_not_set_up", "Claude checks aren't switched on yet.");
  return new Anthropic({ apiKey: env.ANTHROPIC_API_KEY, ...(env.ANTHROPIC_BASE_URL ? { baseURL: env.ANTHROPIC_BASE_URL } : {}),
                         maxRetries: 2, timeout: 120000 });
}
const model = (env) => env.AI_MODEL || "claude-opus-5-5";            // verdicts and library checks
const webModel = (env) => env.AI_WEB_MODEL || model(env);              // web checks (needs web search: not Haiku)

// Monthly allowances: checks (a verdict, or a library check, counts 1) and web checks, free and pro.
export async function quota(env, userId) {
  const u = await env.DB.prepare("SELECT pro FROM users WHERE id = ?").bind(userId).first();
  const row = await env.DB.prepare("SELECT units, web FROM ai_usage WHERE user_id = ? AND month = ?").bind(userId, month()).first();
  const pro = !!(u && u.pro);
  return { pro, month: month(), ready: !!env.ANTHROPIC_API_KEY,
           used: row ? row.units : 0, limit: Number(pro ? env.AI_PRO_MONTHLY || 3000 : env.AI_FREE_MONTHLY || 30),
           web_used: row ? row.web : 0, web_limit: Number(pro ? env.AI_PRO_WEB || 100 : env.AI_FREE_WEB || 3) };
}

async function spend(env, userId, units, kind = "units") {
  const q = await quota(env, userId);
  const web = kind === "web", used = web ? q.web_used : q.used, limit = web ? q.web_limit : q.limit;
  const what = web ? "web checks" : "Claude checks";
  if (used + units > limit) {
    throw new AiError(402, "ai_limit", q.pro
      ? `You've used this month's ${limit} ${what}. They start again next month.`
      : `Free accounts get ${limit} ${what} a month, and they're used up. Citrello Pro gives you many more.`);
  }
  const col = web ? "web" : "units";
  return async () => env.DB.prepare(`INSERT INTO ai_usage (user_id, month, ${col}) VALUES (?, ?, ?)
      ON CONFLICT(user_id, month) DO UPDATE SET ${col} = ${col} + ?`).bind(userId, month(), units, units).run();
}

// Claude Opus refuses some topics on safety grounds; "default" falls back to a model that can answer.
const fallback = (m) => (/opus|sonnet|fable/.test(m) ? { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" } : {});

const JUDGE_SYSTEM = `You help researchers check their citations. For each item you get a sentence from their manuscript
(the claim) and one or more cited papers, each with numbered passages taken from the paper's full text (or abstract).
For every paper, decide from those passages only:
- "supports": the paper clearly says what the sentence claims;
- "partly": it says something close, but weaker, narrower or only part of the claim;
- "contradicts": it says the opposite, or numbers/findings that disagree with the claim;
- "not_found": the passages don't address the claim.
Give "quote": the shortest exact words copied from one passage that show your verdict (at most 40 words; empty for
not_found), "passage": that passage's number (0 if none), and "reason": one short plain-language sentence for the
researcher (e.g. "The paper says cables were 42% of the cost, the largest share."). Never invent text.`;

const JUDGE_SCHEMA = {
  type: "object", additionalProperties: false, required: ["results"],
  properties: { results: { type: "array", items: { type: "object", additionalProperties: false,
    required: ["id", "paper", "verdict", "passage", "quote", "reason"],
    properties: { id: { type: "string" }, paper: { type: "string" }, verdict: { type: "string", enum: VERDICTS },
                  passage: { type: "integer" }, quote: { type: "string" }, reason: { type: "string" } } } } },
};

function textOf(msg) {
  return msg.content.filter((b) => b.type === "text").map((b) => b.text).join("");
}

// {items: [{id, claim, papers: [{ref, title, passages: [text]}]}]} -> {results: [{id, paper, verdict, ...}], quota}
export async function judge(env, userId, body) {
  const items = (Array.isArray(body.items) ? body.items : []).slice(0, 12).map((it) => ({
    id: clip(it.id, 40), claim: clip(it.claim, 1200),
    papers: (Array.isArray(it.papers) ? it.papers : []).slice(0, 8).map((p) => ({
      ref: clip(p.ref, 80), title: clip(p.title, 300),
      passages: (Array.isArray(p.passages) ? p.passages : []).slice(0, 8).map((x) => clip(x, 1500)) })),
  })).filter((it) => it.claim && it.papers.length);
  if (!items.length) throw new AiError(400, "invalid_request", "Nothing to check.");
  // a verdict = one sentence against one paper; a library check (several candidate papers for one sentence) counts 1
  const units = body.kind === "library" ? items.length : items.reduce((a, it) => a + it.papers.length, 0);
  const done = await spend(env, userId, units);
  const prompt = items.map((it) => `<item id="${it.id}">\n<claim>${it.claim}</claim>\n` + it.papers.map((p) =>
    `<paper ref="${p.ref}" title="${p.title.replace(/"/g, "'")}">\n` + p.passages.map((x, i) => `<passage n="${i + 1}">${x}</passage>`).join("\n") + "\n</paper>").join("\n")
    + "\n</item>").join("\n\n");
  const msg = await client(env).beta.messages.create({
    model: model(env), max_tokens: 16000, ...fallback(model(env)),
    output_config: { effort: "low", format: { type: "json_schema", schema: JUDGE_SCHEMA } },
    system: JUDGE_SYSTEM,
    messages: [{ role: "user", content: `${prompt}\n\nGive one result per paper of every item (paper = its ref).` }],
  });
  if (msg.stop_reason === "refusal") throw new AiError(422, "ai_refused", "Claude couldn't check these sentences.");
  let out;
  try { out = JSON.parse(textOf(msg)); } catch { throw new AiError(502, "ai_bad_answer", "Claude's answer couldn't be read. Please try again."); }
  await done();
  const results = (out.results || []).filter((r) => VERDICTS.includes(r.verdict)).map((r) => ({
    id: clip(r.id, 40), paper: clip(r.paper, 80), verdict: r.verdict, passage: Number(r.passage) || 0,
    quote: clip(r.quote, 400), reason: clip(r.reason, 400) }));
  return { results, quota: await quota(env, userId) };
}

const WEB_SYSTEM = `You help researchers find published sources for a sentence in their manuscript. Search the web
for peer-reviewed papers (journal articles, conference papers, books from academic publishers) whose findings support
the sentence. Prefer recent, well-cited work with a DOI. Read each paper's abstract (or page) before choosing it, and
never invent papers, authors or DOIs: only list papers you found. List at most 5.
End your answer with only a JSON object, no other text after it:
{"papers": [{"title": "", "authors": "Family name of the first author, or 'Family & Family', or 'Family et al.'",
"year": "", "doi": "10.xxxx/... or empty", "url": "", "verdict": "supports" or "partly",
"quote": "the exact sentence from the abstract that shows it", "reason": "one short plain sentence"}]}`;

// {claim, context, exclude: [doi]} -> {papers: [...], quota}
export async function findOnWeb(env, userId, body) {
  const claim = clip(body.claim, 1200);
  if (!claim) throw new AiError(400, "invalid_request", "Nothing to look for.");
  const exclude = (Array.isArray(body.exclude) ? body.exclude : []).slice(0, 50).map((d) => clip(d, 200).toLowerCase());
  const done = await spend(env, userId, 1, "web");
  const params = {
    model: webModel(env), max_tokens: 16000, ...fallback(webModel(env)), output_config: { effort: "medium" }, system: WEB_SYSTEM,
    tools: [{ type: /haiku/.test(webModel(env)) ? "web_search_20250305" : "web_search_20260209", name: "web_search", max_uses: 6 }],
    messages: [{ role: "user", content: `The sentence: <claim>${claim}</claim>` +
      (body.context ? `\nThe paragraph around it: <context>${clip(body.context, 2000)}</context>` : "") +
      (exclude.length ? `\nAlready cited (don't list): ${exclude.join(", ")}` : "") }],
  };
  let msg = await client(env).beta.messages.create(params);
  for (let i = 0; msg.stop_reason === "pause_turn" && i < 3; i++) {  // a long search: let the server continue it
    params.messages = [...params.messages, { role: "assistant", content: msg.content }];
    msg = await client(env).beta.messages.create(params);
  }
  if (msg.stop_reason === "refusal") throw new AiError(422, "ai_refused", "Claude couldn't search for this sentence.");
  const text = textOf(msg), a = text.indexOf("{"), b = text.lastIndexOf("}");
  let out;
  try { out = JSON.parse(text.slice(a, b + 1)); } catch { throw new AiError(502, "ai_bad_answer", "Claude's answer couldn't be read. Please try again."); }
  await done();
  const papers = (out.papers || []).slice(0, 5).map((p) => ({
    title: clip(p.title, 300), authors: clip(p.authors, 120), year: clip(p.year, 8),
    doi: clip(String(p.doi || "").replace(/^https?:\/\/(dx\.)?doi\.org\//i, ""), 200), url: clip(p.url, 500),
    verdict: p.verdict === "partly" ? "partly" : "supports", quote: clip(p.quote, 600), reason: clip(p.reason, 400) }))
    .filter((p) => p.title && !exclude.includes(p.doi.toLowerCase()));
  return { papers, quota: await quota(env, userId) };
}
