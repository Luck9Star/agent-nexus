//! `SkillLoader` — parses SKILL.md files with YAML frontmatter (--- delimiters).

use crate::skills::models::Skill;

/// Parse a SKILL.md content string into a `Skill`.
///
/// Format:
/// ```text
/// ---
/// name: my-skill
/// description: Does things
/// inputs:
///   - name: query
///     type: string
///     required: true
/// outputs:
///   - name: result
///     type: string
/// ---
/// Skill body in markdown...
/// ```
///
/// If no frontmatter is found, all YAML fields default to empty and the
/// entire content becomes the body.
///
/// # Errors
/// Returns an error if the underlying operation fails.
pub fn parse_skill(content: &str) -> Result<Skill, String> {
    let content = content.trim_start();

    // Normalize CRLF to LF for simpler parsing
    let content = content.replace("\r\n", "\n");
    let content = content.trim_start();

    // Must start with "---"
    if !content.starts_with("---") {
        return Ok(Skill {
            body: content.to_string(),
            ..Default::default()
        });
    }

    // Split into lines
    let lines: Vec<&str> = content.split('\n').collect();

    // First line must be "---"
    if lines[0].trim() != "---" {
        return Ok(Skill {
            body: content.to_string(),
            ..Default::default()
        });
    }

    // Find the closing "---" line (starting from line 1)
    let closing_idx = lines[1..]
        .iter()
        .position(|l| l.trim() == "---")
        .map(|p| p + 1); // offset by 1 since we sliced from [1..]

    let (yaml_str, body) = if let Some(ci) = closing_idx {
        let yaml_part = lines[1..ci].join("\n");
        let body_part = lines[ci + 1..].join("\n");
        (yaml_part.trim().to_string(), body_part.trim().to_string())
    } else {
        // No closing delimiter — everything after opening --- is YAML
        let yaml_part = lines[1..].join("\n");
        (yaml_part.trim().to_string(), String::new())
    };

    if yaml_str.is_empty() {
        return Ok(Skill {
            body,
            ..Default::default()
        });
    }

    let mut skill: Skill = serde_yml::from_str(&yaml_str)
        .map_err(|e| format!("invalid YAML frontmatter: {e}"))?;
    skill.body = body;
    Ok(skill)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_full_frontmatter() {
        let content = "\
---
name: code-review
description: Reviews code for quality issues
inputs:
  - name: diff
    type: string
    required: true
outputs:
  - name: feedback
    type: string
---
## Steps

1. Read the diff
2. Analyze patterns
";
        let skill = parse_skill(content).unwrap();
        assert_eq!(skill.name, "code-review");
        assert_eq!(skill.description, "Reviews code for quality issues");
        assert_eq!(skill.inputs.len(), 1);
        assert_eq!(skill.inputs[0].name, "diff");
        assert!(skill.inputs[0].required);
        assert_eq!(skill.outputs.len(), 1);
        assert_eq!(skill.outputs[0].name, "feedback");
        assert!(skill.body.contains("## Steps"));
    }

    #[test]
    fn parse_no_frontmatter() {
        let content = "## My Skill\n\nThis is just body content.";
        let skill = parse_skill(content).unwrap();
        assert!(skill.name.is_empty());
        assert!(skill.description.is_empty());
        assert!(skill.inputs.is_empty());
        assert!(skill.outputs.is_empty());
        assert_eq!(skill.body, content);
    }

    #[test]
    fn parse_empty_frontmatter() {
        let content = "---\n---\nSome body text";
        let skill = parse_skill(content).unwrap();
        assert!(skill.name.is_empty());
        assert!(skill.body.contains("Some body text"));
    }

    #[test]
    fn parse_partial_frontmatter() {
        let content = "---\nname: test-skill\n---\nBody here";
        let skill = parse_skill(content).unwrap();
        assert_eq!(skill.name, "test-skill");
        assert!(skill.description.is_empty());
        assert!(skill.inputs.is_empty());
        assert_eq!(skill.body, "Body here");
    }

    #[test]
    fn parse_invalid_yaml() {
        let content = "---\nname: [invalid: yaml: {\n---\nbody";
        let result = parse_skill(content);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("invalid YAML frontmatter"));
    }

    #[test]
    fn parse_only_name_no_closing_delimiter() {
        let content = "---\nname: orphan-skill";
        let skill = parse_skill(content).unwrap();
        assert_eq!(skill.name, "orphan-skill");
        assert!(skill.body.is_empty());
    }

    #[test]
    fn parse_preserves_body_whitespace() {
        let content = "---\nname: ws-test\n---\n\n  Indented line\n\nTrailing\n";
        let skill = parse_skill(content).unwrap();
        assert!(skill.body.contains("Indented line"));
        assert!(skill.body.contains("Trailing"));
    }

    #[test]
    fn parse_crlf_line_endings() {
        let content = "---\r\nname: crlf-skill\r\n---\r\nBody content";
        let skill = parse_skill(content).unwrap();
        assert_eq!(skill.name, "crlf-skill");
    }
}
