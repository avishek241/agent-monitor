-- Agent Deck machine-wide store, schema 1 (design §3.3)
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE accounts(id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE usage(account TEXT, read_at INTEGER, source TEXT, pct5 REAL, pct7 REAL, resets5 INTEGER, resets7 INTEGER,
      confidence TEXT, PRIMARY KEY(account, read_at));
CREATE TABLE limits(id INTEGER PRIMARY KEY, account TEXT, kind TEXT, detected_at INTEGER, resets_at INTEGER,
      source TEXT, project TEXT, task TEXT, attempt INTEGER, raw TEXT, cleared_at INTEGER);
CREATE TABLE workers(id TEXT PRIMARY KEY, account TEXT, state TEXT, idle_since INTEGER);
CREATE TABLE lanes(worker TEXT, lane INTEGER, state TEXT, project TEXT, task TEXT, attempt INTEGER, since INTEGER, PRIMARY KEY(worker, lane));
CREATE TABLE account_state(account TEXT PRIMARY KEY, state TEXT, capacity_json TEXT, spare_since INTEGER, updated_at INTEGER);
CREATE TABLE ext_domains(name TEXT, holder TEXT, project TEXT, acquired_at INTEGER, expires_at INTEGER, PRIMARY KEY(name, holder));
CREATE TABLE ext_domain_waits(name TEXT, task TEXT, since INTEGER, reserved INTEGER DEFAULT 0, PRIMARY KEY(name, task));
CREATE TABLE events(id INTEGER PRIMARY KEY, ts INTEGER, type TEXT, severity TEXT, task TEXT, attempt INTEGER, worker TEXT,
       for_ TEXT, text TEXT, data TEXT, project TEXT, src_id INTEGER);
CREATE TRIGGER gevents_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER gevents_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TABLE usage_cost(account_tool TEXT, size TEXT, delta5 REAL, delta7 REAL, minutes REAL, ts INTEGER);
