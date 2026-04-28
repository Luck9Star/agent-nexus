//! Comprehensive E2E tests covering all CLI sub-subcommands.
//!
//! Covers: evolution (health/list/history/metrics/fix), runtime (start/stop/restart/status/logs),
//! install (update/list/uninstall/info/search), config (edit/validate/providers/path),
//! create (with --tools/--wizard/--output), check --package, doctor, sources (--branch).

use assert_cmd::Command;
use predicates::prelude::*;

fn cli() -> Command {
    Command::cargo_bin("agent-nexus").unwrap()
}

fn tmpdir_with_config() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[models.providers]\nopenai_base = \"https://api.openai.com/v1\"\n\n[[sources]]\nname = \"official\"\nurl = \"https://github.com/example/agents\"\n",
    )
    .unwrap();
    dir
}

// ══════════════════════════════════════════════════════════════════════════════
// Evolution subcommands
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn evolution_health_no_skill() {
    // health without a skill name should list all active skills
    cli()
        .args(["evolution", "health"])
        .assert()
        .success();
}

#[test]
fn evolution_health_with_verbose() {
    cli()
        .args(["evolution", "health", "-v"])
        .assert()
        .success();
}

#[test]
fn evolution_health_nonexistent_skill() {
    cli()
        .args(["evolution", "health", "nonexistent-skill-xyz"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("not found"));
}

#[test]
fn evolution_list_active_only() {
    cli()
        .args(["evolution", "list"])
        .assert()
        .success();
}

#[test]
fn evolution_list_all_flag() {
    cli()
        .args(["evolution", "list", "--all"])
        .assert()
        .success();
}

#[test]
fn evolution_list_json_output() {
    let output = cli()
        .args(["--json", "evolution", "list"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let _: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("Expected valid JSON, got: {stdout:?}\nError: {e}"));
}

#[test]
fn evolution_history_nonexistent_skill() {
    cli()
        .args(["evolution", "history", "nonexistent-skill-xyz"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("not found"));
}

#[test]
fn evolution_metrics_all() {
    let output = cli()
        .args(["evolution", "metrics"])
        .assert()
        .success();

    // Should produce some output
    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(!stdout.is_empty() || !output.get_output().stderr.is_empty());
}

#[test]
fn evolution_metrics_filter_by_agent() {
    cli()
        .args(["evolution", "metrics", "-a", "nonexistent"])
        .assert()
        .success();
}

#[test]
fn evolution_metrics_json_output() {
    let output = cli()
        .args(["--json", "evolution", "metrics"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let _: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("Expected valid JSON, got: {stdout:?}\nError: {e}"));
}

#[test]
fn evolution_fix_nonexistent_skill() {
    cli()
        .args(["evolution", "fix", "nonexistent-skill-id-xyz"])
        .assert()
        .failure();
}

// ══════════════════════════════════════════════════════════════════════════════
// Runtime subcommands
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn runtime_start_no_agent() {
    // start without agent or --all should fail or print help
    cli()
        .args(["runtime", "start"])
        .assert()
        .failure();
}

#[test]
fn runtime_start_nonexistent_agent() {
    cli()
        .args(["runtime", "start", "nonexistent-agent-xyz"])
        .assert()
        .failure();
}

#[test]
fn runtime_start_all_flag() {
    // --all without lockfile should fail gracefully (use temp dir for isolation)
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();
    cli()
        .args(["runtime", "start", "--all"])
        .current_dir(dir.path())
        .assert()
        .failure();
}

#[test]
fn runtime_stop_no_agent() {
    cli()
        .args(["runtime", "stop"])
        .assert()
        .failure();
}

#[test]
fn runtime_stop_nonexistent_agent() {
    // stop gracefully reports agent not running with exit 0
    cli()
        .args(["runtime", "stop", "nonexistent-agent-xyz"])
        .assert()
        .success()
        .stdout(predicate::str::contains("not running"));
}

#[test]
fn runtime_stop_all_flag() {
    // --all with no running agents should succeed (nothing to stop)
    cli()
        .args(["runtime", "stop", "--all"])
        .assert()
        .success();
}

#[test]
fn runtime_restart_nonexistent_agent() {
    cli()
        .args(["runtime", "restart", "nonexistent-agent-xyz"])
        .assert()
        .failure();
}

#[test]
fn runtime_status_empty() {
    // status with no running agents should succeed
    cli()
        .args(["runtime", "status"])
        .assert()
        .success();
}

#[test]
fn runtime_status_json_output() {
    let output = cli()
        .args(["--json", "runtime", "status"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let _: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("Expected valid JSON, got: {stdout:?}\nError: {e}"));
}

#[test]
fn runtime_logs_nonexistent_agent() {
    // logs gracefully reports missing log file with exit 0
    cli()
        .args(["runtime", "logs", "nonexistent-agent-xyz"])
        .assert()
        .success()
        .stdout(predicate::str::contains("No log file found"));
}

#[test]
fn runtime_logs_with_lines_flag() {
    // logs gracefully reports missing log file with exit 0
    cli()
        .args(["runtime", "logs", "nonexistent-agent-xyz", "--lines", "10"])
        .assert()
        .success()
        .stdout(predicate::str::contains("No log file found"));
}

// ══════════════════════════════════════════════════════════════════════════════
// Install and related top-level commands (list, update, uninstall, info, search)
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn list_empty() {
    // list with no lockfile should succeed (empty list)
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["list"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn list_json_output() {
    let dir = tempfile::tempdir().unwrap();
    let output = cli()
        .args(["--json", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let _: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("Expected valid JSON, got: {stdout:?}\nError: {e}"));
}

#[test]
fn update_nonexistent_agent() {
    cli()
        .args(["update", "nonexistent-agent-xyz"])
        .assert()
        .failure();
}

#[test]
fn update_all_flag() {
    // --all with no installed agents should succeed
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["update", "--all"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn uninstall_nonexistent_agent() {
    cli()
        .args(["uninstall", "nonexistent-agent-xyz"])
        .assert()
        .failure();
}

#[test]
fn info_nonexistent_agent() {
    cli()
        .args(["info", "nonexistent-agent-xyz"])
        .assert()
        .failure();
}

#[test]
fn search_query() {
    // search should succeed even with no results
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    cli()
        .args(["search", "test-query"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn install_agent_url_validation() {
    // Install with invalid URL should fail
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["install", "test-agent", "--source", "not-a-url"])
        .current_dir(dir.path())
        .assert()
        .failure();
}

// ══════════════════════════════════════════════════════════════════════════════
// Config subcommands: edit, validate, providers, path
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn config_validate_valid_file() {
    let dir = tmpdir_with_config();
    cli()
        .args(["config", "validate"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn config_validate_invalid_file() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "not valid toml [[[[").unwrap();

    cli()
        .args(["config", "validate"])
        .current_dir(dir.path())
        .assert()
        .failure();
}

#[test]
fn config_validate_missing_file() {
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["config", "validate"])
        .current_dir(dir.path())
        .assert()
        .failure();
}

#[test]
fn config_providers_with_keys() {
    let dir = tmpdir_with_config();
    cli()
        .env("OPENAI_API_KEY", "sk-test-key-for-providers-test")
        .args(["config", "providers"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn config_providers_json_output() {
    let dir = tmpdir_with_config();
    let output = cli()
        .env("OPENAI_API_KEY", "sk-test-key")
        .args(["--json", "config", "providers"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let _: serde_json::Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("Expected valid JSON, got: {stdout:?}\nError: {e}"));
}

#[test]
fn config_path_command() {
    let dir = tmpdir_with_config();
    let output = cli()
        .args(["config", "path"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    // Should print a path
    assert!(!stdout.trim().is_empty(), "config path should output something");
}

// ══════════════════════════════════════════════════════════════════════════════
// Create subcommands: --tools, --output flags
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn create_agent_with_pipeline_tools() {
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args([
            "create", "agent", "pipeline-agent",
            "--description", "A pipeline agent",
            "--tools", "pipeline",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    // Verify pipeline agent has 3 tool files
    let agent_dir = dir.path().join("agents").join("atomic").join("pipeline-agent");
    assert!(agent_dir.join("SKILL.md").exists());

    let skill = std::fs::read_to_string(agent_dir.join("SKILL.md")).unwrap();
    assert!(
        skill.contains("analyze") && skill.contains("execute") && skill.contains("report"),
        "Pipeline tools should list analyze/execute/report"
    );
}

#[test]
fn create_agent_with_output_dir() {
    let dir = tempfile::tempdir().unwrap();
    let output_path = dir.path().join("custom-output");

    cli()
        .args([
            "create", "agent", "output-dir-agent",
            "--description", "Custom output dir test",
            "--output", output_path.to_str().unwrap(),
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    assert!(
        output_path.join("output-dir-agent").join("SKILL.md").exists(),
        "Agent should be created in custom output directory"
    );
}

#[test]
fn create_agent_without_description_fails() {
    let dir = tempfile::tempdir().unwrap();
    cli()
        .args(["create", "agent", "no-desc-agent"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("description"));
}

// ══════════════════════════════════════════════════════════════════════════════
// Check --package subcommand
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn check_package_nonexistent_path() {
    cli()
        .args(["check", "/nonexistent/path/to/agent"])
        .assert()
        .failure();
}

#[test]
fn check_package_valid_agent() {
    // Create a minimal valid agent package
    let dir = tempfile::tempdir().unwrap();
    let agent_dir = dir.path().join("test-agent");
    let pkg_dir = agent_dir.join("test_agent");
    std::fs::create_dir_all(&pkg_dir).unwrap();
    std::fs::write(agent_dir.join("SKILL.md"), "# Test Agent\nA test.").unwrap();
    std::fs::write(agent_dir.join("pyproject.toml"), "[project]\nname = \"test-agent\"\n").unwrap();
    std::fs::write(pkg_dir.join("__init__.py"), "").unwrap();

    cli()
        .args(["check", agent_dir.to_str().unwrap()])
        .assert()
        .success();
}

#[test]
fn check_package_missing_skill_md() {
    let dir = tempfile::tempdir().unwrap();
    let agent_dir = dir.path().join("incomplete-agent");
    std::fs::create_dir_all(&agent_dir).unwrap();
    std::fs::write(agent_dir.join("pyproject.toml"), "[project]\nname = \"test\"\n").unwrap();
    // No SKILL.md

    cli()
        .args(["check", agent_dir.to_str().unwrap()])
        .assert()
        .failure();
}

// ══════════════════════════════════════════════════════════════════════════════
// Doctor command
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn doctor_command() {
    let dir = tmpdir_with_config();
    cli()
        .env("OPENAI_API_KEY", "sk-test-key-for-doctor-test")
        .args(["doctor"])
        .current_dir(dir.path())
        .assert()
        .success();
}

#[test]
fn doctor_json_output() {
    let dir = tmpdir_with_config();
    let output = cli()
        .env("OPENAI_API_KEY", "sk-test-key-for-doctor-test")
        .args(["--json", "doctor"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    // Doctor may output multiple JSON lines
    for line in stdout.lines() {
        if !line.trim().is_empty() {
            let _: serde_json::Value = serde_json::from_str(line)
                .unwrap_or_else(|e| panic!("Expected valid JSON line, got: {line:?}\nError: {e}"));
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// Sources --branch flag (extended)
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn sources_add_with_branch_and_list_json() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    cli()
        .args([
            "sources", "add",
            "--name", "branch-test",
            "--url", "https://github.com/example/repo",
            "--branch", "feature-branch",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    let output = cli()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout).expect("Expected valid JSON");
    let sources = parsed.as_array().expect("Expected JSON array");
    assert_eq!(sources.len(), 1);
    assert_eq!(sources[0]["branch"], "feature-branch");
}

#[test]
fn sources_add_without_branch_defaults_main() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    cli()
        .args([
            "sources", "add",
            "--name", "no-branch",
            "--url", "https://github.com/example/repo",
        ])
        .current_dir(dir.path())
        .assert()
        .success();

    let output = cli()
        .args(["--json", "sources", "list"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout).expect("Expected valid JSON");
    let sources = parsed.as_array().expect("Expected JSON array");
    // branch defaults to "main" when not specified
    assert_eq!(sources[0]["branch"], "main");
}

// ══════════════════════════════════════════════════════════════════════════════
// Version subcommand (explicit)
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn version_subcommand_matches_flag() {
    let flag_output = cli()
        .arg("--version")
        .assert()
        .success();

    let subcmd_output = cli()
        .args(["version"])
        .assert()
        .success();

    let flag_ver = std::str::from_utf8(&flag_output.get_output().stdout).unwrap().trim();
    let subcmd_ver = std::str::from_utf8(&subcmd_output.get_output().stdout).unwrap().trim();
    assert_eq!(flag_ver, subcmd_ver, "version flag and subcommand should match");
}

// ══════════════════════════════════════════════════════════════════════════════
// Install agent with --local flag
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn install_local_nonexistent_path() {
    cli()
        .args(["install", "nonexistent-agent", "--local"])
        .assert()
        .failure();
}

// ══════════════════════════════════════════════════════════════════════════════
// Run with transport flag validation
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn run_with_invalid_transport() {
    let dir = tmpdir_with_config();
    cli()
        .args(["run", "test-agent", "--transport", "invalid-transport"])
        .current_dir(dir.path())
        .assert()
        .failure();
}

#[test]
fn run_with_invalid_mode() {
    let dir = tmpdir_with_config();
    cli()
        .args(["run", "test-agent", "--mode", "invalid-mode"])
        .current_dir(dir.path())
        .assert()
        .failure();
}

// ══════════════════════════════════════════════════════════════════════════════
// Runtime logs --follow with real log file (GAP-4 verification)
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn runtime_logs_shows_content() {
    let dir = tempfile::tempdir().unwrap();
    let agents_dir = dir.path().join(".agents").join("log-test-agent");
    std::fs::create_dir_all(&agents_dir).unwrap();
    std::fs::write(
        agents_dir.join("agent.log"),
        "line 1\nline 2\nline 3\nline 4\nline 5\n",
    )
    .unwrap();

    let output = cli()
        .args(["runtime", "logs", "log-test-agent"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(stdout.contains("line 1"), "Should show log content");
    assert!(stdout.contains("line 5"), "Should show last log line");
}

#[test]
fn runtime_logs_lines_flag_limits_output() {
    let dir = tempfile::tempdir().unwrap();
    let agents_dir = dir.path().join(".agents").join("lines-test-agent");
    std::fs::create_dir_all(&agents_dir).unwrap();
    std::fs::write(
        agents_dir.join("agent.log"),
        "line 1\nline 2\nline 3\nline 4\nline 5\n",
    )
    .unwrap();

    let output = cli()
        .args(["runtime", "logs", "lines-test-agent", "--lines", "2"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    assert!(!stdout.contains("line 1"), "Should not show old lines");
    assert!(!stdout.contains("line 2"), "Should not show old lines");
    assert!(!stdout.contains("line 3"), "Should not show old lines");
    assert!(stdout.contains("line 4"), "Should show tail line 4");
    assert!(stdout.contains("line 5"), "Should show tail line 5");
}

#[test]
fn runtime_logs_follow_outputs_initial_content() {
    // Test that --follow mode at least outputs the initial lines before blocking.
    // Use a timeout to prevent the test from hanging indefinitely.
    let dir = tempfile::tempdir().unwrap();
    let agents_dir = dir.path().join(".agents").join("follow-agent");
    std::fs::create_dir_all(&agents_dir).unwrap();
    std::fs::write(
        agents_dir.join("agent.log"),
        "initial line 1\ninitial line 2\n",
    )
    .unwrap();

    // --follow will block; use assert_cmd timeout to kill it after 2s
    let result = cli()
        .args(["runtime", "logs", "follow-agent", "--follow"])
        .current_dir(dir.path())
        .timeout(std::time::Duration::from_secs(2))
        .assert();

    let output = result.get_output();
    // The process was killed by timeout, so exit code may be non-zero — that's OK.
    // What matters is that it printed the initial lines before blocking.
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("initial line 1"),
        "Follow mode should show initial content, got: {stdout:?}"
    );
    assert!(
        stdout.contains("initial line 2"),
        "Follow mode should show initial content, got: {stdout:?}"
    );
}

// ══════════════════════════════════════════════════════════════════════════════
// Search --json output validation (GAP-2 verification)
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn search_json_output_empty_sources() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("config.toml"), "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    let output = cli()
        .args(["--json", "search", "anything"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("search --json should produce valid JSON");
    assert!(
        parsed.is_array(),
        "search --json should produce a JSON array"
    );
    assert!(
        parsed.as_array().unwrap().is_empty(),
        "search with no sources should return empty array"
    );
}

#[test]
fn search_json_output_with_matching_source() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.toml"),
        "[models]\ndefault = \"openai:gpt-4o\"\n\n[[sources]]\nname = \"my-tools\"\ntype = \"git\"\nurl = \"https://github.com/example/tools\"\nbranch = \"main\"\n",
    )
    .unwrap();

    let output = cli()
        .args(["--json", "search", "my-tools"])
        .current_dir(dir.path())
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value =
        serde_json::from_str(stdout).expect("search --json should produce valid JSON");
    let results = parsed.as_array().expect("search should return JSON array");
    assert_eq!(results.len(), 1, "Should find one matching source");
    assert_eq!(results[0]["name"], "my-tools");
    // Verify the JSON has the expected fields from run_search
    assert!(results[0].get("version").is_some(), "Result should have 'version'");
    assert!(results[0].get("type").is_some(), "Result should have 'type'");
    assert!(results[0].get("source").is_some(), "Result should have 'source'");
}

// ══════════════════════════════════════════════════════════════════════════════
// Info --json with nonexistent agent (GAP-3 verification)
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn info_json_nonexistent_agent() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("lockfile.json"), "{}").unwrap();

    cli()
        .args(["--json", "info", "nonexistent-agent"])
        .current_dir(dir.path())
        .assert()
        .failure()
        .stderr(predicate::str::contains("not installed"));
}

// ══════════════════════════════════════════════════════════════════════════════
// Global --json flag across various subcommands
// ══════════════════════════════════════════════════════════════════════════════

#[test]
fn evolution_status_json_fields() {
    let output = cli()
        .args(["--json", "evolution", "status"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout.trim())
        .expect("Expected valid JSON");

    assert!(parsed.get("status").is_some(), "JSON should have 'status' field");
    assert!(parsed.get("health_score").is_some(), "JSON should have 'health_score' field");
    assert!(parsed.get("skill_count").is_some(), "JSON should have 'skill_count' field");
}

#[test]
fn env_json_has_expected_fields() {
    let output = cli()
        .args(["--json", "env"])
        .assert()
        .success();

    let stdout = std::str::from_utf8(&output.get_output().stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(stdout.trim())
        .expect("Expected valid JSON");

    // env output should have some structured fields
    assert!(parsed.is_object(), "env JSON output should be an object");
}
