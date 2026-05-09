//! Static security audit for agent code.
//!
//! Scans agent source files for disallowed patterns, hardcoded secrets,
//! and suspicious network access patterns.

use std::path::Path;

use thiserror::Error;

/// Errors from security audit operations.
#[derive(Debug, Error)]
pub enum SecurityAuditError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

/// Severity level of a security finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum Severity {
    /// Informational — no action required.
    Info,
    /// Warning — should be reviewed.
    Warning,
    /// Critical — must be addressed before install.
    Critical,
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Severity::Info => write!(f, "Info"),
            Severity::Warning => write!(f, "Warning"),
            Severity::Critical => write!(f, "Critical"),
        }
    }
}

/// A single security finding from the audit.
#[derive(Debug, Clone, serde::Serialize)]
pub struct SecurityFinding {
    /// Severity level.
    pub severity: Severity,
    /// Category of the finding (e.g. `"dangerous_function"`, `"hardcoded_secret"`).
    pub category: String,
    /// Human-readable description.
    pub message: String,
    /// File path relative to agent root (or file name).
    pub file: String,
    /// 1-based line number where the finding was detected (0 if unknown).
    pub line: usize,
}

/// Result of a security audit.
#[derive(Debug, Clone, serde::Serialize)]
pub struct SecurityAuditResult {
    /// Individual findings from the audit.
    pub findings: Vec<SecurityFinding>,
    /// Whether the audit passed (no Critical findings).
    pub passed: bool,
}

impl SecurityAuditResult {
    fn new(findings: Vec<SecurityFinding>) -> Self {
        let passed = !findings.iter().any(|f| f.severity == Severity::Critical);
        Self { findings, passed }
    }
}

/// Disallowed Python function calls.
const DANGEROUS_PATTERNS: &[(&str, &str)] = &[
    ("eval(", "dangerous_function"),
    ("exec(", "dangerous_function"),
    ("__import__(", "dangerous_function"),
    ("compile(", "dangerous_function"),
    ("os.system(", "dangerous_function"),
    ("subprocess.call(", "dangerous_function"),
];

/// Patterns suggesting hardcoded secrets.
const SECRET_PATTERNS: &[(&str, &str)] = &[
    ("OPENAI_API_KEY=", "hardcoded_secret"),
    ("ANTHROPIC_API_KEY=", "hardcoded_secret"),
    ("sk-proj-", "hardcoded_secret"),
    ("sk-ant-", "hardcoded_secret"),
    ("AKIA", "aws_access_key"),
    ("-----BEGIN RSA PRIVATE KEY-----", "hardcoded_secret"),
];

/// Patterns suggesting network access.
const NETWORK_PATTERNS: &[(&str, &str)] = &[
    ("requests.get", "network_access"),
    ("requests.post", "network_access"),
    ("urllib.request", "network_access"),
    ("urllib.urlopen", "network_access"),
    ("http.client", "network_access"),
    ("socket.connect", "network_access"),
];

/// Run a security audit on the given agent directory.
///
/// Scans all `.py` files for dangerous patterns, hardcoded secrets, and
/// network access. Returns a structured result with findings and severity levels.
///
/// # Errors
/// Returns [`SecurityAuditError`] if the directory cannot be read.
pub fn audit_agent(agent_dir: &Path) -> Result<SecurityAuditResult, SecurityAuditError> {
    let mut findings = Vec::new();

    if !agent_dir.is_dir() {
        return Ok(SecurityAuditResult::new(findings));
    }

    let py_files = collect_py_files(agent_dir)?;
    for file_path in &py_files {
        let content = std::fs::read_to_string(file_path)?;
        let relative = file_path
            .strip_prefix(agent_dir)
            .unwrap_or(file_path)
            .to_string_lossy()
            .to_string();

        scan_content(&content, &relative, &mut findings);
    }

    Ok(SecurityAuditResult::new(findings))
}

/// Recursively collect `.py` files in a directory.
fn collect_py_files(dir: &Path) -> Result<Vec<std::path::PathBuf>, SecurityAuditError> {
    let mut files = Vec::new();
    let entries = std::fs::read_dir(dir)?;

    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            files.extend(collect_py_files(&path)?);
        } else if path.extension().is_some_and(|ext| ext == "py") {
            files.push(path);
        }
    }

    Ok(files)
}

