//! SKILL.md validation for agent packages.
//!
//! Verifies that a SKILL.md file exists in the agent directory and contains
//! the required sections with minimum content.

use std::path::Path;

use thiserror::Error;

/// Errors from SKILL.md checking operations.
#[derive(Debug, Error)]
pub enum SkillCheckerError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

/// A single issue found in the SKILL.md file.
#[derive(Debug, Clone, serde::Serialize)]
pub struct SkillCheckIssue {
    /// Section or aspect that failed (e.g. `"## Overview"`, `"file"`).
    pub section: String,
    /// Human-readable description of the problem.
    pub message: String,
}

impl SkillCheckIssue {
    fn new(section: &str, message: &str) -> Self {
        Self {
            section: section.to_string(),
            message: message.to_string(),
        }
    }
}

/// Result of SKILL.md validation.
#[derive(Debug, Clone, serde::Serialize)]
pub struct SkillCheckResult {
    /// Whether the SKILL.md passed all checks.
    pub valid: bool,
    /// Individual check issues (empty when `valid` is true).
    pub issues: Vec<SkillCheckIssue>,
}

impl SkillCheckResult {
    fn ok() -> Self {
        Self {
            valid: true,
            issues: vec![],
        }
    }

    fn with_issues(issues: Vec<SkillCheckIssue>) -> Self {
        Self {
            valid: false,
            issues,
        }
    }
}

/// Required sections and their minimum content length (chars).
const REQUIRED_SECTIONS: &[(&str, usize)] = &[
    ("## Overview", 50),
    ("## Usage", 50),
    ("## Examples", 50),
];

/// Validate the SKILL.md file in the given agent directory.
///
/// Checks:
/// - SKILL.md file exists
/// - Contains required sections: `## Overview`, `## Usage`, `## Examples`
/// - Each section has at least 50 characters of content
///
/// # Errors
/// Returns [`SkillCheckerError`] if the file cannot be read.
pub fn check_skill_md(agent_dir: &Path) -> Result<SkillCheckResult, SkillCheckerError> {
    let skill_path = agent_dir.join("SKILL.md");

    if !skill_path.exists() {
        return Ok(SkillCheckResult::with_issues(vec![
            SkillCheckIssue::new("file", "SKILL.md file is missing"),
        ]));
    }

    let content = std::fs::read_to_string(&skill_path)?;
    let issues = validate_skill_content(&content);

    if issues.is_empty() {
        Ok(SkillCheckResult::ok())
    } else {
        Ok(SkillCheckResult::with_issues(issues))
    }
}

/// Validate SKILL.md content for required sections and minimum length.
fn validate_skill_content(content: &str) -> Vec<SkillCheckIssue> {
    let mut issues = Vec::new();

    for (section, min_len) in REQUIRED_SECTIONS {
        match find_section_length(content, section) {
            None => {
                issues.push(SkillCheckIssue::new(
                    section,
                    &format!("required section '{section}' is missing"),
                ));
            }
            Some(len) => {
                if len < *min_len {
                    issues.push(SkillCheckIssue::new(
                        section,
                        &format!(
                            "section '{section}' content is too short ({} chars, minimum {})",
                            len, min_len
                        ),
                    ));
                }
            }
        }
    }

    issues
}

/// Extract the content of a specific section from markdown text.
///
/// A section starts with the given heading (e.g. `## Overview`) and ends
/// at the next heading of the same or higher level, or at EOF.
fn find_section_length(content: &str, heading: &str) -> Option<usize> {
    let heading_line = format!("{heading}\n");
    let start = content.find(&heading_line)?;
    // Skip past the heading line itself
    let body_start = start + heading_line.len();

    // Collect lines until the next heading of the same level (##) or higher (#)
    let section_content: String = content[body_start..]
        .lines()
        .take_while(|line| !line.starts_with('#'))
        .collect::<Vec<_>>()
        .join("\n");

    Some(section_content.trim().len())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_skill_md(dir: &std::path::Path, content: &str) {
        std::fs::write(dir.join("SKILL.md"), content).unwrap();
    }

    #[test]
    fn valid_skill_md() {
        let dir = tempfile::tempdir().unwrap();
        write_skill_md(
            dir.path(),
            r#"# My Agent Skill

## Overview
This agent processes documentation files and fills in missing template variables.
It supports multiple output formats and can handle nested template structures.

## Usage
Run the agent with the target file path as argument. The agent will scan for
template placeholders and fill them in using the configured model backend.

## Examples
```bash
agent-nexus run doc-filler --input README.tmpl.md --output README.md
```
The above command processes a template and outputs a completed file.
"#,
        );
        let result = check_skill_md(dir.path()).unwrap();
        assert!(result.valid);
        assert!(result.issues.is_empty());
    }

    #[test]
    fn missing_skill_md() {
        let dir = tempfile::tempdir().unwrap();
        let result = check_skill_md(dir.path()).unwrap();
        assert!(!result.valid);
        assert_eq!(result.issues.len(), 1);
        assert_eq!(result.issues[0].section, "file");
        assert!(result.issues[0].message.contains("missing"));
    }

    #[test]
    fn missing_required_sections() {
        let dir = tempfile::tempdir().unwrap();
        write_skill_md(
            dir.path(),
            r#"# Incomplete Skill

## Overview
Only overview section present, nothing else useful here at all.
"#,
        );
        let result = check_skill_md(dir.path()).unwrap();
        assert!(!result.valid);
        let sections: Vec<&str> = result.issues.iter().map(|i| i.section.as_str()).collect();
        assert!(sections.contains(&"## Usage"));
        assert!(sections.contains(&"## Examples"));
    }

    #[test]
    fn section_too_short() {
        let dir = tempfile::tempdir().unwrap();
        write_skill_md(
            dir.path(),
            r#"# Short Skill

## Overview
Too brief.

## Usage
Also too brief.

## Examples
Brief.
"#,
        );
        let result = check_skill_md(dir.path()).unwrap();
        assert!(!result.valid);
        // All three sections should be flagged as too short
        assert_eq!(result.issues.len(), 3);
        for issue in &result.issues {
            assert!(issue.message.contains("too short"));
        }
    }

    #[test]
    fn section_boundary_at_next_heading() {
        let dir = tempfile::tempdir().unwrap();
        write_skill_md(
            dir.path(),
            r#"# Agent

## Overview
This is a valid overview section with enough content to pass the minimum
length requirement. It should be properly extracted.

## Usage
This is valid usage content that has enough characters to meet the minimum
threshold for the section validation check.

## Examples
Here are some examples of how to use this agent effectively in practice
with various configuration options and input formats.
"#,
        );
        let result = check_skill_md(dir.path()).unwrap();
        assert!(result.valid);
    }
}
