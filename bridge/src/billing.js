// Plans, paid through Stripe with Managed Payments (Stripe is the seller of record: it handles VAT and sales tax,
// receipts, fraud and disputes; customers see "Sold through Link"). The four prices are found by their lookup keys
// (created by scripts/stripe-setup.mjs). Stripe's webhook only says that something changed: the subscription is then
// read from Stripe itself with our key, so a made-up webhook can't give anyone credits.

import { PLANS, CreditError, balance } from "./credits.js";

const API_VERSION = "2025-03-31.basil";  // the oldest version that has Managed Payments
export const LOOKUP = {
  student: { month: "citrello_student_monthly", year: "citrello_student_yearly" },
  researcher: { month: "citrello_researcher_monthly", year: "citrello_researcher_yearly" },
};
const now = () => Math.floor(Date.now() / 1000);

function form(obj, prefix = "", out = new URLSearchParams()) {  // Stripe's nested form encoding: a[b][0][c]=1
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    const key = prefix ? `${prefix}[${k}]` : k;
    if (typeof v === "object") form(v, key, out);
    else out.append(key, String(v));
  }
  return out;
}

export async function stripe(env, method, path, params) {
  if (!env.STRIPE_SECRET_KEY) throw new CreditError(503, "billing_off", "Plans aren't available yet.");
  const base = env.STRIPE_API_BASE || "https://api.stripe.com";
  const qs = method === "GET" && params ? "?" + form(params) : "";
  const r = await fetch(`${base}/v1${path}${qs}`, {
    method, headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`, "Stripe-Version": API_VERSION,
                       ...(method !== "GET" ? { "Content-Type": "application/x-www-form-urlencoded" } : {}) },
    body: method !== "GET" && params ? form(params) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    console.error("Stripe", method, path, r.status, d.error && d.error.message);
    throw new CreditError(502, "billing_error", "The payment service didn't answer. Please try again in a minute.");
  }
  return d;
}

let priceCache = null;
export async function prices(env) {  // {student: {month: {id, amount, currency}, year: ...}, researcher: ...}
  if (priceCache && priceCache.at > Date.now() - 600000) return priceCache.data;
  const keys = Object.values(LOOKUP).flatMap((p) => Object.values(p));
  const list = await stripe(env, "GET", "/prices", { active: "true", limit: 10, lookup_keys: keys });
  const data = {};
  for (const [plan, byInterval] of Object.entries(LOOKUP)) {
    data[plan] = {};
    for (const [interval, key] of Object.entries(byInterval)) {
      const p = (list.data || []).find((x) => x.lookup_key === key);
      if (p) data[plan][interval] = { id: p.id, amount: p.unit_amount, currency: p.currency };
    }
  }
  priceCache = { at: Date.now(), data };
  return data;
}

// For the Plans window: the plans with their prices, and what the account has now
export async function info(env, userId) {
  const b = await balance(env, userId);
  let p = {};
  if (env.STRIPE_SECRET_KEY) { try { p = await prices(env); } catch { p = {}; } }
  return { ...b, plans: Object.entries(PLANS).map(([id, x]) => ({ id, name: x.name, credits_per_month: x.credits, prices: p[id] || {} })) };
}

// Stripe's payment page for a plan; it opens in the browser
export async function checkout(env, d, body) {
  const plan = String(body.plan || ""), interval = body.interval === "year" ? "year" : "month";
  if (!PLANS[plan]) throw new CreditError(400, "invalid_request", "Choose the Student or the Researcher plan.");
  const b = await balance(env, d.user_id);
  if (b.plan) throw new CreditError(409, "has_plan", "You already have a plan: switch or cancel it with Manage plan.");
  const price = ((await prices(env))[plan] || {})[interval];
  if (!price) throw new CreditError(503, "billing_off", "This plan isn't available yet.");
  const site = env.SITE_URL || "https://citrello.app";
  const s = await stripe(env, "POST", "/checkout/sessions", {
    mode: "subscription",
    line_items: [{ price: price.id, quantity: 1 }],
    managed_payments: { enabled: true },
    customer_email: d.email,
    client_reference_id: d.user_id,
    subscription_data: { metadata: { user_id: d.user_id } },
    metadata: { user_id: d.user_id },
    success_url: `${site}/thanks?plan=${plan}`,
    cancel_url: `${site}/thanks?cancelled=1`,
  });
  return { url: s.url };
}

// Stripe's page to switch plan, change the card or cancel
export async function portal(env, d) {
  const u = await env.DB.prepare("SELECT stripe_customer FROM users WHERE id = ?").bind(d.user_id).first();
  if (!u || !u.stripe_customer) throw new CreditError(404, "no_plan", "You don't have a plan to manage yet.");
  const s = await stripe(env, "POST", "/billing_portal/sessions",
    { customer: u.stripe_customer, return_url: `${env.SITE_URL || "https://citrello.app"}/thanks?managed=1` });
  return { url: s.url };
}

// Read a subscription from Stripe and write what it means into the account
export async function syncSubscription(env, subId, userHint) {
  const sub = await stripe(env, "GET", `/subscriptions/${encodeURIComponent(subId)}`);
  const userId = (sub.metadata && sub.metadata.user_id) || userHint;
  if (!userId) return { ignored: "no account" };
  const item = (sub.items && sub.items.data && sub.items.data[0]) || {};
  const key = item.price && item.price.lookup_key;
  let plan = null, interval = null;
  for (const [p, byInterval] of Object.entries(LOOKUP)) for (const [i, k] of Object.entries(byInterval)) if (k === key) { plan = p; interval = i; }
  if (!plan) return { ignored: "not a Citrello plan" };
  const until = item.current_period_end || sub.current_period_end || sub.ended_at || now();
  const customer = typeof sub.customer === "string" ? sub.customer : sub.customer && sub.customer.id;
  const r = await env.DB.prepare(`UPDATE users SET plan = ?, plan_interval = ?, plan_status = ?, plan_anchor = ?, plan_until = ?,
      stripe_customer = COALESCE(?, stripe_customer), stripe_subscription = ? WHERE id = ?`)
    .bind(plan, interval, sub.status, sub.start_date || item.current_period_start || now(), until, customer || null, sub.id, userId).run();
  return { user: userId, plan, status: sub.status, updated: r.meta.changes };
}

// Stripe's webhook: checkout finished, subscription created/changed/ended, invoice paid
export async function webhook(env, event) {
  const type = String(event && event.type || ""), obj = (event && event.data && event.data.object) || {};
  if (type === "checkout.session.completed" && obj.id) {
    const s = await stripe(env, "GET", `/checkout/sessions/${encodeURIComponent(obj.id)}`);
    if (!s.subscription) return { ignored: "no subscription" };
    return syncSubscription(env, typeof s.subscription === "string" ? s.subscription : s.subscription.id, s.client_reference_id);
  }
  if (type.startsWith("customer.subscription.") && obj.id) return syncSubscription(env, obj.id);
  if (type.startsWith("invoice.")) {
    const sub = obj.subscription || (obj.parent && obj.parent.subscription_details && obj.parent.subscription_details.subscription);
    if (sub) return syncSubscription(env, typeof sub === "string" ? sub : sub.id);
  }
  return { ignored: type };
}
