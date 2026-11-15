// Sets up Citrello's plans in Stripe (run after each deploy; it only creates what's missing, so it's safe to repeat):
// the four prices (Student and Researcher, monthly and yearly, found later by their lookup keys), the webhook that
// tells Citrello's server about payments, and the customer portal (switch plan, change card, cancel).
// Needs STRIPE_SECRET_KEY (a test key in the sandbox, a live key for real payments). Prints the price IDs.
//   STRIPE_SECRET_KEY=rk_test_... node scripts/stripe-setup.mjs [https://mcp.citrello.app]

const KEY = process.env.STRIPE_SECRET_KEY;
const BASE = process.env.STRIPE_API_BASE || "https://api.stripe.com";
const BRIDGE = (process.argv[2] || "https://mcp.citrello.app").replace(/\/$/, "");
const TAX_CODE = "txcd_10105001";  // Artificial Intelligence as a Service, cloud based, personal use (Managed Payments)
const PLANS = [
  { key: "citrello_student_monthly", product: "Citrello Student", amount: 600, interval: "month",
    description: "1,000 credits a month for Claude's citation checks in Citrello (about 330 claim checks)." },
  { key: "citrello_student_yearly", product: "Citrello Student", amount: 5000, interval: "year",
    description: "1,000 credits a month for Claude's citation checks in Citrello (about 330 claim checks)." },
  { key: "citrello_researcher_monthly", product: "Citrello Researcher", amount: 1800, interval: "month",
    description: "4,000 credits a month for Claude's citation checks in Citrello (about 1,330 claim checks)." },
  { key: "citrello_researcher_yearly", product: "Citrello Researcher", amount: 15000, interval: "year",
    description: "4,000 credits a month for Claude's citation checks in Citrello (about 1,330 claim checks)." },
];
const EVENTS = ["checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
                "customer.subscription.deleted", "invoice.paid"];

function form(obj, prefix = "", out = new URLSearchParams()) {
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    const key = prefix ? `${prefix}[${k}]` : k;
    if (typeof v === "object") form(v, key, out); else out.append(key, String(v));
  }
  return out;
}
async function api(method, path, params) {
  const qs = method === "GET" && params ? "?" + form(params) : "";
  const r = await fetch(`${BASE}/v1${path}${qs}`, { method, body: method !== "GET" && params ? form(params) : undefined,
    headers: { Authorization: `Bearer ${KEY}`, "Stripe-Version": "2025-03-31.basil",
               ...(method !== "GET" ? { "Content-Type": "application/x-www-form-urlencoded" } : {}) } });
  const d = await r.json();
  if (!r.ok) throw new Error(`${method} ${path}: ${(d.error && d.error.message) || r.status}`);
  return d;
}

if (!KEY) { console.log("No STRIPE_SECRET_KEY: plans not set up."); process.exit(0); }
console.log(KEY.includes("_test_") ? "Stripe test mode (sandbox)" : "Stripe LIVE mode");

// products and prices
const existing = await api("GET", "/prices", { lookup_keys: PLANS.map((p) => p.key), limit: 10, expand: ["data.product"] });
const products = {};
for (const p of existing.data) products[p.product.name] = p.product.id;
const ids = {};
for (const plan of PLANS) {
  const found = existing.data.find((p) => p.lookup_key === plan.key);
  if (found) { ids[plan.key] = found.id; continue; }
  if (!products[plan.product]) {
    const list = await api("GET", "/products", { active: "true", limit: 100 });
    const prod = list.data.find((x) => x.name === plan.product) ||
      await api("POST", "/products", { name: plan.product, description: plan.description, tax_code: TAX_CODE,
                                      metadata: { citrello: "plan" } });
    products[plan.product] = prod.id;
  }
  const price = await api("POST", "/prices", { product: products[plan.product], currency: "gbp", unit_amount: plan.amount,
    recurring: { interval: plan.interval }, lookup_key: plan.key, tax_behavior: "inclusive", nickname: `${plan.product} (${plan.interval}ly)` });
  ids[plan.key] = price.id;
  console.log(`created ${plan.key}`);
}
for (const [k, v] of Object.entries(ids)) console.log(`${k}: ${v}`);

// webhook: Citrello's server reads each subscription back from Stripe, so no signing secret is needed
const hookUrl = `${BRIDGE}/api/stripe/webhook`;
const hooks = await api("GET", "/webhook_endpoints", { limit: 100 });
if (!hooks.data.some((h) => h.url === hookUrl)) {
  await api("POST", "/webhook_endpoints", { url: hookUrl, enabled_events: EVENTS, description: "Citrello credits" });
  console.log(`webhook created: ${hookUrl}`);
} else console.log(`webhook already there: ${hookUrl}`);

// customer portal: switch between the plans, update the card, cancel at the end of the period
try {
  const confs = await api("GET", "/billing_portal/configurations", { limit: 10, is_default: "true" });
  if (!confs.data.length) {
    const byProduct = {};
    for (const plan of PLANS) (byProduct[products[plan.product]] ||= []).push(ids[plan.key]);
    await api("POST", "/billing_portal/configurations", {
      business_profile: { headline: "Citrello: manage your plan", privacy_policy_url: "https://citrello.app/privacy",
                          terms_of_service_url: "https://citrello.app/terms" },
      features: {
        payment_method_update: { enabled: true },
        invoice_history: { enabled: true },
        subscription_cancel: { enabled: true, mode: "at_period_end" },
        subscription_update: { enabled: true, default_allowed_updates: ["price"], proration_behavior: "create_prorations",
          products: Object.entries(byProduct).map(([product, prices]) => ({ product, prices })) },
      },
    });
    console.log("customer portal set up");
  } else console.log("customer portal already set up");
} catch (e) {
  console.log(`customer portal not set up (${e.message}): set it up in the Stripe Dashboard → Settings → Billing → Customer portal`);
}
