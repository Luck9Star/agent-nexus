//! `agent-nexus install/uninstall/update/list/search/info` — agent lifecycle commands.

use anyhow::{Context, Result};
use ap_fetcher::installer::GitInstaller;
use ap_fetcher::lockfile::LockfileManager;

use crate::commands;
use crate::output::OutputFormatter;

/// Run `install <agent>` command.
///
/// Looks up the agent in configured sources, clones via `GitInstaller`,
/// and records in `LockfileManager`. Supports `--source` for direct URL
/// and `--local` for local project installs.
pub fn run(
    agent: &str,
    version: Option<&str>,
    source_url: Option<&str>,
    local: bool,
    output: &OutputFormatter,
) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
    );

    if local {
        return run_install_local(agent, version, &root, output);
    }

    // Resolve source URL: explicit --source flag, or look up in sources.yaml
    let git_url = if let Some(url) = source_url {
        url.to_string()
    } else {
        let sources_path = root.join("sources.yaml");
        let sources = ap_fetcher::sources::SourceManager::new(sources_path).list();
        let source = sources
            .iter()
            .find(|s| s.name == agent)
            .with_context(|| {
                format!(
                    "Agent '{agent}' not found in sources. \
                     Run `agent-nexus sources list` to see available agents. \
                     Hint: Use --local to install from the local project directory."
                )
            })?;
        source.url.clone()
    };

    output.info(&format!("Installing '{agent}' ..."));

    let install_dir = root.join(".agents");
    let installer = GitInstaller::new(install_dir);

    let installed_path = installer
        .install(&git_url, Some("main"), version)
        .with_context(|| format!("Failed to install agent '{agent}'"))?;

    let agent_dir = installed_path.join(agent);
    if !agent_dir.exists() {
        anyhow::bail!("Agent '{agent}' not found in source repository");
    }

    // Resolve actual HEAD commit SHA for reproducible installs
    let commit_sha = git2::Repository::open(&installed_path)
        .ok()
        .and_then(|repo| {
            let head = repo.head().ok()?;
            head.target().map(|oid| oid.to_string())
        })
        .unwrap_or_else(|| "unknown".to_string());

    // Detect agent type from manifest if available
    let agent_type = detect_agent_type(&installed_path);

    let entry = ap_core::models::distribution::LockfileEntry {
        version: version.unwrap_or("latest").to_string(),
        source: git_url.clone(),
        commit_sha,
        agent_type,
        installed_at: chrono::Utc::now(),
        venv_path: format!(".venvs/{agent}"),
        dependencies: vec![],
    };

    let lockfile_path = root.join("lockfile.json");
    let lockfile_mgr = LockfileManager::new(lockfile_path);
    lockfile_mgr
        .add(agent, entry)
        .with_context(|| "Failed to update lockfile")?;

    output.success(&format!(
        "Installed {}@{}",
        agent,
        version.unwrap_or("latest")
    ));
    Ok(())
}

/// Install an agent from the local project `agents/` directory.
fn run_install_local(
    agent: &str,
    version: Option<&str>,
    root: &std::path::Path,
    output: &OutputFormatter,
) -> Result<()> {
    // Search both atomic and composite agent directories
    for subdir in &["atomic", "composite"] {
        let local_path = root.join("agents").join(subdir).join(agent);
        if local_path.is_dir() {
            let install_dir = root.join(".agents");
            // Copy the agent directory to .agents/
            let dest = install_dir.join(agent);
            if dest.exists() {
                std::fs::remove_dir_all(&dest)
                    .with_context(|| format!("Failed to remove existing install at {}", dest.display()))?;
            }
            copy_dir_recursive(&local_path, &dest)?;

            let agent_type = detect_agent_type(&dest);
            let entry = ap_core::models::distribution::LockfileEntry {
                version: version.unwrap_or("0.1.0").to_string(),
                source: "local".to_string(),
                commit_sha: "local".to_string(),
                agent_type,
                installed_at: chrono::Utc::now(),
                venv_path: format!(".venvs/{agent}"),
                dependencies: vec![],
            };

            let lockfile_path = root.join("lockfile.json");
            let lockfile_mgr = LockfileManager::new(lockfile_path);
            lockfile_mgr
                .add(agent, entry)
                .with_context(|| "Failed to update lockfile")?;

            output.success(&format!(
                "Installed {}@{} (local)",
                agent,
                version.unwrap_or("0.1.0")
            ));
            return Ok(());
        }
    }

    anyhow::bail!(
        "Agent '{}' not found locally. Searched:\n  {}\n  {}",
        agent,
        root.join("agents").join("atomic").join(agent).display(),
        root.join("agents").join("composite").join(agent).display()
    );
}

