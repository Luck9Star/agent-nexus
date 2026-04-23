//! Extended E2E tests for agent-nexus CLI.
//!
//! Covers: sources, config, create, check, evolution, and general CLI behavior.

use assert_cmd::Command;

// ---------------------------------------------------------------------------
// Sources command tests
// ---------------------------------------------------------------------------

#[test]
fn sources_add_with_branch() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Add a source with --branch develop
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "sources",
            "add",
            "dev-source",
            "https://github.com/example/repo",
            "--branch",
            "develop",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // List and verify the branch is stored
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout).expect("Expected valid JSON");
    // Verify the source appears in JSON output
    let sources = parsed.as_array().expect("Expected JSON array of sources");
    assert_eq!(sources.len(), 1);
    assert_eq!(sources[0]["name"], "dev-source");
    assert_eq!(sources[0]["branch"], "develop");
    assert_eq!(sources[0]["url"], "https://github.com/example/repo");
}

#[test]
fn sources_add_duplicate_upserts() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Add source first time
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "sources",
            "add",
            "dup",
            "https://github.com/example/repo1",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // Add same name again with different URL (upsert semantics)
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "sources",
            "add",
            "dup",
            "https://github.com/example/repo2",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify only one entry exists with the updated URL
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout).expect("Expected valid JSON");
    let sources = parsed.as_array().expect("Expected JSON array");
    assert_eq!(sources.len(), 1);
    assert_eq!(sources[0]["url"], "https://github.com/example/repo2");
}

#[test]
fn sources_remove_nonexistent() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Remove a source that doesn't exist — should fail
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["sources", "remove", "ghost"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("not found"));
}

#[test]
fn sources_list_json_output() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Add a source so we have data
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "sources",
            "add",
            "json-test",
            "https://github.com/example/repo",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // List with --json
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout).expect("Expected valid JSON");
    assert!(parsed.is_array());
    assert_eq!(parsed.as_array().unwrap().len(), 1);
    assert_eq!(parsed[0]["name"], "json-test");
}

#[test]
fn sources_add_empty_url_rejected() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Add a source with empty URL — validation should reject it
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["sources", "add", "bad-url", ""])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("url is empty"));
}

// ---------------------------------------------------------------------------
// Config command tests
// ---------------------------------------------------------------------------

#[test]
fn config_get_nested_path() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[models.providers]\nopenai_base = \"https://api.openai.com/v1\"\n",
    )
    .unwrap();

    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["config", "get", "models.providers.openai_base"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(stdout.contains("https://api.openai.com/v1"));
}

#[test]
fn config_set_nested_path() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[models.providers]\nopenai_base = \"https://old.example.com\"\n",
    )
    .unwrap();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "config",
            "set",
            "models.providers.openai_base",
            "https://new.example.com",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify the value was persisted
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(content.contains("https://new.example.com"));
    assert!(!content.contains("https://old.example.com"));
}

#[test]
fn config_get_nonexistent_key() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["config", "get", "models.nonexistent_key"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("not found"));
}

#[test]
fn config_set_creates_new_key() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();

    // Set a key that doesn't exist yet
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["config", "set", "models.custom_setting", "my-value"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify it was persisted
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(content.contains("custom_setting"));
    assert!(content.contains("my-value"));
}

// ---------------------------------------------------------------------------
// Create command tests
// ---------------------------------------------------------------------------

#[test]
fn create_agent_duplicate_rejected() {
    let dir = tempfile::tempdir().unwrap();

    // Create agent first time
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["create", "agent", "dup-agent"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Create same agent again — should fail
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["create", "agent", "dup-agent"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("already exists"));
}

#[test]
fn create_agent_path_traversal() {
    let dir = tempfile::tempdir().unwrap();

    // Path traversal in agent name should be rejected
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["create", "agent", "../../etc/passwd"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("path traversal"));
}

#[test]
fn create_agent_invalid_name() {
    let dir = tempfile::tempdir().unwrap();

    // Agent name with path separators should be rejected
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["create", "agent", "bad/agent/name"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("path traversal"));

    // Backslash in agent name should also be rejected
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["create", "agent", "bad\\agent"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("path traversal"));
}

// ---------------------------------------------------------------------------
// Check command tests
// ---------------------------------------------------------------------------

#[test]
fn check_with_missing_config() {
    let dir = tempfile::tempdir().unwrap();
    // No config.toml, no sources.yaml — check should fail

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("check")
        .current_dir(dir.path())
        .assert()
        .failure();
}

#[test]
fn check_json_output() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .env("OPENAI_API_KEY", "sk-test-key-for-check-test")
        .args(["--json", "check"])
        .current_dir(dir.path())
        .assert()
        .success();

    // In JSON mode, check should produce valid JSON lines on stdout
    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    for line in stdout.lines() {
        if !line.trim().is_empty() {
            let _: serde_json::Value =
                serde_json::from_str(line).unwrap_or_else(|e| {
                    panic!("Expected valid JSON line, got: {line:?}\nError: {e}")
                });
        }
    }
}

// ---------------------------------------------------------------------------
// Evolution command tests
// ---------------------------------------------------------------------------

#[test]
fn evolution_status_json_output() {
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["--json", "evolution", "status"])
        .assert()
        .success();

    // In JSON mode, output should be valid JSON lines on stdout
    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    for line in stdout.lines() {
        if !line.trim().is_empty() {
            let _: serde_json::Value =
                serde_json::from_str(line).unwrap_or_else(|e| {
                    panic!("Expected valid JSON line, got: {line:?}\nError: {e}")
                });
        }
    }
}

#[test]
fn evolution_promote_placeholder() {
    // Evolution promote is a placeholder — it always succeeds with an info message
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["evolution", "promote", "nonexistent-skill"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("nonexistent-skill"),
        "Output should mention the skill name"
    );
}

// ---------------------------------------------------------------------------
// General CLI tests
// ---------------------------------------------------------------------------

#[test]
fn no_args_shows_help() {
    // Running with no args should show help (clap default behavior)
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .assert()
        .failure()
        .stderr(predicates::str::contains("Usage: agent-nexus"));
}

#[test]
fn help_shows_all_subcommands() {
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--help")
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();

    // Verify all subcommands are listed
    let expected_subcommands = [
        "init",
        "sources",
        "install",
        "run",
        "create",
        "check",
        "config",
        "evolution",
        "runtime",
        "env",
        "version",
    ];
    for subcmd in &expected_subcommands {
        assert!(
            stdout.contains(subcmd),
            "Help text should list '{}' subcommand",
            subcmd
        );
    }
}
