//! Agent manifest validation (TOML format).
//!
//! Validates the structure and required fields of `agent.toml` manifest files
//! before an agent is installed or loaded.

use std::path::Path;

use thiserror::Error;

/// Errors from manifest validation operations.
#[derive(Debug, Error)]
pub enum ManifestCheckerError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("TOML parse error: {0}")]
    Toml(#[from] toml::de::Error),
}

/// A single validation issue found in the manifest.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ValidationError {
    /// Machine-readable field path (e.g. `"name"`, `"type"`).
    pub field: String,
    /// Human-readable description of the problem.
    pub message: String,
}

impl ValidationError {
    fn new(field: &str, message: &str) -> Self {
        Self {
            field: field.to_string(),
            message: message.to_string(),
        }
    }
}

/// Result of manifest validation.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ManifestValidationResult {
    /// Whether the manifest passed all checks.
    pub valid: bool,
    /// Individual validation issues (empty when `valid` is true).
    pub errors: Vec<ValidationError>,
}

impl ManifestValidationResult {
    /// Create a passing result with no errors.
    fn ok() -> Self {
        Self {
            valid: true,
            errors: vec![],
        }
    }

    /// Create a failing result with the given errors.
    fn with_errors(errors: Vec<ValidationError>) -> Self {
        Self {
            valid: false,
            errors,
        }
    }
}

/// Validate an agent manifest TOML file.
///
/// Reads the file at `path`, parses it as TOML, and checks:
/// - Required fields: `name`, `version`, `type`, `entry`
/// - `type` must be `"Atomic"` or `"Composite"`
///
/// # Errors
/// Returns [`ManifestCheckerError`] if the file cannot be read or parsed.
pub fn validate_manifest(path: &Path) -> Result<ManifestValidationResult, ManifestCheckerError> {
    let content = std::fs::read_to_string(path)?;
    let manifest: toml::Value = toml::from_str(&content)?;
    validate_manifest_value(&manifest)
}

/// Validate an already-parsed TOML value as an agent manifest.
fn validate_manifest_value(
    manifest: &toml::Value,
) -> Result<ManifestValidationResult, ManifestCheckerError> {
    let mut errors = Vec::new();

    // Required string fields
    for field in &["name", "version", "type", "entry"] {
        match manifest.get(field) {
            None => {
                errors.push(ValidationError::new(
                    field,
                    &format!("required field '{field}' is missing"),
                ));
            }
            Some(toml::Value::String(_)) => {}
            Some(_) => {
                errors.push(ValidationError::new(
                    field,
                    &format!("field '{field}' must be a string"),
                ));
            }
        }
    }

    // Validate `type` value if present and a string
    if let Some(toml::Value::String(type_val)) = manifest.get("type") {
        if type_val != "Atomic" && type_val != "Composite" {
            errors.push(ValidationError::new(
                "type",
                &format!(
                    "field 'type' must be 'Atomic' or 'Composite', got '{type_val}'"
                ),
            ));
        }
    }

    if errors.is_empty() {
        Ok(ManifestValidationResult::ok())
    } else {
        Ok(ManifestValidationResult::with_errors(errors))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_manifest(dir: &std::path::Path, content: &str) -> std::path::PathBuf {
        let path = dir.join("agent.toml");
        std::fs::write(&path, content).unwrap();
        path
    }

    #[test]
    fn valid_atomic_manifest() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_manifest(
            dir.path(),
            r#"
name = "doc-filler"
version = "1.0.0"
type = "Atomic"
entry = "main.py"
description = "Fills in documentation templates"
"#,
        );
        let result = validate_manifest(&path).unwrap();
        assert!(result.valid);
        assert!(result.errors.is_empty());
    }

    #[test]
    fn valid_composite_manifest() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_manifest(
            dir.path(),
            r#"
name = "feature-delivery"
version = "0.3.0"
type = "Composite"
entry = "pipeline.toml"
dependencies = ["doc-filler", "code-reviewer"]
"#,
        );
        let result = validate_manifest(&path).unwrap();
        assert!(result.valid);
    }

    #[test]
    fn missing_required_fields() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_manifest(
            dir.path(),
            r#"
name = "incomplete-agent"
"#,
        );
        let result = validate_manifest(&path).unwrap();
        assert!(!result.valid);
        assert_eq!(result.errors.len(), 3); // version, type, entry missing

        let fields: Vec<&str> = result.errors.iter().map(|e| e.field.as_str()).collect();
        assert!(fields.contains(&"version"));
        assert!(fields.contains(&"type"));
        assert!(fields.contains(&"entry"));
    }

    #[test]
    fn invalid_agent_type() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_manifest(
            dir.path(),
            r#"
name = "bad-type"
version = "1.0.0"
type = "Hybrid"
entry = "main.py"
"#,
        );
        let result = validate_manifest(&path).unwrap();
        assert!(!result.valid);
        assert_eq!(result.errors.len(), 1);
        assert_eq!(result.errors[0].field, "type");
        assert!(result.errors[0].message.contains("Hybrid"));
    }

    #[test]
    fn non_string_required_field() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_manifest(
            dir.path(),
            r#"
name = 42
version = "1.0.0"
type = "Atomic"
entry = "main.py"
"#,
        );
        let result = validate_manifest(&path).unwrap();
        assert!(!result.valid);
        assert_eq!(result.errors.len(), 1);
        assert_eq!(result.errors[0].field, "name");
        assert!(result.errors[0].message.contains("must be a string"));
    }

    #[test]
    fn missing_file_returns_io_error() {
        let result = validate_manifest(Path::new("/nonexistent/agent.toml"));
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("No such file"));
    }
}
