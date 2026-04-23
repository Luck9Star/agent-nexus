//! `agent-nexus install <agent>` — install an agent via GitInstaller + LockfileManager.

use anyhow::{Context, Result};
use ap_fetcher::installer::GitInstaller;
use ap_fetcher::lockfile::LockfileManager;

use crate::commands;
use crate::output::OutputFormatter;

/// Run `install <agent>` command.
///
/// Looks up the agent in configured sources, clones via GitInstaller,
/// and records in LockfileManager.
pub fn run(agent: &str, version: Option<&str>, output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from(".")));

    // Load sources to find the URL for the requested agent
    let sources_path = root.join("sources.yaml");
    let sources = ap_fetcher::sources::SourceManager::new(sources_path).list();

    let source = sources
        .iter()
        .find(|s| s.name == agent)
        .with_context(|| {
            format!(
                "Agent '{}' not found in sources. Run `agent-nexus sources list` to see available agents.",
                agent
            )
        })?;

    output.info(&format!("Installing '{}' from {} ...", agent, source.url));

    let install_dir = root.join(".agents");
    let installer = GitInstaller::new(install_dir);

    let installed_path = installer
        .install(
            &source.url,
            Some(&source.branch),
            version,
        )
        .with_context(|| format!("Failed to install agent '{}' from {}", agent, source.url))?;

    output.success(&format!(
        "Installed '{}' -> {}",
        agent,
        installed_path.display()
    ));

    // Update lockfile
    let lockfile_path = root.join("lockfile.json");
    let mut lockfile_mgr = LockfileManager::new(lockfile_path);

    // Resolve actual HEAD commit SHA for reproducible installs
    let commit_sha = git2::Repository::open(&installed_path)
        .ok()
        .and_then(|repo| {
            let head = repo.head().ok()?;
            head.target().map(|oid| oid.to_string())
        })
        .unwrap_or_else(|| "unknown".to_string());

    let entry = ap_core::models::distribution::LockfileEntry {
        version: version.unwrap_or("latest").to_string(),
        source: agent.to_string(),
        commit_sha,
        agent_type: ap_core::models::agent::AgentType::Atomic,
        installed_at: chrono::Utc::now(),
        venv_path: format!(".venvs/{}", agent),
        dependencies: vec![],
    };

    lockfile_mgr
        .add(agent, entry)
        .with_context(|| "Failed to update lockfile")?;

    output.info("Lockfile updated.");
    Ok(())
}
