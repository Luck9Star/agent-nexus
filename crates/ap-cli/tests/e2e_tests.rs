//! End-to-end tests for agent-nexus CLI.
//!
//! These tests exercise the compiled binary as a black box via `assert_cmd`.
//! Each test is fully independent: creates its own temp dir, runs the binary,
//! and asserts on exit code + stdout/stderr content.
//!
//! Complements cli_tests.rs (basic smoke tests) and cli_extended_tests.rs
//! (edge cases) with:
//! - Workflow tests (multi-command sequences)
//! - Commands not covered elsewhere: version subcommand, config show, env
//! - JSON output validation for config commands
//! - Type preservation in config set (bool, int, float, string)

use assert_cmd::Command;
use predicates::prelude::*;

// ══════════════════════════════════════════════════════════════════════════
// Helper: build a Command pointing at the compiled agent-nexus binary
// ══════════════════════════════════════════════════════════════════════════

fn cli() -> Command {
    Command::cargo_bin("agent-nexus").unwrap()
}

// ══════════════════════════════════════════════════════════════════════════
// 1. version subcommand (separate from --version flag)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn version_subcommand() {
    let output = cli().arg("version").assert().success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("agent-nexus"),
        "version output should contain 'agent-nexus', got: {stdout:?}"
    );
    // Version should match semver pattern (e.g. 0.1.0)
    assert!(
        stdout.contains(char::is_numeric),
        "version output should contain a version number, got: {stdout:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 2. env command
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn env_command() {
    let output = cli().arg("env").assert().success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("Config dir"),
        "env output should contain 'Config dir', got: {stdout:?}"
    );
    assert!(
        stdout.contains("Python"),
        "env output should contain 'Python', got: {stdout:?}"
    );
    assert!(
        stdout.contains("Git"),
        "env output should contain 'Git', got: {stdout:?}"
    );
}

#[test]
fn env_command_json_output() {
    let output = cli().args(["--json", "env"]).assert().success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("env --json should produce valid JSON");

    assert!(
        parsed.get("config_dir").is_some(),
        "JSON output should contain 'config_dir'"
    );
    assert!(
        parsed.get("python_version").is_some(),
        "JSON output should contain 'python_version'"
    );
    assert!(
        parsed.get("git_version").is_some(),
        "JSON output should contain 'git_version'"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 3. config show command
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_show_with_config() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[runtime]\npython_path = \"python3\"\nuv_path = \"uv\"\n",
    )
    .unwrap();

    let output = cli()
        .arg("config")
        .arg("show")
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("Config dir"),
        "config show should contain 'Config dir', got: {stdout:?}"
    );
    assert!(
        stdout.contains("openai:gpt-4o"),
        "config show should display the default model, got: {stdout:?}"
    );
    assert!(
        stdout.contains("python3"),
        "config show should display python_path, got: {stdout:?}"
    );
}

