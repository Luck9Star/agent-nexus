//! `agent-nexus check` -- verify environment health.

use std::path::PathBuf;
use std::process::Command;

use anyhow::Result;

use crate::output::OutputFormatter;

/// Run `check` command.
///
/// Checks 7 items: python3 >= 3.11, config.toml, sources in config.toml, git, uv, API key, connectivity.
/// Prints [PASS]/[FAIL] status for each check. Returns error if any check fails.
pub fn run(output: &OutputFormatter) -> Result<()> {
    let mut passed = 0usize;
    let total = 7;
    let root = super::find_project_root(&std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));

    // Check 1: python3 >= 3.11
    if check_python_version(output) {
        output.success("[PASS] python3 >= 3.11");
        passed += 1;
    } else {
        output.error("[FAIL] python3 >= 3.11: not found or version < 3.11");
    }

    // Parse config.toml once (used by checks 2 and 3)
    let config_value = {
        let path = root.join("config.toml");
        if path.exists() {
            std::fs::read_to_string(&path).ok().and_then(|c| toml::from_str(&c).ok())
        } else {
            None
        }
    };

    // Check 2: config.toml valid
    if config_value.is_some() {
        output.success("[PASS] config.toml: valid");
        passed += 1;
    } else {
        output.error("[FAIL] config.toml: not found or invalid TOML (run `agent-nexus init` to create)");
    }

    // Check 3: sources in config.toml
    match check_sources(config_value.as_ref()) {
        Ok(()) => {
            output.success("[PASS] config.toml [sources]: has valid entries");
            passed += 1;
        }
        Err(e) => {
            output.error(&format!("[FAIL] sources: {e}"));
        }
    }

    // Check 4: git on PATH
    if check_command_exists("git") {
        output.success("[PASS] git: available");
        passed += 1;
    } else {
        output.error("[FAIL] git: not found on PATH");
    }

    // Check 5: uv on PATH
    if check_command_exists("uv") {
        output.success("[PASS] uv: available");
        passed += 1;
    } else {
        output.error("[FAIL] uv: not found on PATH");
    }

    // Check 6: API key configured
    if check_api_key() {
        output.success("[PASS] API key: at least one configured");
        passed += 1;
    } else {
        output.error("[FAIL] API key: none configured (set OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)");
    }

    // Check 7: Python connectivity (uv can find python)
    if check_command_exists("python3") {
        output.success("[PASS] python3: reachable");
        passed += 1;
    } else {
        output.error("[FAIL] python3: not reachable");
    }

    if passed == total {
        output.success(&format!("{passed}/{total} checks passed"));
        Ok(())
    } else {
        output.info(&format!("{passed}/{total} checks passed"));
        Err(anyhow::anyhow!("Some checks failed -- see messages above"))
    }
}

/// Check python3 is available and version >= 3.11.
fn check_python_version(_output: &OutputFormatter) -> bool {
    let Ok(output) = Command::new("python3").arg("--version").output() else {
        return false;
    };
    if !output.status.success() {
        return false;
    }
    let version_str = String::from_utf8_lossy(&output.stdout);
    parse_python_version(&version_str).is_some_and(|(major, minor)| major > 3 || (major == 3 && minor >= 11))
}

/// Parse "Python 3.x.y" into (major, minor).
fn parse_python_version(s: &str) -> Option<(u32, u32)> {
    // Handle "Python 3.12.0" or "Python 3.11.0rc1" etc.
    let s = s.trim();
    let version_part = s.strip_prefix("Python ")?;
    let mut parts = version_part.split('.');
    let major: u32 = parts.next()?.parse().ok()?;
    let minor: u32 = parts.next()?.split(|c: char| !c.is_ascii_digit()).next()?.parse().ok()?;
    Some((major, minor))
}

