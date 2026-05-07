//! CLISessionStore — SQLite session persistence with WAL and triggers.

use crate::types::{CLIBackendError, CLISession, DataLifecycleConfig, ExecutionRecord};
use rusqlite::{params, Connection};
use std::path::Path;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS cli_sessions (
    session_id   TEXT PRIMARY KEY,
    name         TEXT,
    backend_name TEXT NOT NULL,
    model        TEXT,
    task_id      TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now')),
    turn_count   INTEGER DEFAULT 1,
    metadata     TEXT
);

CREATE TABLE IF NOT EXISTS task_executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    backend_type  TEXT NOT NULL,
    backend_name  TEXT NOT NULL,
    model         TEXT,
    session_id    TEXT REFERENCES cli_sessions(session_id),
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    status        TEXT DEFAULT 'success',
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backend_health (
    backend_name TEXT PRIMARY KEY,
    is_available INTEGER DEFAULT 0,
    last_check   TEXT,
    version      TEXT,
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date         TEXT NOT NULL,
    backend_name TEXT NOT NULL,
    total_calls  INTEGER DEFAULT 0,
    success_calls INTEGER DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    avg_duration_ms     REAL DEFAULT 0,
    PRIMARY KEY (date, backend_name)
);

CREATE TRIGGER IF NOT EXISTS trg_update_daily_stats
AFTER INSERT ON task_executions
BEGIN
    INSERT INTO daily_stats (date, backend_name, total_calls, success_calls,
                             total_input_tokens, total_output_tokens, avg_duration_ms)
    VALUES (DATE('now'), NEW.backend_name, 1,
            CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
            COALESCE(NEW.input_tokens, 0), COALESCE(NEW.output_tokens, 0),
            COALESCE(NEW.duration_ms, 0))
    ON CONFLICT(date, backend_name) DO UPDATE SET
        total_calls = total_calls + 1,
        success_calls = success_calls + CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens + COALESCE(NEW.input_tokens, 0),
        total_output_tokens = total_output_tokens + COALESCE(NEW.output_tokens, 0),
        avg_duration_ms = (avg_duration_ms * (total_calls - 1) + COALESCE(NEW.duration_ms, 0)) / total_calls;
END;

CREATE TRIGGER IF NOT EXISTS trg_delete_daily_stats
AFTER DELETE ON task_executions
BEGIN
    UPDATE daily_stats SET
        total_calls = total_calls - 1,
        success_calls = success_calls - CASE WHEN OLD.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens - COALESCE(OLD.input_tokens, 0),
        total_output_tokens = total_output_tokens - COALESCE(OLD.output_tokens, 0)
    WHERE date = DATE(OLD.created_at) AND backend_name = OLD.backend_name;
END;
";

const PRAGMAS: &str = "
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=1000;
PRAGMA synchronous=NORMAL;
";

pub struct CLISessionStore {
    conn: Connection,
}

