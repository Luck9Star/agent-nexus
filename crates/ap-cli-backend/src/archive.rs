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
        return Err(CLIBackendError::JsonParse(
            format!("Archive path contains disallowed characters: {}", path_str)
        ));
    }

    conn.execute(
        &format!("ATTACH DATABASE '{}' AS archive", path_str),
        [],
    )?;

    let result = archive_inner(conn, config);
    let _ = conn.execute("DETACH DATABASE archive", []);
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