fn check_sources(config: Option<&toml::Value>) -> Result<()> {
    let config = match config {
        Some(c) => c,
        None => return Err(anyhow::anyhow!("config.toml not found or invalid (run `agent-nexus init` to create)")),
    };

    match config.get("sources").and_then(|s| s.as_array()) {
        Some(sources) if !sources.is_empty() => {
            let valid_count = sources.iter().filter(|s| {
                s.get("name").and_then(|n| n.as_str()).is_some_and(|n| !n.is_empty())
                    && s.get("url").and_then(|u| u.as_str()).is_some_and(|u| !u.is_empty())
            }).count();
            if valid_count > 0 {
                Ok(())
            } else {
                Err(anyhow::anyhow!("[[sources]] exists but no entry has both 'name' and 'url'"))
            }
        }
        Some(_) => Err(anyhow::anyhow!("[[sources]] is empty — add a source with `agent-nexus sources add`")),
        None => Err(anyhow::anyhow!("no [[sources]] section in config.toml (run `agent-nexus init` to add defaults)")),
    }
}

/// Check that a command exists on PATH.
///
/// Uses `which` on Unix and `where` on Windows for cross-platform support.
fn check_command_exists(cmd: &str) -> bool {
    let finder = if cfg!(windows) { "where" } else { "which" };
    Command::new(finder)
        .arg(cmd)
        .output()
        .is_ok_and(|o| o.status.success())
}

/// Check that at least one API key environment variable is set.
fn check_api_key() -> bool {
    ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "OLLAMA_HOST"]
        .iter()
        .any(|k| std::env::var(k).is_ok())
}

/// Run `check <path>` command -- validate an agent package at a specific path.
pub fn run_check_package(path: &str, output: &OutputFormatter) -> Result<()> {
    let pkg_path = PathBuf::from(path);
    if !pkg_path.exists() {
        anyhow::bail!("Path does not exist: {path}");
    }
    if !pkg_path.is_dir() {
        anyhow::bail!("Path is not a directory: {path}");
    }

    let mut passed = 0usize;
    let total = 4;

    // Check 1: SKILL.md exists
    if pkg_path.join("SKILL.md").exists() {
        output.success("[PASS] SKILL.md: present");
        passed += 1;
    } else {
        output.error("[FAIL] SKILL.md: missing (required for all agents)");
    }

    // Check 2: pyproject.toml or setup.py exists
    if pkg_path.join("pyproject.toml").exists() || pkg_path.join("setup.py").exists() {
        output.success("[PASS] Package manifest: present");
        passed += 1;
    } else {
        output.error("[FAIL] Package manifest: missing pyproject.toml or setup.py");
    }

    // Check 3: Python source directory exists
    let has_src = std::fs::read_dir(&pkg_path)?
        .filter_map(std::result::Result::ok)
        .any(|e| e.path().is_dir() && !e.file_name().to_string_lossy().ends_with(".py"));
    if has_src {
        output.success("[PASS] Source directory: present");
        passed += 1;
    } else {
        output.error("[FAIL] Source directory: no source directory found");
    }

    // Check 4: No obvious security issues (world-writable)
    let metadata = std::fs::metadata(&pkg_path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = metadata.permissions().mode();
        if mode & 0o002 != 0 {
            output.error("[FAIL] Security: package directory is world-writable");
        } else {
            output.success("[PASS] Security: package directory permissions OK");
            passed += 1;
        }
    }
    #[cfg(not(unix))]
    {
        output.success("[PASS] Security: platform permissions check skipped");
        passed += 1;
    }

    if passed == total {
        output.success(&format!("{passed}/{total} package checks passed"));
        Ok(())
    } else {
        output.info(&format!("{passed}/{total} package checks passed"));
        Err(anyhow::anyhow!("Some package checks failed"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_python_version_standard() {
        assert_eq!(parse_python_version("Python 3.12.0"), Some((3, 12)));
        assert_eq!(parse_python_version("Python 3.11.0rc1"), Some((3, 11)));
        assert_eq!(parse_python_version("Python 3.10.15"), Some((3, 10)));
    }

    #[test]
    fn parse_python_version_invalid() {
        assert_eq!(parse_python_version("not python"), None);
        assert_eq!(parse_python_version(""), None);
    }

    #[test]
    fn parse_python_version_v4() {
        assert_eq!(parse_python_version("Python 4.0.0"), Some((4, 0)));
    }
}
