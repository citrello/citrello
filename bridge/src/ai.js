// Claude for "What your sources say" (a pro feature): the app sends a cited sentence with short passages of the
// papers' full text that it picked on the computer; Claude says whether they support it, with the exact words. It
// can also look on the web for published papers that support a sentence. Nothing is stored here except a monthly
// count per account. The API key is a secret of this Worker (ANTHROPIC_API_KEY); the model is AI_MODEL.
import Anthropic from "@anthropic-ai/sdk";
import * as credits from "./credits.js";

const VERDICTS = ["supports", "partly", "contradicts", "not_found"];
const clip = (s, n) => String(s ?? "").slice(0, n);

export const isApiError = (e) => e instanceof Anthropic.APIError;
export const isCreditError = (e) => e instanceof credits.CreditError;

export class AiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function client(env) {
  if (!env.ANTHROPIC_API_KEY) throw new AiError(503, "ai_not_set_up", "Claude checks aren't switched on yet.");
  return new Anthropic({ apiKey: env.ANTHROPIC_API_KEY, ...(env.ANTHROPIC_BASE_URL ? { baseURL: env.ANTHROPIC_BASE_URL } : {}),
                         maxRetries: 2, timeout: 120000 });
}
const model = (env) => env.AI_MODEL || "claude-sonnet-5-5";          // verdicts and library checks
const webModel = (env) => env.AI_WEB_MODEL || "claude-haiku-5-5";     // the web search step of web checks (cheap)

// What each check costs: credits (src/credits.js). quota() is the account's balance, for the app and the Word add-in.
export const quota = (env, userId) => credits.balance(env, userId);

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

// what a request used (for measuring the cost): tokens in and out, and web searches
const usageOf = (msg, searches) => ({ model: msg.model, input_tokens: msg.usage.input_tokens, output_tokens: msg.usage.output_tokens,
  ...(searches !== undefined ? { web_searches: searches } : {}) });

function textOf(msg) {
  return msg.content.filter((b) => b.type === "text").map((b) => b.text).join("");
}

// {items: [{id, claim, papers: [{ref, title, passages: [text]}]}]} -> {results: [{id, paper, verdict, ...}], quota}
// A claim check costs COST.claim credits per paper; a library find (several candidate papers for one sentence)
// COST.library per sentence.
export async function judge(env, userId, body) {
  const items = (Array.isArray(body.items) ? body.items : []).slice(0, 12).map((it) => ({
    id: clip(it.id, 40), claim: clip(it.claim, 1200),
    papers: (Array.isArray(it.papers) ? it.papers : []).slice(0, 8).map((p) => ({
      ref: clip(p.ref, 80), title: clip(p.title, 300),
      passages: (Array.isArray(p.passages) ? p.passages : []).slice(0, 8).map((x) => clip(x, 1500)) })),
  })).filter((it) => it.claim && it.papers.length);
  if (!items.length) throw new AiError(400, "invalid_request", "Nothing to check.");
  const library = body.kind === "library";
  const cost = library ? items.length * credits.COST.library : items.reduce((a, it) => a + it.papers.length, 0) * credits.COST.claim;
  const done = await credits.charge(env, userId, cost, library ? "library find" : "claim check");
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
  return { results, usage: usageOf(msg), quota: await quota(env, userId) };
}

const WEB_SYSTEM = `You help researchers find published sources for a sentence in their manuscript. Use web_search
(at most 3 searches) to find peer-reviewed papers (journal articles, conference papers, academic books) whose findings
support the sentence: search scholarly terms, and include words such as "doi" or "journal" to reach publishers' and
indexes' pages. Judge each candidate from the title and abstract text in the search results; don't open pages. Prefer
recent, well-cited work with a DOI. Never invent papers, authors or DOIs: only list papers that appear in your search
results, with the DOI exactly as shown there (empty if none is shown). List at most 5; 3 good ones are enough.
End your answer with only a JSON object, no other text after it:
{"papers": [{"title": "", "authors": "Family name of the first author, or 'Family & Family', or 'Family et al.'",
"year": "", "doi": "10.xxxx/... or empty", "url": "", "verdict": "supports" or "partly",
"quote": "the exact words from the search result's abstract text that show it", "reason": "one short plain sentence"}]}`;

// {claim, context, exclude: [doi]} -> {papers: [...], quota}
export async function findOnWeb(env, userId, body) {
  const claim = clip(body.claim, 1200);
  if (!claim) throw new AiError(400, "invalid_request", "Nothing to look for.");
  const exclude = (Array.isArray(body.exclude) ? body.exclude : []).slice(0, 50).map((d) => clip(d, 200).toLowerCase());
  const done = await credits.charge(env, userId, credits.COST.web, "web find");
  const params = {
    // the search step is cheap on purpose (Haiku, search results only, no pages opened: ~3-5 cents instead of ~20):
    // the papers it picks are checked against their full text afterwards, by AI_MODEL
    model: webModel(env), max_tokens: 16000, ...fallback(webModel(env)), output_config: { effort: "low" }, system: WEB_SYSTEM,
    tools: [/haiku/.test(webModel(env))  // Haiku has the basic version of the web search tool
      ? { type: "web_search_20250305", name: "web_search", max_uses: 3 }
      : { type: "web_search_20260209", name: "web_search", max_uses: 3 }],
    messages: [{ role: "user", content: `The sentence: <claim>${claim}</claim>` +
      (body.context ? `\nThe paragraph around it: <context>${clip(body.context, 2000)}</context>` : "") +
      (exclude.length ? `\nAlready cited (don't list): ${exclude.join(", ")}` : "") }],
  };
  let msg = await client(env).beta.messages.create(params);
  let searches = (msg.usage.server_tool_use && msg.usage.server_tool_use.web_search_requests) || 0;
  let tin = msg.usage.input_tokens, tout = msg.usage.output_tokens;
  for (let i = 0; msg.stop_reason === "pause_turn" && i < 3; i++) {  // a long search: let the server continue it
    params.messages = [...params.messages, { role: "assistant", content: msg.content }];
    msg = await client(env).beta.messages.create(params);
    searches += (msg.usage.server_tool_use && msg.usage.server_tool_use.web_search_requests) || 0;
    tin += msg.usage.input_tokens; tout += msg.usage.output_tokens;
  }
  if (msg.stop_reason === "refusal") throw new AiError(422, "ai_refused", "Claude couldn't search for this sentence.");
  const text = textOf(msg), last = text.lastIndexOf('{"papers"'), a = last >= 0 ? last : text.indexOf("{"), b = text.lastIndexOf("}");
  let out;
  try { out = JSON.parse(text.slice(a, b + 1)); } catch { throw new AiError(502, "ai_bad_answer", "Claude's answer couldn't be read. Please try again."); }
  await done();
  const papers = (out.papers || []).slice(0, 5).map((p) => ({
    title: clip(p.title, 300), authors: clip(p.authors, 120), year: clip(p.year, 8),
    doi: clip(String(p.doi || "").replace(/^https?:\/\/(dx\.)?doi\.org\//i, ""), 200), url: clip(p.url, 500),
    verdict: p.verdict === "partly" ? "partly" : "supports", quote: clip(p.quote, 600), reason: clip(p.reason, 400) }))
    .filter((p) => p.title && !exclude.includes(p.doi.toLowerCase()));
  return { papers, usage: { ...usageOf(msg, searches), input_tokens: tin, output_tokens: tout }, quota: await quota(env, userId),
           ...(env.DEV_RETURN_CODES && !papers.length ? { raw: text.slice(-3000), stop: msg.stop_reason } : {}) };  // test copies only: why nothing came back
}
