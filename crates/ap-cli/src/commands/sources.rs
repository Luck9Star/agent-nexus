//! `agent-nexus sources` -- manage source repos (add/list/remove).
//!
//! Aligned with Python CLI: `sources add --name <name> --url <url> [--type <type>]`

use anyhow::Result;
use ap_core::models::distribution::SourceEntry;
use ap_fetcher::sources::SourceManager;

use crate::commands;
use crate::output::OutputFormatter;

fn sources_yaml_path() -> std::path::PathBuf {
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    commands::find_project_root(&cwd).join("sources.yaml")
}

/// Run `sources list` command.
#[allow(clippy::unnecessary_wraps)]
pub fn run_list(output: &OutputFormatter) -> Result<()> {
    let mgr = SourceManager::new(sources_yaml_path());
    let sources = mgr.list();

    if output.is_json() {
        output.data(&sources);
    } else if sources.is_empty() {
        output.info("No sources configured.");
    } else {
        println!("{:<20} {:<10} URL", "Name", "Type");
        println!("{}", "-".repeat(60));
        for src in &sources {
            println!("{:<20} {:<10} {}", src.name, src.source_type, src.url);
        }
    }

    Ok(())
}

/// Run `sources add --name <name> --url <url> [--type <type>] [--branch <branch>]` command.
pub fn run_add(name: &str, url: &str, source_type: Option<&str>, branch: Option<&str>, output: &OutputFormatter) -> Result<()> {
    let entry = SourceEntry {
        name: name.to_string(),
        source_type: source_type.unwrap_or("git").to_string(),
        url: url.to_string(),
        branch: branch.unwrap_or("main").to_string(),
    };

    entry
        .validate()
        .map_err(|e| anyhow::anyhow!(e))?;

    let mgr = SourceManager::new(sources_yaml_path());
    mgr.add(entry)?;

    output.success(&format!("Source '{name}' added."));
    Ok(())
}

/// Run `sources remove <name>` command.
pub fn run_remove(name: &str, output: &OutputFormatter) -> Result<()> {
    let mgr = SourceManager::new(sources_yaml_path());

    let sources = mgr.list();
    if !sources.iter().any(|s| s.name == name) {
        output.error(&format!("Source '{name}' not found."));
        return Err(anyhow::anyhow!("Source '{name}' not found."));
    }

    mgr.remove(name)?;
    output.success(&format!("Source '{name}' removed."));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_empty_sources() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        std::fs::write(&path, "sources: []\n").unwrap();

        let root = commands::find_project_root(dir.path());
        assert_eq!(root, dir.path());

        let mgr = SourceManager::new(root.join("sources.yaml"));
        let sources = mgr.list();
        assert!(sources.is_empty());
    }

    #[test]
    fn add_and_remove_source() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        std::fs::write(&path, "sources: []\n").unwrap();

        let mgr = SourceManager::new(path);

        let entry = SourceEntry {
            name: "test".to_string(),
            source_type: "git".to_string(),
            url: "https://github.com/example/repo".to_string(),
            branch: "main".to_string(),
        };
        mgr.add(entry).unwrap();

        let sources = mgr.list();
        assert_eq!(sources.len(), 1);
        assert_eq!(sources[0].name, "test");

        mgr.remove("test").unwrap();
        let sources = mgr.list();
        assert!(sources.is_empty());
    }
}
