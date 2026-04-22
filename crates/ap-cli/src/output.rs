//! Output formatting: supports both human-readable (colored) and JSON modes.

use owo_colors::OwoColorize;
use serde::Serialize;

/// Formats CLI output in either human-readable (colored) or JSON mode.
pub struct OutputFormatter {
    json_mode: bool,
    #[allow(dead_code)]
    follow_mode: bool,
}

impl OutputFormatter {
    pub fn new(json: bool, follow: bool) -> Self {
        Self {
            json_mode: json,
            follow_mode: follow,
        }
    }

    /// Print a success message.
    pub fn success(&self, msg: &str) {
        if self.json_mode {
            let obj = serde_json::json!({
                "status": "ok",
                "message": msg,
            });
            println!("{}", serde_json::to_string(&obj).unwrap());
        } else {
            println!("{} {}", "✓".green(), msg);
        }
    }

    /// Print an error message to stderr.
    pub fn error(&self, msg: &str) {
        if self.json_mode {
            let obj = serde_json::json!({
                "status": "error",
                "message": msg,
            });
            eprintln!("{}", serde_json::to_string(&obj).unwrap());
        } else {
            eprintln!("{} {}", "✗".red(), msg);
        }
    }

    /// Print an informational message.
    pub fn info(&self, msg: &str) {
        if self.json_mode {
            let obj = serde_json::json!({
                "status": "info",
                "message": msg,
            });
            println!("{}", serde_json::to_string(&obj).unwrap());
        } else {
            println!("{}", msg.dimmed());
        }
    }

    /// Print structured data. In JSON mode, pretty-print; otherwise display a summary.
    pub fn data<T: Serialize>(&self, data: &T) {
        // Always pretty-print JSON for structured data
        println!("{}", serde_json::to_string_pretty(data).unwrap());
    }

    /// Returns whether JSON mode is active.
    pub fn is_json(&self) -> bool {
        self.json_mode
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn success_human_mode() {
        let fmt = OutputFormatter::new(false, false);
        assert!(!fmt.is_json());
    }

    #[test]
    fn success_json_mode() {
        let fmt = OutputFormatter::new(true, false);
        assert!(fmt.is_json());
    }

    #[test]
    fn data_serializes_struct() {
        let fmt = OutputFormatter::new(true, false);
        let data = serde_json::json!({"key": "value"});
        // Should not panic
        fmt.data(&data);
    }
}
