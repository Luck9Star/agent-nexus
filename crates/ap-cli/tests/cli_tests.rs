use assert_cmd::Command;
use predicates::prelude::PredicateBooleanExt;

#[test]
fn help_flag() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--help")
        .assert()
        .success()
        .stdout(predicates::str::contains("MCP-native Agent Platform"));
}

#[test]
fn version_flag() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--version")
        .assert()
        .success();
}

#[test]
fn init_command() {
    let dir = tempfile::tempdir().unwrap();
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();
    assert!(dir.path().join("config.toml").exists());
}

#[test]
fn init_command_creates_valid_config() {
    let dir = tempfile::tempdir().unwrap();
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    // Verify config.toml is valid TOML with sources included
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    let config: toml::Value = toml::from_str(&content).expect("config.toml should be valid TOML");
    assert!(config.get("sources").is_some(), "config.toml should contain [sources]");
}

#[test]
fn init_idempotent() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().to_str().unwrap();

    // Run init twice
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["init", "--dir", path])
        .assert()
        .success();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["init", "--dir", path])
        .assert()
        .success();

    // config.toml should still exist and be valid
    assert!(dir.path().join("config.toml").exists());
}

#[test]
fn sources_list_empty() {
    let dir = tempfile::tempdir().unwrap();
    // Create config.toml with no sources
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["sources", "list"])
        .env("CURRENT_DIR", dir.path())
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn sources_add_and_list() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    // Add a source
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "sources",
            "add",
            "--name",
            "test",
            "--url",
            "https://github.com/example/repo",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // List sources
    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(stdout.contains("test"));
}

#[test]
fn sources_remove() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    // Add then remove
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args([
            "sources",
            "add",
            "--name",
            "toremove",
            "--url",
            "https://github.com/example/repo",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["sources", "remove", "toremove"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn check_command() {
    let dir = tempfile::tempdir().unwrap();
    // Create valid config.toml
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .env("OPENAI_API_KEY", "sk-test-key-for-check-test")
        .arg("check")
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn config_get() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();

    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["config", "get", "models.default"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(stdout.contains("openai:gpt-4o"));
}

#[test]
fn config_set() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"old\"\n",
    )
    .unwrap();

    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["config", "set", "models.default", "new-model"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify the value was written
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(content.contains("new-model"));
}

#[test]
fn create_agent() {
    let dir = tempfile::tempdir().unwrap();
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["create", "agent", "my-test-agent", "--description", "A test agent"])
        .current_dir(dir.path())
        .assert()
        .success();

    assert!(dir
        .path()
        .join("agents")
        .join("atomic")
        .join("my-test-agent")
        .join("SKILL.md")
        .exists());
}

#[test]
fn evolution_status() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["evolution", "status"])
        .assert()
        .success();
}

#[test]
fn run_placeholder() {
    // Run mode requires a lockfile and installed agent; should fail gracefully.
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["run", "test-agent", "do", "something"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("lockfile").or(predicates::str::contains("not installed")));
}

#[test]
fn runtime_exec_requires_lockfile() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["runtime", "exec", "test-agent"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicates::str::contains("No lockfile found"));
}

#[test]
fn json_flag_produces_json() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    let output = Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let _: serde_json::Value = serde_json::from_str(stdout).expect("Expected valid JSON output");
}

#[test]
fn invalid_subcommand_fails() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("nonexistent")
        .assert()
        .failure();
}
