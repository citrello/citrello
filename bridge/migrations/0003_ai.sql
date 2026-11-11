-- Claude checks ("What your sources say"): a pro flag per account and how much each account used per month.
ALTER TABLE users ADD COLUMN pro INTEGER NOT NULL DEFAULT 0;

CREATE TABLE ai_usage (
  user_id TEXT NOT NULL,
  month TEXT NOT NULL,          -- "2026-11"
  units INTEGER NOT NULL DEFAULT 0,  -- one unit = one cited sentence judged against one paper; a web search = 5
  PRIMARY KEY (user_id, month)
);
