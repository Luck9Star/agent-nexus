//! Shared utilities for model definitions.

use chrono::{DateTime, Utc};

/// Returns the current UTC timestamp. Used as default for datetime fields.
pub fn utc_now() -> DateTime<Utc> {
    Utc::now()
}