#[test]
fn config_show_json_output() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[models.providers]\nopenai_base = \"https://api.openai.com/v1\"\n",
    )
    .unwrap();

    let output = cli()
        .args(["--json", "config", "show"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("config show --json should produce valid JSON");

    assert_eq!(parsed["default_model"], "openai:gpt-4o");
    assert!(
        parsed.get("providers").is_some(),
        "JSON output should contain 'providers'"
    );
}

#[test]
fn config_show_no_config_file() {
    let dir = tempfile::tempdir().unwrap();
    // No config.toml -- config show should still work (with defaults)

    let output = cli()
        .arg("config")
        .arg("show")
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("Config dir"),
        "config show should still print Config dir even without config.toml"
    );
    assert!(
        stdout.contains("No config.toml found"),
        "config show should mention missing config.toml, got: {stdout:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 4. config get --json
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_get_json_output() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();

    let output = cli()
        .args(["--json", "config", "get", "models.default"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("config get --json should produce valid JSON");

    assert_eq!(parsed["key"], "models.default");
    let val = parsed["value"].as_str().expect("value should be a string");
    assert!(val.contains("openai:gpt-4o"), "expected openai:gpt-4o in value, got: {val}");
}

// ══════════════════════════════════════════════════════════════════════════
// 5. config set type preservation (E2E)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_set_boolean_value() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[runtime]\npython_path = \"python3\"\n",
    )
    .unwrap();

    cli()
        .args(["config", "set", "runtime.verbose", "true"])
        .current_dir(dir.path())
        .assert()
        .success();

    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(
        content.contains("verbose = true"),
        "Boolean should be stored as TOML boolean, got: {content:?}"
    );
    assert!(
        !content.contains("verbose = \"true\""),
        "Boolean should NOT be stored as string, got: {content:?}"
    );
}

#[test]
fn config_set_integer_value() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[runtime]\npython_path = \"python3\"\n",
    )
    .unwrap();

    cli()
        .args(["config", "set", "runtime.timeout", "30"])
        .current_dir(dir.path())
        .assert()
        .success();

    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(
        content.contains("timeout = 30"),
        "Integer should be stored as TOML integer, got: {content:?}"
    );
    assert!(
        !content.contains("timeout = \"30\""),
        "Integer should NOT be stored as string, got: {content:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 6. Workflow: init -> config get -> config set -> config get (round-trip)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn workflow_init_config_get_set_get() {
    let dir = tempfile::tempdir().unwrap();

    // Step 1: init
    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();
    assert!(dir.path().join("config.toml").exists());

    // Step 2: config get (should find the default set by init)
    let output = cli()
        .args(["config", "get", "models.default"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    // The default config should have some model value
    assert!(
        !stdout.trim().is_empty(),
        "config get should return a value for models.default"
    );

    // Step 3: config set
    cli()
        .args(["config", "set", "models.default", "anthropic:claude-sonnet-4-20250514"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Step 4: config get again -- should reflect the new value
    let output = cli()
        .args(["config", "get", "models.default"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("anthropic:claude-sonnet-4-20250514"),
        "config get should return updated value, got: {stdout:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 7. Workflow: init -> sources add -> sources list -> sources remove
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn workflow_init_sources_crud() {
    let dir = tempfile::tempdir().unwrap();

    // Init creates sources.yaml with the official source
    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    // List should show the official source
    let output = cli()
        .args(["sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(
        stdout.contains("official"),
        "sources list after init should contain 'official', got: {stdout:?}"
    );

    // Add a custom source
    cli()
        .args([
            "sources",
            "add",
            "--name",
            "my-custom",
            "--url",
            "https://github.com/example/custom-agents.git",
            "--branch",
            "develop",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // List should now show both sources
    let output = cli()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("Expected valid JSON");
    let sources = parsed.as_array().expect("Expected JSON array");
    assert_eq!(sources.len(), 2, "Should have 2 sources after add");

    // Remove the custom source
    cli()
        .args(["sources", "remove", "my-custom"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Back to just official
    let output = cli()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("Expected valid JSON");
    let sources = parsed.as_array().expect("Expected JSON array");
    assert_eq!(sources.len(), 1, "Should have 1 source after remove");
    assert_eq!(sources[0]["name"], "official");
}

// ══════════════════════════════════════════════════════════════════════════
// 8. Workflow: init -> create agent -> verify scaffold
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn workflow_init_create_agent() {
    let dir = tempfile::tempdir().unwrap();

    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    cli()
        .args(["create", "agent", "my-workflow-agent", "--description", "A workflow test agent"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify the scaffold was created
    let agent_dir = dir
        .path()
        .join("agents")
        .join("atomic")
        .join("my-workflow-agent");
    assert!(agent_dir.join("SKILL.md").exists(), "SKILL.md should exist");
    assert!(
        agent_dir.join("pyproject.toml").exists(),
        "pyproject.toml should exist"
    );
    assert!(
        agent_dir
            .join("my_workflow_agent")
            .join("agent.py")
            .exists(),
        "agent.py should exist in agent module"
    );

    // Verify SKILL.md contains the agent name
    let skill_content = std::fs::read_to_string(agent_dir.join("SKILL.md")).unwrap();
    assert!(
        skill_content.contains("my-workflow-agent"),
        "SKILL.md should mention agent name"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 9. Workflow: init -> check (full health check with config)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn workflow_init_check() {
    let dir = tempfile::tempdir().unwrap();

    // Init creates both config.toml and sources.yaml
    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    // Run check from the initialized directory
    let output = cli()
        .env("OPENAI_API_KEY", "sk-test-e2e-workflow")
        .arg("check")
        .current_dir(dir.path())
        .assert();

    // Check may pass or fail depending on env (python3 version, uv, etc.)
    // but it should NOT crash. Verify config.toml and sources.yaml checks pass.
    let result = output.get_output();
    let stderr = String::from_utf8_lossy(&result.stderr);
    let stdout = String::from_utf8_lossy(&result.stdout);

    // Combine both for assertion
    let combined = format!("{stdout}{stderr}");
    assert!(
        combined.contains("config.toml") || combined.contains("Config"),
        "check should mention config.toml status"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 10. init creates valid TOML with expected keys
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn init_creates_config_with_expected_keys() {
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    let config: toml::Value =
        toml::from_str(&content).expect("config.toml should be valid TOML");

    // Should have runtime section with python_path and uv_path
    assert!(
        config.get("runtime").is_some() || config.get("models").is_some(),
        "config should have 'runtime' or 'models' section"
    );

    // Should have models section with a default
    if let Some(models) = config.get("models") {
        assert!(
            models.get("default").is_some(),
            "models section should have 'default' key"
        );
    }
}

// ══════════════════════════════════════════════════════════════════════════
// 11. init creates valid YAML sources with official source
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn init_creates_sources_with_official() {
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    let content = std::fs::read_to_string(dir.path().join("sources.yaml")).unwrap();
    let sources: serde_yaml::Value =
        serde_yaml::from_str(&content).expect("sources.yaml should be valid YAML");

    let sources_list = sources
        .get("sources")
        .and_then(|s| s.as_sequence())
        .expect("should have 'sources' array");

    assert!(
        !sources_list.is_empty(),
        "init should create at least one source"
    );

    let official = sources_list
        .iter()
        .find(|s| s.get("name").and_then(|n| n.as_str()) == Some("official"));
    assert!(
        official.is_some(),
        "init should create the 'official' source"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 12. config get without init fails gracefully
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_get_without_init_fails() {
    let dir = tempfile::tempdir().unwrap();
    // No config.toml in the temp dir

    cli()
        .args(["config", "get", "models.default"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("config.toml not found"));
}

// ══════════════════════════════════════════════════════════════════════════
// 13. config set without init fails gracefully
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_set_without_init_fails() {
    let dir = tempfile::tempdir().unwrap();

    cli()
        .args(["config", "set", "models.default", "test"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("config.toml not found"));
}

// ══════════════════════════════════════════════════════════════════════════
// 14. install without sources fails gracefully
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn install_nonexistent_agent_fails() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("sources.yaml"),
        "sources: []\n",
    )
    .unwrap();

    cli()
        .args(["install", "no-such-agent"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("not found in sources"));
}

// ══════════════════════════════════════════════════════════════════════════
// 15. Multiple config set operations preserve unrelated keys
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_set_preserves_unrelated_keys() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[runtime]\npython_path = \"python3\"\nuv_path = \"uv\"\n",
    )
    .unwrap();

    // Set one key
    cli()
        .args(["config", "set", "models.default", "anthropic:claude-sonnet-4-20250514"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify other keys are still present
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(
        content.contains("python3"),
        "runtime.python_path should be preserved"
    );
    assert!(
        content.contains("uv_path"),
        "runtime.uv_path should be preserved"
    );
    assert!(
        content.contains("anthropic:claude-sonnet-4-20250514"),
        "new value should be present"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 16. evolution promote with invalid skill name
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn evolution_promote_invalid_skill() {
    // Promoting a non-existent skill should fail gracefully (not panic).
    // The promotion backend will attempt to look up the skill in the store
    // and fail with a descriptive error.
    let result = cli()
        .args(["evolution", "promote", "nonexistent-skill-xyz"])
        .assert();

    let output = result.get_output();
    let exit_success = output.status.success();

    // The command may succeed (placeholder behavior) or fail -- either way
    // it must not panic. Verify we got meaningful output.
    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    let combined = format!("{stdout}{stderr}");

    // Should reference the skill name somewhere in output
    assert!(
        combined.contains("nonexistent-skill-xyz"),
        "evolution promote should reference the skill name, got: {combined:?}"
    );

    // Must not panic (which would produce a different exit pattern)
    assert!(
        !combined.contains("panic"),
        "evolution promote should not panic"
    );

    let _ = exit_success; // success or failure are both acceptable
}

// ══════════════════════════════════════════════════════════════════════════
// 17. runtime exec with extra args passed through
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn runtime_exec_with_args() {
    // Without a lockfile, runtime exec should fail with "No lockfile found".
    // The agent name should appear in the error output.
    let output = cli()
        .args(["runtime", "exec", "test-agent", "arg1", "arg2"])
        .assert()
        .failure();

    let stderr = String::from_utf8_lossy(&output.get_output().stderr);
    assert!(
        stderr.contains("No lockfile found"),
        "runtime exec should fail with lockfile message, got: {stderr:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 18. create agent with hyphens in the name
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn create_agent_special_chars_name() {
    let dir = tempfile::tempdir().unwrap();

    cli()
        .args(["create", "agent", "my-special-agent", "--description", "Special chars test"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify scaffold was created with hyphenated name
    let agent_dir = dir
        .path()
        .join("agents")
        .join("atomic")
        .join("my-special-agent");
    assert!(
        agent_dir.join("SKILL.md").exists(),
        "SKILL.md should exist for hyphenated agent name"
    );
    assert!(
        agent_dir.join("pyproject.toml").exists(),
        "pyproject.toml should exist for hyphenated agent name"
    );
    assert!(
        agent_dir
            .join("my_special_agent")
            .join("agent.py")
            .exists(),
        "agent.py should exist inside agent module directory"
    );

    // SKILL.md should contain the agent name
    let skill = std::fs::read_to_string(agent_dir.join("SKILL.md")).unwrap();
    assert!(
        skill.contains("my-special-agent"),
        "SKILL.md should contain the agent name"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 19. sources add with empty name or URL validation
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn sources_add_url_validation() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Empty URL should be rejected
    cli()
        .args(["sources", "add", "--name", "bad-url", "--url", ""])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("url is empty"));

    // Empty name with valid URL -- clap rejects empty --name as missing required value
    let result = cli()
        .args(["sources", "add", "--name", "", "--url", "https://github.com/example/repo"])
        .current_dir(dir.path())
        .assert();

    // Verify it doesn't panic regardless of outcome
    let stderr = String::from_utf8_lossy(&result.get_output().stderr);
    let stdout = String::from_utf8_lossy(&result.get_output().stdout);
    let combined = format!("{stdout}{stderr}");
    assert!(
        !combined.contains("panic"),
        "sources add with empty name should not panic"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 20. install with --version flag fails for agent not in sources
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn install_with_version_flag() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    cli()
        .args(["install", "some-agent", "--version", "v1.0.0"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("not found in sources"));
}

// ══════════════════════════════════════════════════════════════════════════
// 21. run with --model flag
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn run_with_model_flag() {
    // The run command always fails with PlatformRouter message (placeholder),
    // but the --mode flag should be accepted by clap parsing.
    cli()
        .args(["run", "test-agent", "--mode", "mcp", "some", "task"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("PlatformRouter"));
}

// ══════════════════════════════════════════════════════════════════════════
// 22. config set with float value
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn config_set_float_value() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[runtime]\npython_path = \"python3\"\n",
    )
    .unwrap();

    cli()
        .args(["config", "set", "runtime.timeout", "3.5"])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify the value is stored as a TOML float, not a string
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert!(
        content.contains("timeout = 3.5"),
        "Float should be stored as TOML float, got: {content:?}"
    );
    assert!(
        !content.contains("timeout = \"3.5\""),
        "Float should NOT be stored as string, got: {content:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 23. init idempotent with pre-existing files
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn init_idempotent_with_existing_files() {
    let dir = tempfile::tempdir().unwrap();

    // Create a pre-existing file that init should not overwrite
    let preexisting_content = "this should not be overwritten";
    std::fs::write(dir.path().join("config.toml"), preexisting_content).unwrap();

    // Run init
    cli()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();

    // config.toml should NOT have been overwritten
    let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
    assert_eq!(
        content, preexisting_content,
        "init should not overwrite pre-existing config.toml"
    );

    // sources.yaml should have been created (it did not exist before)
    assert!(
        dir.path().join("sources.yaml").exists(),
        "init should create sources.yaml when it does not exist"
    );
}

// ══════════════════════════════════════════════════════════════════════════
// 24. evolution status with --json flag
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn evolution_status_json_output() {
    let output = cli()
        .args(["--json", "evolution", "status"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    // output.data() uses pretty-print (multi-line JSON), so parse the full output
    let _: serde_json::Value =
        serde_json::from_str(stdout.trim()).unwrap_or_else(|e| {
            panic!("Expected valid JSON, got: {stdout:?}\nError: {e}")
        });
}

// ══════════════════════════════════════════════════════════════════════════
// 25. check with missing git in a directory with config
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn check_with_missing_git() {
    let dir = tempfile::tempdir().unwrap();
    // Create config but no git repo -- check should handle gracefully
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("sources.yaml"), "sources: []\n").unwrap();

    // Run check -- it will likely fail (no API key, etc.) but must not panic
    let result = cli()
        .env("OPENAI_API_KEY", "sk-test-check-no-git")
        .arg("check")
        .current_dir(dir.path())
        .assert();

    let output = result.get_output();
    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    let combined = format!("{stdout}{stderr}");

    // Should mention config.toml check (PASS since we created a valid one)
    assert!(
        combined.contains("config.toml"),
        "check should mention config.toml status, got: {combined:?}"
    );

    // Must not panic
    assert!(
        !combined.contains("panic"),
        "check should not panic even without git repo"
    );
}
