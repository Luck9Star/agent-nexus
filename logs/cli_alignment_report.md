# CLI Alignment Report: Python vs Rust

Generated: 2026-04-23

## Summary

| Metric | Count |
|--------|-------|
| Total Python commands (top-level + subcommands) | 31 |
| Total Rust commands (top-level + subcommands) | 18 |
| Fully implemented in Rust | 13 |
| Stub / placeholder in Rust | 1 |
| Missing from Rust entirely | 13 |

Breakdown:
- Python has **8 top-level commands** + **23 subcommands** across 6 groups.
- Rust has **11 top-level commands** + **7 subcommands** across 5 groups.
- Rust `run` is a stub that always returns an error.
- Several Python subcommand groups (runtime lifecycle, evolution diagnostics) are partially or entirely absent from Rust.

---

## Command-by-Command Comparison

### Top-Level Commands

| Python Command | Rust Status | Implementation Detail | Notes |
|---|---|---|---|
| `install` | Implemented | Full implementation via `ap_fetcher::GitInstaller` + `LockfileManager`. Resolves commit SHA, writes lockfile. | Python also supports `--source/-s` (direct Git URL) and `--local/-l` (local agents/ directory). **Rust missing `--source` and `--local` flags.** |
| `uninstall` | Missing | Not present in Rust clap enum. | Python removes agent via `GitInstaller.uninstall()`. |
| `update` | Missing | Not present in Rust clap enum. | Python supports `--all` flag for batch updates with concurrent git operations. |
| `run` | Stub | Returns `bail!("Agent execution requires PlatformRouter integration...")`. | Python supports `--mode/-m` (mcp/router/cli), `--transport/-t` (stdio/sse), extra args forwarding. Rust has `--model/-m` flag and trailing task args but **does nothing**. |
| `list` | Missing | Not present in Rust clap enum. | Python displays installed agents table with `--json` support. |
| `search` | Missing | Not present in Rust clap enum. | Python queries `SourceManager.search_agents()` with `--json` support. |
| `info` | Missing | Not present in Rust clap enum. | Python reads lockfile entry + manifest + SKILL.md preview. |
| `check` | Implemented | Full environment health check: python3 version, config.toml, sources.yaml, git, uv, API key, python3 reachable. Returns PASS/FAIL per check. | Python `check` takes a `<path>` argument for agent package validation (manifest, SKILL.md, pyproject.toml, composition.toml DAG). **Rust `check` is environment diagnostics -- different semantics entirely.** |
| `--version/-v` (flag) | Implemented | `Commands::Version` prints `agent-nexus <CARGO_PKG_VERSION>`. | Python uses `--version/-v` as a top-level flag via Typer callback. Rust uses a separate `version` subcommand. |

### Subcommand Groups

#### init

| Python Subcommand | Rust Equivalent | Rust Status | Notes |
|---|---|---|---|
| `init` | `Init` | Implemented | Python has `--wizard/-w` for interactive setup (questionary-based). Rust has `--dir/-d` (default "."). Rust creates config.toml + sources.yaml, detects API keys. **Missing: wizard mode, config migration, .env loading.** |
| `version` | `Version` | Implemented | Python reads via `importlib.metadata`. Rust uses `env!("CARGO_PKG_VERSION")`. |
| `doctor` | `Check` | Implemented (different name) | Python `doctor` checks: config.toml, API key, git, uv, Python >= 3.11, config dir writable, Evolution DB. Rust `check` checks: python3 >= 3.11, config.toml, sources.yaml, git, uv, API key, python3 reachable. **Functionally similar but named differently.** |
| `env` | `Env` | Implemented | Python shows: config dir, Python version, git, uv, providers with key status. Rust shows: config dir, python version, git version, uv version, provider names. **Rust missing per-provider API key status display.** |

#### config

| Python Subcommand | Rust Equivalent | Rust Status | Notes |
|---|---|---|---|
| `show` | `Config::Show` | Implemented | Python: config dir, default model, python path, uv path, providers. Supports `--json`. Rust: config dir, default model, python path, uv path, providers. Supports `--json`. **Good alignment.** |
| `get` | `Config::Get` | Implemented | Both take dot-path key (e.g. `models.default`). Rust has JSON output. |
| `set` | `Config::Set` | Implemented | Both take dot-path key + value. Rust does atomic write (write to tmp, rename). Preserves TOML types (bool, int, float, string). |
| `edit` | Missing | Not present in Rust. | Python opens `$EDITOR` on config.toml. |
| `validate` | Missing | Not present in Rust. | Python loads config via `ConfigLoader` + checks schema version migration. |
| `providers` | Missing | Not present in Rust. | Python lists providers with base URL, API key env var, and key status. |
| `path` | Missing | Not present in Rust. | Python prints config directory path. |

