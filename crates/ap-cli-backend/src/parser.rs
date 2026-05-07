//! Output parsing — JSON dot-path extraction and text regex extraction.

use crate::types::{BackendConfig, CLIResult, JsonPathConfig};

pub fn extract_json_value<'a>(data: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut current = data;
    for key in path.split('.') {
        match current {
            serde_json::Value::Object(map) => {
                current = map.get(key)?;
            }
            _ => return None,
        }
    }
    Some(current)
}

pub fn parse_json_output(stdout: &str, config: &BackendConfig) -> CLIResult {
    let mut result = CLIResult {
        raw_stdout: stdout.to_string(),
        ..Default::default()
    };

    let json_data: serde_json::Value = match serde_json::from_str(stdout.trim()) {
        Ok(v) => v,
        Err(_) => {
            result.text = stdout.to_string();
            result.parse_error = true;
            return result;
        }
    };

    build_result_from_json(&json_data, &config.json_paths, &mut result);
    result.raw_stdout = stdout.to_string();
    result
}

fn build_result_from_json(
    data: &serde_json::Value,
    paths: &JsonPathConfig,
    result: &mut CLIResult,
) {
    if let Some(ref p) = paths.text {
        if let Some(v) = extract_json_value(data, p) {
            result.text = v.as_str().unwrap_or("").to_string();
        }
    }
    if let Some(ref p) = paths.session_id {
        if let Some(v) = extract_json_value(data, p) {
            result.session_id = v.as_str().map(String::from);
        }
    }
    if let Some(ref p) = paths.model {
        if let Some(v) = extract_json_value(data, p) {
            result.model = v.as_str().unwrap_or("").to_string();
        }
    }
    if let Some(ref p) = paths.input_tokens {
        if let Some(v) = extract_json_value(data, p) {
            result.input_tokens = v.as_u64();
        }
    }
    if let Some(ref p) = paths.output_tokens {
        if let Some(v) = extract_json_value(data, p) {
            result.output_tokens = v.as_u64();
        }
    }
}

pub fn parse_text_output(stdout: &str, stderr: &str, config: &BackendConfig) -> CLIResult {
    let mut result = CLIResult {
        text: stdout.to_string(),
        raw_stdout: stdout.to_string(),
        raw_stderr: stderr.to_string(),
        ..Default::default()
    };

    if let Some(ref pattern) = config.text_patterns.session_id {
        if let Ok(re) = regex::Regex::new(pattern) {
            if let Some(caps) = re.captures(stderr) {
                result.session_id = caps.get(1).map(|m| m.as_str().to_string());
            }
        }
    }

    if let Some(ref pattern) = config.text_patterns.model {
        if let Ok(re) = regex::Regex::new(pattern) {
            if let Some(caps) = re.captures(stdout) {
                result.model = caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
            }
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::TextPatternConfig;

    fn claude_config() -> BackendConfig {
        serde_json::from_str(r#"{
            "command": "claude",
            "args": ["-p"],
            "json_paths": {
                "text": "result",
                "session_id": "session_id",
                "model": "model",
                "input_tokens": "usage.input_tokens",
                "output_tokens": "usage.output_tokens"
            }
        }"#).unwrap()
    }

    #[test]
    fn extract_simple_path() {
        let data = serde_json::json!({"result": "hello", "session_id": "abc"});
        assert_eq!(extract_json_value(&data, "result").unwrap().as_str(), Some("hello"));
    }

    #[test]
    fn extract_nested_path() {
        let data = serde_json::json!({"usage": {"input_tokens": 100}});
        assert_eq!(extract_json_value(&data, "usage.input_tokens").unwrap().as_u64(), Some(100));
    }

    #[test]
    fn extract_missing_returns_none() {
        let data = serde_json::json!({"result": "text"});
        assert!(extract_json_value(&data, "nonexistent.path").is_none());
    }

    #[test]
    fn parse_json_claude_format() {
        let stdout = r#"{"result": "planned", "session_id": "s1", "model": "claude-sonnet-4", "usage": {"input_tokens": 100, "output_tokens": 50}}"#;
        let result = parse_json_output(stdout, &claude_config());
        assert_eq!(result.text, "planned");
        assert_eq!(result.session_id, Some("s1".to_string()));
        assert_eq!(result.model, "claude-sonnet-4");
        assert_eq!(result.input_tokens, Some(100));
        assert_eq!(result.output_tokens, Some(50));
        assert!(!result.parse_error);
    }

    #[test]
    fn parse_json_invalid_falls_back() {
        let result = parse_json_output("not json", &claude_config());
        assert_eq!(result.text, "not json");
        assert!(result.parse_error);
    }

    #[test]
    fn parse_text_with_regex() {
        let config = BackendConfig {
            command: "openclaw".into(),
            output_format: "text".into(),
            text_patterns: TextPatternConfig {
                session_id: Some(r"session[:\s]+([a-f0-9-]+)".into()),
                model: None,
            },
            ..Default::default()
        };
        let result = parse_text_output("done", "session: abc-123 started", &config);
        assert_eq!(result.text, "done");
        assert_eq!(result.session_id, Some("abc-123".to_string()));
    }
}