/// Run `uninstall <agent>` command.
pub fn run_uninstall(agent: &str, output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
    );

    let lockfile_path = root.join("lockfile.json");
    let lockfile_mgr = LockfileManager::new(lockfile_path);

    if lockfile_mgr.get(agent).context("Failed to read lockfile")?.is_some() {
        lockfile_mgr
            .remove(agent)
            .context("Failed to remove from lockfile")?;

        // Remove install directory if it exists
        let install_dir = root.join(".agents").join(agent);
        if install_dir.exists() {
            std::fs::remove_dir_all(&install_dir)
                .with_context(|| format!("Failed to remove install directory {}", install_dir.display()))?;
        }

        output.success(&format!("Uninstalled {agent}"));
        Ok(())
    } else {
        output.error(&format!("Agent '{agent}' is not installed."));
        Err(anyhow::anyhow!("Agent '{agent}' is not installed."))
    }
}

/// Run `update [agent]` or `update --all` command.
pub fn run_update(agent: Option<&str>, all: bool, output: &OutputFormatter) -> Result<()> {
    if !all && agent.is_none() {
        anyhow::bail!("Specify an agent name or use --all to update all agents.");
    }

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
    );

    let lockfile_path = root.join("lockfile.json");
    let lockfile_mgr = LockfileManager::new(lockfile_path.clone());

    let agents_to_update: Vec<String> = if all {
        // List all agents from lockfile
        lockfile_mgr
            .load()
            .map(|lf| lf.agents.into_keys().collect())
            .unwrap_or_default()
    } else {
        vec![agent.ok_or_else(|| anyhow::anyhow!("Agent name required when --all is not set"))?.to_string()]
    };

    if agents_to_update.is_empty() {
        output.info("No installed agents to update.");
        return Ok(());
    }

    let mut updated_count = 0usize;
    let total = agents_to_update.len();

    for agent_name in &agents_to_update {
        match do_update_agent(agent_name, &root) {
            Ok(new_version) => {
                output.success(&format!("Updated {agent_name}@{new_version}"));
                updated_count += 1;
            }
            Err(e) => {
                output.error(&format!("Error updating {agent_name}: {e}"));
            }
        }
    }

    output.info(&format!("Updated {updated_count}/{total} agent(s)."));
    if updated_count == 0 {
        return Err(anyhow::anyhow!("No agents were successfully updated"));
    }
    Ok(())
}

/// Update a single agent. Returns the new version string on success.
fn do_update_agent(agent: &str, root: &std::path::Path) -> Result<String> {
    let install_dir = root.join(".agents").join(agent);
    if !install_dir.exists() {
        anyhow::bail!("Agent '{agent}' is not installed.");
    }

    // Pull latest changes
    let repo = git2::Repository::open(&install_dir)
        .with_context(|| format!("Failed to open repo for '{agent}'"))?;

    // Fetch and reset to origin/main
    let remote_name = "origin";
    let mut remote = repo.find_remote(remote_name)?;
    remote.fetch(&["refs/heads/*:refs/remotes/origin/*"], None, None)?;

    let fetch_head = repo.find_reference("FETCH_HEAD")?;
    let fetch_commit = repo.reference_to_annotated_commit(&fetch_head)?;
    let analysis = repo.merge_analysis(&[&fetch_commit])?;

    if analysis.0.is_up_to_date() {
        // Already up to date
        let head = repo.head()?.target().map(|oid| oid.to_string()).unwrap_or_default();
        return Ok(head.get(..12).unwrap_or("latest").to_string());
    }

    // Fast-forward if possible
    if analysis.0.is_fast_forward() {
        // Determine the current branch dynamically instead of hardcoding "refs/heads/main"
        let head_ref = repo.head()?;
        let refname = head_ref.name().unwrap_or("refs/heads/main");
        repo.reference(refname, fetch_commit.id(), true, "Fast-forward")?;
        repo.set_head(refname)?;
        repo.checkout_head(Some(git2::build::CheckoutBuilder::default().force()))?;
    }

    let new_sha = repo
        .head()
        .ok()
        .and_then(|h| h.target().map(|oid| oid.to_string()))
        .unwrap_or_default();

    // Update lockfile entry
    let lockfile_path = root.join("lockfile.json");
    let lockfile_mgr = LockfileManager::new(lockfile_path);
    if let Some(mut entry) = lockfile_mgr.get(agent)? {
        entry.commit_sha = new_sha.clone();
        lockfile_mgr.add(agent, entry)?;
    }

    Ok(new_sha.get(..12).unwrap_or("latest").to_string())
}

