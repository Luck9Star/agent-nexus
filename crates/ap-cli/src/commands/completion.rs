//! Shell completion generation for agent-nexus CLI.

use clap::CommandFactory;
use clap_complete::{generate, Shell};

use crate::Cli;

/// Generate shell completion scripts.
///
/// Usage:
///   agent-nexus completion bash > /etc/bash_completion.d/agent-nexus
///   agent-nexus completion zsh > ~/.zfunc/_agent-nexus
///   agent-nexus completion fish > ~/.config/fish/completions/agent-nexus.fish
pub fn run(shell: Shell) -> anyhow::Result<()> {
    let mut cmd = Cli::command();
    let name = cmd.get_name().to_string();
    generate(shell, &mut cmd, name, &mut std::io::stdout());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn completion_bash_succeeds() {
        let mut buf = Vec::new();
        let mut cmd = Cli::command();
        let name = cmd.get_name().to_string();
        generate(Shell::Bash, &mut cmd, name, &mut buf);
        let output = String::from_utf8(buf).expect("bash completion should be valid UTF-8");
        assert!(
            output.contains("agent-nexus"),
            "bash completion should contain 'agent-nexus'"
        );
    }

    #[test]
    fn completion_zsh_succeeds() {
        let mut buf = Vec::new();
        let mut cmd = Cli::command();
        let name = cmd.get_name().to_string();
        generate(Shell::Zsh, &mut cmd, name, &mut buf);
        let output = String::from_utf8(buf).expect("zsh completion should be valid UTF-8");
        assert!(
            output.contains("agent-nexus"),
            "zsh completion should contain 'agent-nexus'"
        );
    }

    #[test]
    fn completion_fish_succeeds() {
        let mut buf = Vec::new();
        let mut cmd = Cli::command();
        let name = cmd.get_name().to_string();
        generate(Shell::Fish, &mut cmd, name, &mut buf);
        let output = String::from_utf8(buf).expect("fish completion should be valid UTF-8");
        assert!(
            output.contains("agent-nexus"),
            "fish completion should contain 'agent-nexus'"
        );
    }

    #[test]
    fn completion_bash_contains_subcommands() {
        let mut buf = Vec::new();
        let mut cmd = Cli::command();
        let name = cmd.get_name().to_string();
        generate(Shell::Bash, &mut cmd, name, &mut buf);
        let output = String::from_utf8(buf).expect("bash completion should be valid UTF-8");
        // Verify that the completion script references key subcommands
        assert!(
            output.contains("init"),
            "bash completion should reference 'init' subcommand"
        );
        assert!(
            output.contains("install"),
            "bash completion should reference 'install' subcommand"
        );
        assert!(
            output.contains("run"),
            "bash completion should reference 'run' subcommand"
        );
    }
}
