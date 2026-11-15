// Credits: what each account can spend on Claude's checks. 100 free credits once (bonus_credits, also where credits
// given by hand go), plus the monthly credits of a plan (Student 1,000, Researcher 4,000), paid through Stripe
// (src/billing.js). A plan's credits are spent first (they don't carry over to the next month), then the bonus.
// Yearly plans get their credits month by month too.

export const COST = { claim: 3, library: 8, web: 25 };  // credits per paper checked / per library find / per web find
export const PLANS = {
  student: { name: "Student", credits: 1000 },
  researcher: { name: "Researcher", credits: 4000 },
};
const ACTIVE = ["active", "trialing", "past_due"];
const now = () => Math.floor(Date.now() / 1000);

export class CreditError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

// The month (from the plan's start date) that `t` falls in: [start, end)
export function monthWindow(anchor, t = now()) {
  const a = new Date(anchor * 1000);
  const at = (k) => {
    const d = new Date(Date.UTC(a.getUTCFullYear(), a.getUTCMonth() + k, 1, a.getUTCHours(), a.getUTCMinutes(), a.getUTCSeconds()));
    const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
    d.setUTCDate(Math.min(a.getUTCDate(), last));
    return Math.floor(d.getTime() / 1000);
  };
  let k = Math.max(0, (new Date(t * 1000).getUTCFullYear() - a.getUTCFullYear()) * 12 + new Date(t * 1000).getUTCMonth() - a.getUTCMonth() - 1);
  while (at(k + 1) <= t) k++;
  return { start: at(k), end: at(k + 1) };
}

export function planActive(u, t = now()) {
  return !!(u && u.plan && PLANS[u.plan] && ACTIVE.includes(u.plan_status) && (u.plan_until || 0) + 3 * 86400 > t);
}

function allowance(u, t = now()) {  // {per_month, left, window} of an active plan, else null
  if (!planActive(u, t)) return null;
  const w = monthWindow(u.plan_anchor || u.plan_until || t, t);
  const used = u.allowance_window === w.start ? u.allowance_used : 0;
  const per = PLANS[u.plan].credits;
  return { per_month: per, used, left: Math.max(0, per - used), window: w };
}

async function user(env, userId) {
  const u = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(userId).first();
  if (!u) throw new CreditError(401, "unauthorized", "Please sign in again.");
  return u;
}

// What the account can spend, for the app and the Word add-in
export async function balance(env, userId) {
  const u = await user(env, userId);
  const a = allowance(u);
  const bonus = Math.max(0, u.bonus_credits || 0);
  const credits = (a ? a.left : 0) + bonus;
  return {
    ready: !!env.ANTHROPIC_API_KEY, billing: !!env.STRIPE_SECRET_KEY, costs: COST,
    credits, bonus, checks_left: Math.floor(credits / COST.claim),
    plan: a ? { id: u.plan, name: PLANS[u.plan].name, interval: u.plan_interval, per_month: a.per_month, left: a.left,
                renews_at: a.window.end, paid_until: u.plan_until, status: u.plan_status } : null,
    can_manage: !!u.stripe_customer,
  };
}

// Check there are enough credits; returns a function that takes them (called once Claude has answered)
export async function charge(env, userId, amount, what) {
  const b = await balance(env, userId);
  if (b.credits < amount) {
    throw new CreditError(402, "no_credits", b.plan
      ? `This needs ${amount} credits and ${b.credits} are left this month. They renew on ${new Date(b.plan.renews_at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long" })}, or switch to a bigger plan.`
      : `This needs ${amount} credits and ${b.credits} are left. Choose a plan to keep checking with Claude.`);
  }
  return async () => {
    const u = await user(env, userId), t = now();
    const a = allowance(u, t);
    let fromPlan = 0, fromBonus = amount;
    if (a) { fromPlan = Math.min(a.left, amount); fromBonus = amount - fromPlan; }
    await env.DB.batch([
      env.DB.prepare(`UPDATE users SET allowance_window = ?, allowance_used = ?, bonus_credits = MAX(0, bonus_credits - ?) WHERE id = ?`)
        .bind(a ? a.window.start : u.allowance_window, a ? a.used + fromPlan : u.allowance_used, fromBonus, userId),
      env.DB.prepare("INSERT INTO credit_log (user_id, at, delta, what) VALUES (?, ?, ?, ?)").bind(userId, t, -amount, what),
    ]);
  };
}

// Credits given by hand (support, refunds, testers): added to the bonus credits
export async function give(env, email, amount, what = "given by admin") {
  const u = await env.DB.prepare("SELECT id FROM users WHERE email = ?").bind(String(email || "").trim().toLowerCase()).first();
  if (!u) throw new CreditError(404, "not_found", "No account with that email.");
  await env.DB.batch([
    env.DB.prepare("UPDATE users SET bonus_credits = MAX(0, bonus_credits + ?) WHERE id = ?").bind(amount, u.id),
    env.DB.prepare("INSERT INTO credit_log (user_id, at, delta, what) VALUES (?, ?, ?, ?)").bind(u.id, now(), amount, what),
  ]);
  return balance(env, u.id);
}