/// Scan file content for security patterns and append findings.
fn scan_content(content: &str, file: &str, findings: &mut Vec<SecurityFinding>) {
    for (line_num, line) in content.lines().enumerate() {
        let line_num_1based = line_num + 1;

        // Check dangerous patterns
        for (pattern, category) in DANGEROUS_PATTERNS {
            if line.contains(pattern) {
                findings.push(SecurityFinding {
                    severity: Severity::Critical,
                    category: category.to_string(),
                    message: format!("dangerous function call detected: {pattern}"),
                    file: file.to_string(),
                    line: line_num_1based,
                });
            }
        }

        // Check hardcoded secrets
        for (pattern, category) in SECRET_PATTERNS {
            if line.contains(pattern) {
                findings.push(SecurityFinding {
                    severity: Severity::Critical,
                    category: category.to_string(),
                    message: format!("potential hardcoded secret detected: {pattern}"),
                    file: file.to_string(),
                    line: line_num_1based,
                });
            }
        }

        // Check network access
        for (pattern, category) in NETWORK_PATTERNS {
            if line.contains(pattern) {
                findings.push(SecurityFinding {
                    severity: Severity::Warning,
                    category: category.to_string(),
                    message: format!("network access pattern detected: {pattern}"),
                    file: file.to_string(),
                    line: line_num_1based,
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_py_file(dir: &std::path::Path, name: &str, content: &str) {
        std::fs::write(dir.join(name), content).unwrap();
    }

    #[test]
    fn clean_agent_passes() {
        let dir = tempfile::tempdir().unwrap();
        write_py_file(
            dir.path(),
            "main.py",
            r#"
def hello():
    print("Hello, world!")
    return 42
"#,
        );
        let result = audit_agent(dir.path()).unwrap();
        assert!(result.passed);
        assert!(result.findings.is_empty());
    }

    #[test]
    fn detects_dangerous_functions() {
        let dir = tempfile::tempdir().unwrap();
        write_py_file(
            dir.path(),
            "evil.py",
            r#"
def run_user_code(code):
    result = eval(code)
    exec(code)
    return result
"#,
        );
        let result = audit_agent(dir.path()).unwrap();
        assert!(!result.passed);
        let dangerous: Vec<_> = result
            .findings
            .iter()
            .filter(|f| f.category == "dangerous_function")
            .collect();
        assert_eq!(dangerous.len(), 2);
        assert_eq!(dangerous[0].severity, Severity::Critical);
        assert!(dangerous[0].message.contains("eval("));
        assert!(dangerous[1].message.contains("exec("));
    }

    #[test]
    fn detects_hardcoded_secrets() {
        let dir = tempfile::tempdir().unwrap();
        write_py_file(
            dir.path(),
            "config.py",
            r#"
OPENAI_API_KEY="sk-proj-abc123def456"
"#,
        );
        let result = audit_agent(dir.path()).unwrap();
        assert!(!result.passed);
        let secrets: Vec<_> = result
            .findings
            .iter()
            .filter(|f| f.category == "hardcoded_secret")
            .collect();
        assert!(!secrets.is_empty());
        assert!(secrets.iter().any(|f| f.message.contains("OPENAI_API_KEY=")));
        assert!(secrets.iter().any(|f| f.message.contains("sk-proj-")));
    }

    #[test]
    fn detects_network_access_as_warning() {
        let dir = tempfile::tempdir().unwrap();
        write_py_file(
            dir.path(),
            "client.py",
            r#"
import requests

def fetch_data(url):
    return requests.get(url)
"#,
        );
        let result = audit_agent(dir.path()).unwrap();
        // Network access is Warning, not Critical, so audit still passes
        assert!(result.passed);
        let network: Vec<_> = result
            .findings
            .iter()
            .filter(|f| f.category == "network_access")
            .collect();
        assert_eq!(network.len(), 1);
        assert_eq!(network[0].severity, Severity::Warning);
    }

    #[test]
    fn empty_directory_passes() {
        let dir = tempfile::tempdir().unwrap();
        let result = audit_agent(dir.path()).unwrap();
        assert!(result.passed);
        assert!(result.findings.is_empty());
    }

    #[test]
    fn nonexistent_directory_passes() {
        let result = audit_agent(Path::new("/nonexistent/path")).unwrap();
        assert!(result.passed);
        assert!(result.findings.is_empty());
    }

    #[test]
    fn scans_subdirectories() {
        let dir = tempfile::tempdir().unwrap();
        let sub = dir.path().join("skills");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(sub.join("skill.py"), "eval('bad')").unwrap();

        let result = audit_agent(dir.path()).unwrap();
        assert!(!result.passed);
        assert_eq!(result.findings.len(), 1);
        assert!(result.findings[0].file.contains("skills/skill.py"));
    }
}
