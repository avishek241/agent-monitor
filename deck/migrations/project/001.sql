-- Agent Deck project store, schema 1 (design §3.2)
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE tasks(id TEXT PRIMARY KEY, num INTEGER UNIQUE, state TEXT NOT NULL, priority TEXT, size TEXT,
      worker TEXT, resume_at INTEGER, rev INTEGER NOT NULL DEFAULT 1,
      data TEXT NOT NULL, created_at INTEGER, updated_at INTEGER, state_since INTEGER);
CREATE INDEX tasks_state ON tasks(state);
CREATE TABLE attempts(task TEXT, n INTEGER, worker TEXT, account TEXT, adapter TEXT, session_id TEXT,
      pid INTEGER, pgid INTEGER, pid_start TEXT, run_dir TEXT, slot INTEGER,
      started_at INTEGER, ended_at INTEGER, lease_until INTEGER, last_heartbeat INTEGER,
      cpu_secs REAL, out_offset INTEGER DEFAULT 0, exit_code INTEGER, outcome TEXT,
      usage_start REAL, usage_end REAL, tokens INTEGER, continuation_of INTEGER, token_hash TEXT, lane INTEGER,
      PRIMARY KEY(task, n));
CREATE TABLE cards(task TEXT PRIMARY KEY, rev INTEGER, data TEXT);
CREATE TABLE card_history(id INTEGER PRIMARY KEY, task TEXT, attempt INTEGER, ts INTEGER, op TEXT, patch TEXT);
CREATE TRIGGER card_history_no_update BEFORE UPDATE ON card_history BEGIN SELECT RAISE(ABORT, 'card_history is append-only'); END;
CREATE TRIGGER card_history_no_delete BEFORE DELETE ON card_history BEGIN SELECT RAISE(ABORT, 'card_history is append-only'); END;
CREATE TABLE handoffs(task TEXT, attempt INTEGER, ts INTEGER, valid INTEGER, data TEXT, PRIMARY KEY(task, attempt));
CREATE TABLE domains(name TEXT, holder TEXT, holder_kind TEXT, acquired_at INTEGER, expires_at INTEGER, PRIMARY KEY(name, holder));
CREATE TABLE domain_waits(name TEXT, task TEXT, since INTEGER, reserved INTEGER DEFAULT 0, PRIMARY KEY(name, task));
CREATE TABLE slots(slot INTEGER PRIMARY KEY, holder TEXT, since INTEGER);
CREATE TABLE merge_queue(id INTEGER PRIMARY KEY, task TEXT UNIQUE, enqueued_at INTEGER, state TEXT, started_at INTEGER, note TEXT);
CREATE TABLE events(id INTEGER PRIMARY KEY, ts INTEGER, type TEXT, severity TEXT, task TEXT, attempt INTEGER, worker TEXT,
       for_ TEXT, text TEXT, data TEXT);
CREATE INDEX events_task ON events(task);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TABLE seq(name TEXT PRIMARY KEY, value INTEGER);
