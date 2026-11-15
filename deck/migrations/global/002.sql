-- Agent Deck machine-wide store, schema 2 (W0 integration): learned job cost per account and size.
ALTER TABLE usage_cost ADD COLUMN account TEXT;
CREATE INDEX usage_cost_account_size ON usage_cost(account, size, ts);
CREATE INDEX usage_cost_tool_size ON usage_cost(account_tool, size, ts);