impl CLISessionStore {
    pub fn open(db_path: &Path) -> Result<Self, CLIBackendError> {
        let conn = Connection::open(db_path)?;
        conn.execute_batch(PRAGMAS)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self { conn })
    }

    pub fn save_session(&self, session: &CLISession) -> Result<(), CLIBackendError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO cli_sessions \
             (session_id, name, backend_name, model, task_id, \
              created_at, last_used_at, turn_count, metadata) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                session.session_id, session.name, session.backend_name,
                session.model, session.task_id, session.created_at,
                session.last_used_at, session.turn_count, session.metadata,
            ],
        )?;
        Ok(())
    }

    pub fn get_session(&self, session_id: &str) -> Result<Option<CLISession>, CLIBackendError> {
        let mut stmt = self.conn.prepare(
            "SELECT session_id, name, backend_name, model, task_id, \
                    created_at, last_used_at, turn_count, metadata \
             FROM cli_sessions WHERE session_id = ?1"
        )?;

        let mut rows = stmt.query(params![session_id])?;
        match rows.next()? {
            Some(row) => Ok(Some(CLISession {
                session_id: row.get(0)?,
                name: row.get(1)?,
                backend_name: row.get(2)?,
                model: row.get(3)?,
                task_id: row.get(4)?,
                created_at: row.get(5)?,
                last_used_at: row.get(6)?,
                turn_count: row.get(7)?,
                metadata: row.get(8)?,
            })),
            None => Ok(None),
        }
    }

    pub fn record_execution(
        &self,
        record: &ExecutionRecord,
    ) -> Result<(), CLIBackendError> {
        self.conn.execute(
            "INSERT INTO task_executions \
             (task_id, backend_type, backend_name, model, session_id, \
              input_tokens, output_tokens, duration_ms, status, error) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                record.task_id, record.backend_type, record.backend_name, record.model, record.session_id,
                record.input_tokens, record.output_tokens, record.duration_ms, record.status, record.error,
            ],
        )?;
        Ok(())
    }

    pub fn close(self) {}

    pub fn prepare_stmt(&self, sql: &str) -> Result<rusqlite::Statement<'_>, CLIBackendError> {
        self.conn.prepare(sql).map_err(CLIBackendError::Database)
    }

    pub fn archive_old_data(
        &self,
        config: &DataLifecycleConfig,
        archive_path: &Path,
    ) -> Result<u64, CLIBackendError> {
        crate::archive::archive_old_data(&self.conn, config, archive_path)
    }

    pub fn cleanup_sessions(&self, max_age_days: u32) -> Result<u64, CLIBackendError> {
        let count = self.conn.execute(
            "DELETE FROM cli_sessions WHERE last_used_at < datetime('now', ?)",
            [format!("-{max_age_days} days")],
        )?;
        Ok(count as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::ExecutionRecord;
    use tempfile::TempDir;

    fn setup() -> (TempDir, CLISessionStore) {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let store = CLISessionStore::open(&db_path).unwrap();
        (dir, store)
    }

    #[test]
    fn save_and_get_session() {
        let (_dir, store) = setup();
        let session = CLISession {
            session_id: "s1".into(),
            backend_name: "claude-code".into(),
            model: Some("claude-sonnet-4".into()),
            name: Some("test session".into()),
            created_at: "2026-01-01T00:00:00".into(),
            last_used_at: "2026-01-01T00:00:00".into(),
            turn_count: 1,
            ..Default::default()
        };
        store.save_session(&session).unwrap();
        let retrieved = store.get_session("s1").unwrap();
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().backend_name, "claude-code");
    }

    #[test]
    fn get_nonexistent_returns_none() {
        let (_dir, store) = setup();
        assert!(store.get_session("nonexistent").unwrap().is_none());
    }

    #[test]
    fn record_execution_updates_daily_stats() {
        let (_dir, store) = setup();
        store.record_execution(&ExecutionRecord {
            task_id: "t1",
            backend_type: "cli",
            backend_name: "claude-code",
            model: Some("model"),
            input_tokens: Some(100),
            output_tokens: Some(50),
            duration_ms: Some(1000),
            status: "success",
            ..Default::default()
        }).unwrap();
        store.record_execution(&ExecutionRecord {
            task_id: "t2",
            backend_type: "cli",
            backend_name: "claude-code",
            model: Some("model"),
            input_tokens: Some(50),
            output_tokens: Some(0),
            duration_ms: Some(500),
            status: "error",
            ..Default::default()
        }).unwrap();

        let mut stmt = store.prepare_stmt(
            "SELECT total_calls, success_calls FROM daily_stats WHERE backend_name = 'claude-code'"
        ).unwrap();
        let row: (i64, i64) = stmt.query_row([], |row| Ok((row.get(0)?, row.get(1)?))).unwrap();
        assert_eq!(row.0, 2);
        assert_eq!(row.1, 1);
    }

    #[test]
    fn cleanup_sessions_removes_old() {
        let (_dir, store) = setup();

        // Insert an old session (manually set last_used_at)
        let old_session = CLISession {
            session_id: "old-s1".into(),
            backend_name: "claude-code".into(),
            created_at: "2020-01-01T00:00:00".into(),
            last_used_at: "2020-01-01T00:00:00".into(),
            turn_count: 1,
            ..Default::default()
        };
        store.save_session(&old_session).unwrap();

        // Insert a recent session
        let recent_session = CLISession {
            session_id: "recent-s2".into(),
            backend_name: "claude-code".into(),
            created_at: "2026-05-01T00:00:00".into(),
            last_used_at: "2026-05-01T00:00:00".into(),
            turn_count: 1,
            ..Default::default()
        };
        store.save_session(&recent_session).unwrap();

        // Cleanup sessions older than 90 days
        let removed = store.cleanup_sessions(90).unwrap();
        assert_eq!(removed, 1);

        // Recent session should still be there
        assert!(store.get_session("recent-s2").unwrap().is_some());
        // Old session should be gone
        assert!(store.get_session("old-s1").unwrap().is_none());
    }

    #[test]
    fn cleanup_sessions_nothing_to_remove() {
        let (_dir, store) = setup();
        let removed = store.cleanup_sessions(90).unwrap();
        assert_eq!(removed, 0);
    }

    #[test]
    fn save_session_upsert() {
        let (_dir, store) = setup();
        let session = CLISession {
            session_id: "s1".into(),
            backend_name: "claude-code".into(),
            created_at: "2026-01-01T00:00:00".into(),
            last_used_at: "2026-01-01T00:00:00".into(),
            turn_count: 1,
            ..Default::default()
        };
        store.save_session(&session).unwrap();

        // Update with same ID
        let updated = CLISession {
            session_id: "s1".into(),
            backend_name: "gemini-cli".into(),
            turn_count: 5,
            ..session
        };
        store.save_session(&updated).unwrap();

        let retrieved = store.get_session("s1").unwrap().unwrap();
        assert_eq!(retrieved.backend_name, "gemini-cli");
        assert_eq!(retrieved.turn_count, 5);
    }
}
