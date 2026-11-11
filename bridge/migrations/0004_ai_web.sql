-- Web checks ("Find support on the web") have their own monthly allowance.
ALTER TABLE ai_usage ADD COLUMN web INTEGER NOT NULL DEFAULT 0;
