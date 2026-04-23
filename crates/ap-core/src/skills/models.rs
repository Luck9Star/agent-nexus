//! Skill models: Skill, `SkillInput`, `SkillOutput`.

use serde::{Deserialize, Serialize};

/// A skill input parameter definition.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillInput {
    #[serde(default)]
    pub name: String,
    #[serde(rename = "type", default)]
    pub input_type: String,
    #[serde(default)]
    pub required: bool,
}

/// A skill output definition.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillOutput {
    #[serde(default)]
    pub name: String,
    #[serde(rename = "type", default)]
    pub output_type: String,
}

/// A parsed SKILL.md — YAML frontmatter + body.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Skill {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub inputs: Vec<SkillInput>,
    #[serde(default)]
    pub outputs: Vec<SkillOutput>,
    #[serde(skip)]
    pub body: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_defaults() {
        let skill: Skill = serde_json::from_str("{}").unwrap();
        assert!(skill.name.is_empty());
        assert!(skill.description.is_empty());
        assert!(skill.inputs.is_empty());
        assert!(skill.outputs.is_empty());
        assert!(skill.body.is_empty());
    }

    #[test]
    fn skill_input_deserialize() {
        let input: SkillInput = serde_json::from_str(
            r#"{"name": "query", "type": "string", "required": true}"#,
        )
        .unwrap();
        assert_eq!(input.name, "query");
        assert_eq!(input.input_type, "string");
        assert!(input.required);
    }

    #[test]
    fn skill_output_deserialize() {
        let output: SkillOutput = serde_json::from_str(
            r#"{"name": "result", "type": "string"}"#,
        )
        .unwrap();
        assert_eq!(output.name, "result");
        assert_eq!(output.output_type, "string");
    }

    #[test]
    fn skill_full_roundtrip() {
        let skill = Skill {
            name: "code-review".into(),
            description: "Reviews code".into(),
            inputs: vec![SkillInput {
                name: "diff".into(),
                input_type: "string".into(),
                required: true,
            }],
            outputs: vec![SkillOutput {
                name: "feedback".into(),
                output_type: "string".into(),
            }],
            body: "## Steps\n1. Read diff".into(),
        };
        let json = serde_json::to_string(&skill).unwrap();
        let deserialized: Skill = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.name, skill.name);
        assert_eq!(deserialized.inputs.len(), 1);
        // body is skipped by serde, so it won't round-trip through JSON
        assert!(deserialized.body.is_empty());
    }
}