#### runtime

| Python Subcommand | Rust Equivalent | Rust Status | Notes |
|---|---|---|---|
| `start` | Missing | Not present in Rust. | Python starts agent subprocess, writes PID file. Supports `--all` flag. |
| `stop` | Missing | Not present in Rust. | Python stops agent via supervisor, cleans PID file. Supports `--all` flag. |
| `restart` | Missing | Not present in Rust. | Python does stop + start with PID management. |
| `status` | Missing | Not present in Rust. | Python reads lockfile + checks PID files + `os.kill(pid, 0)` to detect running agents. |
| `logs` | Missing | Not present in Rust. | Python shows last N lines with `--lines/-n` and `--follow/-f` (tail -f style). |
| `ps` | Missing | Not present in Rust. | Python alias for `status`. |
| (no equivalent) | `Runtime::Exec` | Implemented (different concept) | Rust `runtime exec <agent>` spawns agent subprocess + IPC (JSON-lines) via `ap_runtime::AgentProcess`. **No Python equivalent** -- this is a new Rust-only command for direct agent execution via IPC protocol. |

#### evolution

| Python Subcommand | Rust Equivalent | Rust Status | Notes |
|---|---|---|---|
| `status` | `Evolution::Status` | Implemented | Python: total skills, healthy/unhealthy counts, suggestions count (from health checker). Rust: health score (float), skill count (from `ap_evolution::EvolutionEngine`). **Output differs in detail.** |
| `health` | Missing | Not present in Rust. | Python per-skill or all-skills health diagnostics with applied/completion/fallback rates. |
| `list` | Missing | Not present in Rust. | Python lists skills with version, generation, status, created date. Supports `--all` flag. |
| `history` | Missing | Not present in Rust. | Python traces skill ancestry via `store.get_ancestry()` with indented lineage display. |
| `metrics` | Missing | Not present in Rust. | Python shows selection/applied/completion/fallback counts and rates. Optional `--agent/-a` filter. |
| `fix` | Missing | Not present in Rust. | Python triggers `EvolutionTrigger::METRIC_CHECK` evolution cycle. |
| `promote` | `Evolution::Promote` | Implemented | Python: promotes via `PromotionCandidate` (bypasses quality gates in CLI). Rust: promotes via `ap_evolution::promotion::promote_skill()` creating agent scaffolding. **Both functional, different internal paths.** |

#### create

| Python Subcommand | Rust Equivalent | Rust Status | Notes |
|---|---|---|---|
| `agent` | `Create::Agent` | Implemented (simpler) | Python: full scaffold with `--description/-d`, `--tools/-t` (simple/pipeline), `--wizard/-w`, `--output/-o`. Generates 7 files: manifest YAML, agent.py, SKILL.md, pyproject.toml, `__init__.py`, `pkg/agent.py`, `pkg/mcp_adapter.py`. **Rust generates 4 files: SKILL.md, `__init__.py`, `main.py`, pyproject.toml. Missing: agent-manifest.yaml, agent.py top-level, mcp_adapter.py, wizard mode.** |

#### sources

| Python Subcommand | Rust Equivalent | Rust Status | Notes |
|---|---|---|---|
| `list` | `Sources::List` | Implemented | Both list configured sources. Python shows name/type/URL table. Rust shows name/type/URL with JSON support. |
| `add` | `Sources::Add` | Implemented | Python: `--name`, `--url`, `--type`. Rust: positional `name`, `url`, `--branch/-b` (default "main"). **Different arg styles.** |
| `remove` | `Sources::Remove` | Implemented | Both remove by name. Python uses `typer.Argument`, Rust uses positional arg. |

---

## Global Flags Comparison

| Flag | Python | Rust | Notes |
|------|--------|------|-------|
| `--version` / `-v` | Top-level flag (Typer callback) | Separate `version` subcommand | Different UX pattern. |
| `--json` | Per-command option on `list`, `search`, `config show` | Global flag on `Cli` struct | Rust applies globally; Python applies per-command. |
| `--follow` | Not global; only on `runtime logs -f` | Global flag on `Cli` struct | Rust defines globally but does not use it in current implementations. |

---

## Argument/Flag Gaps Per Command

### install
| Feature | Python | Rust |
|---------|--------|------|
| Agent name | positional arg | positional arg |
| `--version/-v` | yes | yes |
| `--source/-s` (direct URL) | yes | **missing** |
| `--local/-l` (local install) | yes | **missing** |

