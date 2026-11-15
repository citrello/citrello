-- Credits (15 Nov 2026): 100 free credits once per account, plus a plan's monthly credits (Student 1,000, Researcher
-- 4,000), paid through Stripe. A claim check costs 3 credits per paper, a library find 8. Replaces the monthly
-- allowance of 0003 (ai_usage stays, unused) and the pro flag.
ALTER TABLE users ADD COLUMN bonus_credits INTEGER NOT NULL DEFAULT 100;
ALTER TABLE users ADD COLUMN plan TEXT;                -- 'student' | 'researcher' (from the Stripe subscription)
ALTER TABLE users ADD COLUMN plan_interval TEXT;       -- 'month' | 'year'
ALTER TABLE users ADD COLUMN plan_status TEXT;         -- Stripe's subscription status
ALTER TABLE users ADD COLUMN plan_anchor INTEGER;      -- when the subscription started: monthly credits renew from here
ALTER TABLE users ADD COLUMN plan_until INTEGER;       -- end of the period paid for
ALTER TABLE users ADD COLUMN stripe_customer TEXT;
ALTER TABLE users ADD COLUMN stripe_subscription TEXT;
ALTER TABLE users ADD COLUMN allowance_window INTEGER; -- start of the month the used count belongs to
ALTER TABLE users ADD COLUMN allowance_used INTEGER NOT NULL DEFAULT 0;

CREATE TABLE credit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  at INTEGER NOT NULL,
  delta INTEGER NOT NULL,     -- negative: spent; positive: given
  what TEXT NOT NULL          -- "claim check", "library find", "welcome", "given by admin"...
);
CREATE INDEX credit_log_user ON credit_log (user_id, at);
