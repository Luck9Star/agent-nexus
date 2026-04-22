//! Store error types.

/// Errors that can arise from the evolution store.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}

/// Convenience alias for results in this module.
pub type Result<T> = std::result::Result<T, StoreError>;