### run
| Feature | Python | Rust |
|---------|--------|------|
| Agent name | positional arg | positional arg |
| `--mode/-m` (mcp/router/cli) | yes | **missing** (Rust has `--model/-m` instead) |
| `--transport/-t` (stdio/sse) | yes | **missing** |
| `--model/-m` | no | yes (different meaning than Python's `--mode`) |
| Extra args forwarding | yes (`context_settings`) | yes (`trailing_var_arg`) |
| Actual execution | Full (3 modes) | **Stub -- always errors** |

### sources add
| Feature | Python | Rust |
|---------|--------|------|
| Source name | `--name` (option) | positional arg |
| Source URL | `--url` (option, required) | positional arg |
| Source type | `--type` (option) | **missing** (hardcoded "git") |
| Branch | no | `--branch/-b` (default "main") |

### create agent
| Feature | Python | Rust |
|---------|--------|------|
| Agent name | positional arg | positional arg |
| `--description/-d` | yes | **missing** (hardcoded "Agent {name}") |
| `--tools/-t` (simple/pipeline) | yes | **missing** |
| `--wizard/-w` | yes | **missing** |
| `--output/-o` | yes | **missing** |

---

## Priority Recommendations

### Critical -- commands needed for basic workflow parity

1. **`uninstall`** -- Required for agent lifecycle management. Users must be able to remove installed agents. Implementation: read lockfile, remove agent directory, update lockfile.

2. **`update`** -- Required for keeping agents current. Python supports `--all` for batch updates. Implementation: re-clone or git pull agent repos, update lockfile entries.

3. **`list`** -- Essential for users to see what is installed. Implementation: read lockfile, display table of installed agents.

4. **`run` (make functional)** -- Currently a stub. Must connect to `ap_runtime::AgentProcess` for subprocess spawning + IPC. The `runtime exec` command already has this logic -- consider merging or distinguishing the two.

5. **`search`** -- Required for agent discovery from configured sources. Implementation: query `SourceManager` with search term.

### High -- commands that significantly improve usability

6. **`info`** -- Detailed agent information display. Implementation: read lockfile entry + manifest + SKILL.md preview.

7. **`config edit`** -- Opens config in `$EDITOR`. Simple to implement via `std::process::Command`.

8. **`config validate`** -- Validates config.toml structure. Can leverage existing TOML parsing.

9. **`config providers`** -- Lists providers with API key status. Implementation: read config, check env vars per provider.

10. **`install --source` and `--local` flags** -- Direct URL install and local path install. `--source` is common for quick installs without configuring a named source.

### Medium -- nice-to-have for power users

11. **`runtime start/stop/restart/status/logs/ps`** -- Full runtime lifecycle management. Rust already has `runtime exec` for IPC-based execution, but lacks the daemon-style start/stop with PID files that Python has. May want to reconsider the design -- Rust's subprocess model via `ap_runtime` may not need long-running daemon management.

12. **`evolution health/list/history/metrics/fix`** -- Detailed evolution diagnostics. The Rust `ap_evolution` crate has the store and engine; these are primarily query/display commands.

13. **`create agent` enhancements** -- Add `--description`, `--tools`, `--output` flags and generate the full Python scaffold (agent-manifest.yaml, mcp_adapter.py, etc.) to match Python's output.

14. **`init --wizard`** -- Interactive setup mode. Requires a TUI library (e.g. dialoguer, inquire).

15. **`config path`** -- Trivial: print config directory path. One-liner.

### Low -- can defer indefinitely

16. **`runtime ps`** -- Alias for `runtime status`. Only relevant if Rust implements the runtime lifecycle group.

17. **`--follow` global flag usage** -- Currently declared globally but unused. Either wire it up (e.g. for `runtime logs`) or remove it to avoid confusion.

18. **`check` semantics alignment** -- Python `check` validates an agent package at a given path; Rust `check` runs environment diagnostics. These are different commands sharing the same name. Consider renaming one (e.g. Rust `doctor` for environment checks, Rust `check <path>` for agent validation).

---

## Architectural Observations

1. **`run` vs `runtime exec` overlap.** Rust has two separate commands for agent execution: `run` (stub) and `runtime exec` (implemented with IPC). Python's `run` is the primary execution command with 3 modes (mcp/router/cli). The Rust codebase should clarify: is `run` the user-facing command that delegates to `runtime exec` internally, or are they distinct?

2. **Config directory location.** Python uses `~/.agent-nexus/` (via `_get_config_dir()`). Rust uses the project root (found by walking up for `config.toml`). This is a fundamental architectural difference that affects all commands.

3. **JSON output.** Rust applies `--json` globally via `OutputFormatter`. Python applies `--json` per-command. The global approach is cleaner but must be consistently supported across all commands.

4. **`--version` UX.** Python uses a top-level `--version/-v` flag. Rust uses a separate `version` subcommand. Users accustomed to `agent-nexus --version` will not find it in Rust.
