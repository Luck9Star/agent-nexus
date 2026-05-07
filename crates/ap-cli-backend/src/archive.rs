//! Database archival — ATTACH DATABASE based cold storage.

use crate::types::{CLIBackendError, DataLifecycleConfig};
use rusqlite::Connection;
use std::path::Path;

pub fn archive_old_data(
    conn: &Connection,
    config: &DataLifecycleConfig,
    archive_path: &Path,
) -> Result<u64, CLIBackendError> {
    let path_str = archive_path.display().to_string();

    let valid = path_str.chars().all(|c| c.is_alphanumeric() || c == '/' || c == '.' || c == '-' || c == '_');
    if !valid || path_str.is_empty() {
        return Err(CLIBackendError::Io(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("Archive path contains disallowed characters: {}", path_str)
        )));
    }

    conn.execute(
        &format!("ATTACH DATABASE '{}' AS archive", path_str),
        [],
    )?;

    let result = archive_inner(conn, config);
    if let Err(e) = conn.execute("DETACH DATABASE archive", []) {
        tracing::warn!("Failed to detach archive database: {e}");
    }
    result
}

fn archive_inner(
    conn: &Connection,
    config: &DataLifecycleConfig,
) -> Result<u64, CLIBackendError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS archive.task_executions AS SELECT * FROM task_executions WHERE 0;
         CREATE TABLE IF NOT EXISTS archive.cli_sessions AS SELECT * FROM cli_sessions WHERE 0;"
    )?;

    let modifier = format!("-{} days", config.hot_days);
    let migrated = conn.execute(
        "INSERT INTO archive.task_executions SELECT * FROM task_executions \
         WHERE created_at < datetime('now', ?1)",
        [&modifier],
    )?;

    conn.execute(
        "DELETE FROM task_executions WHERE created_at < datetime('now', ?1)",
        [&modifier],
    )?;

    Ok(migrated as u64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::DataLifecycleConfig;
    use tempfile::TempDir;

    #[test]
    fn archive_migrates_old_records() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("main.db");

        // Create and populate the database using raw connection
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch("PRAGMA journal_mode=WAL;").unwrap();

        // Create the schema manually
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS task_executions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       TEXT NOT NULL,
                backend_type  TEXT NOT NULL,
                backend_name  TEXT NOT NULL,
                model         TEXT,
                session_id    TEXT,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                duration_ms   INTEGER,
                status        TEXT DEFAULT 'success',
                error         TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS cli_sessions (
                session_id TEXT PRIMARY KEY, name TEXT, backend_name TEXT NOT NULL
            );"
        ).unwrap();

        // Insert an old record
        conn.execute(
            "INSERT INTO task_executions (task_id, backend_type, backend_name, model, session_id, input_tokens, output_tokens, duration_ms, status, error, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, datetime('now', '-100 days'))",
            rusqlite::params!["old-task", "cli", "claude-code", "model", Option::<String>::None, 100, 50, 1000, "success", Option::<String>::None],
        ).unwrap();

        let archive_path = dir.path().join("archive.db");
        let config = DataLifecycleConfig {
            hot_days: 30,
            ..Default::default()
        };

        let migrated = archive_old_data(&conn, &config, &archive_path).unwrap();
        assert_eq!(migrated, 1);

        // Verify the archive DB has the record
        let archive_conn = rusqlite::Connection::open(&archive_path).unwrap();
        let count: i64 = archive_conn
            .query_row("SELECT COUNT(*) FROM task_executions", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 1);

        // Verify main DB has no records
        let main_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM task_executions", [], |row| row.get(0))
            .unwrap();
        assert_eq!(main_count, 0);
    }

    #[test]
    fn archive_rejects_invalid_path() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let conn = rusqlite::Connection::open(&db_path).unwrap();

        let config = DataLifecycleConfig::default();
        let bad_path = std::path::PathBuf::from("/tmp/evil; DROP TABLE--.db");
        let result = archive_old_data(&conn, &config, &bad_path);
        let err_msg = result.unwrap_err().to_string();
        assert!(err_msg.contains("disallowed characters"), "Expected path validation error, got: {err_msg}");
    }

    #[test]
    fn archive_no_old_records() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch("PRAGMA journal_mode=WAL;").unwrap();

        // Create schema
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS task_executions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       TEXT NOT NULL,
                backend_type  TEXT NOT NULL,
                backend_name  TEXT NOT NULL,
                model         TEXT,
                session_id    TEXT,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                duration_ms   INTEGER,
                status        TEXT DEFAULT 'success',
                error         TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS cli_sessions (
                session_id TEXT PRIMARY KEY, name TEXT, backend_name TEXT NOT NULL
            );"
        ).unwrap();

        // Insert a recent record
        conn.execute(
            "INSERT INTO task_executions (task_id, backend_type, backend_name, model, session_id, input_tokens, output_tokens, duration_ms, status, error) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params!["recent-task", "cli", "claude-code", "model", Option::<String>::None, 10, 5, 500, "success", Option::<String>::None],
        ).unwrap();

        let archive_path = dir.path().join("archive.db");
        let config = DataLifecycleConfig {
            hot_days: 30,
            ..Default::default()
        };

        let migrated = archive_old_data(&conn, &config, &archive_path).unwrap();
        assert_eq!(migrated, 0);

        // Main DB still has the record
        let main_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM task_executions", [], |row| row.get(0))
            .unwrap();
        assert_eq!(main_count, 1);
    }
}