/// Run `list` command -- list installed agents.
pub fn run_list(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
    );

    let lockfile_path = root.join("lockfile.json");
    if !lockfile_path.exists() {
        output.info("No agents installed.");
        return Ok(());
    }

    let lockfile_mgr = LockfileManager::new(lockfile_path);
    let entries: Vec<(String, ap_core::models::distribution::LockfileEntry)> = lockfile_mgr
        .load()
        .map(|lf| lf.agents.into_iter().collect())
        .unwrap_or_default();

    if entries.is_empty() {
        output.info("No agents installed.");
        return Ok(());
    }

    if output.is_json() {
        let result: Vec<serde_json::Value> = entries
            .iter()
            .map(|(name, entry)| {
                serde_json::json!({
                    "name": name,
                    "version": entry.version,
                    "type": format!("{:?}", entry.agent_type).to_lowercase(),
                    "source": entry.source,
                })
            })
            .collect();
        output.data(&result);
    } else {
        println!("{:<25} {:<12} {:<12} Source", "Name", "Version", "Type");
        println!("{}", "-".repeat(65));
        for (name, entry) in &entries {
            let agent_type = format!("{:?}", entry.agent_type).to_lowercase();
            println!("{:<25} {:<12} {:<12} {}", name, entry.version, agent_type, entry.source);
        }
        println!("\n{} agent(s) installed.", entries.len());
    }

    Ok(())
}

/// Run `search <query>` command -- search for available agents in sources.
pub fn run_search(query: &str, output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
    );

    let sources_path = root.join("sources.yaml");
    let mgr = ap_fetcher::sources::SourceManager::new(sources_path);
    let sources = mgr.list();

    let query_lower = query.to_lowercase();
    let mut results: Vec<(String, String, String, String)> = Vec::new();

    for source in &sources {
        // Search agents from the source by listing available agents
        // For now, perform a simple name match against the source name
        if source.name.to_lowercase().contains(&query_lower) {
            results.push((
                source.name.clone(),
                "unknown".to_string(),
                "unknown".to_string(),
                source.url.clone(),
            ));
        }
    }

    if output.is_json() {
        let json_results: Vec<serde_json::Value> = results
            .iter()
            .map(|(name, version, agent_type, source_url)| {
                serde_json::json!({
                    "name": name,
                    "version": version,
                    "type": agent_type,
                    "source": source_url,
                })
            })
            .collect();
        output.data(&json_results);
    } else if results.is_empty() {
        output.info(&format!("No agents found matching '{query}'."));
    } else {
        println!("Search results for '{query}':\n");
        for (name, _version, agent_type, source_url) in &results {
            println!("  {name} ({agent_type})");
            println!("    Source: {source_url}");
        }
        println!("\n{} result(s).", results.len());
    }

    Ok(())
}

/// Run `info <agent>` command -- show detailed information about an agent.
pub fn run_info(agent: &str, output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")),
    );

    let lockfile_path = root.join("lockfile.json");
    let lockfile_mgr = LockfileManager::new(lockfile_path);

    let entry = lockfile_mgr
        .get(agent)?
        .with_context(|| format!("Agent '{agent}' is not installed."))?;

    if output.is_json() {
        let obj = serde_json::json!({
            "name": agent,
            "version": entry.version,
            "type": format!("{:?}", entry.agent_type).to_lowercase(),
            "source": entry.source,
            "commit_sha": entry.commit_sha.get(..12).unwrap_or("unknown"),
            "installed_at": entry.installed_at.to_rfc3339(),
        });
        output.data(&obj);
    } else {
        let agent_type = format!("{:?}", entry.agent_type).to_lowercase();
        let short_sha = entry.commit_sha.get(..12).unwrap_or("unknown");
        println!("Agent: {agent}");
        println!("  Version:      {}", entry.version);
        println!("  Type:         {agent_type}");
        println!("  Source:       {}", entry.source);
        println!("  Commit SHA:   {short_sha}");
        println!("  Installed at: {}", entry.installed_at.to_rfc3339());
        if !entry.dependencies.is_empty() {
            println!("  Dependencies: {}", entry.dependencies.join(", "));
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Detect agent type from the installed directory structure.
fn detect_agent_type(install_path: &std::path::Path) -> ap_core::models::agent::AgentType {
    // If composition.toml exists, it's composite; otherwise atomic
    if install_path.join("composition.toml").exists() {
        ap_core::models::agent::AgentType::Composite
    } else {
        ap_core::models::agent::AgentType::Atomic
    }
}

/// Recursively copy a directory.
///
/// Symlinks are skipped to prevent symlink-following attacks (e.g. a malicious
/// agent symlinking to `/etc/passwd`). Only regular files and directories are copied.
fn copy_dir_recursive(src: &std::path::Path, dst: &std::path::Path) -> Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let file_type = std::fs::symlink_metadata(entry.path())?.file_type();
        // Skip symlinks to prevent following malicious links
        if file_type.is_symlink() {
            continue;
        }
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else {
            std::fs::copy(&src_path, &dst_path)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_agent_name() {
        assert!(commands::validate_fs_name("my-agent").is_ok());
        assert!(commands::validate_fs_name("my_agent").is_ok());
        assert!(commands::validate_fs_name("../../evil").is_err());
        assert!(commands::validate_fs_name("").is_err());
    }
}
