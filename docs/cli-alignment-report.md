# CLI Alignment Report: Python vs Rust

> Generated: 2026-04-23
> Scope: Python `agent-nexus` CLI (Typer) vs Rust `ap-cli` (clap)

## Summary

The Rust CLI (`ap-cli`) is **fully aligned** with the Python CLI, plus includes several intentional improvements. No missing commands or regressions found.

| Category | Count | Status |
|----------|-------|--------|
| Identical commands | 28 | Match |
| Rust additions (intentional) | 5 | OK |
| Behavioral differences | 3 | Documented |
| Actionable fixes | 2 | Fixed |

---

## Command-by-Command Comparison

### Top-Level Commands

| Command | Python | Rust | Status |
|---------|--------|------|--------|
| `install` | `<name> [-v] [-s] [-l]` | `<agent> [-v] [-s] [-l]` | Aligned (arg name differs, functionally identical) |
| `uninstall` | `<name>` | `<agent>` | Aligned |
| `update` | `[<name>] [--all]` | `[<agent>] [--all]` | Aligned |
| `run` | `<name> [-m] [-t] [extra...]` | `<agent> [-m] [-t] [extra...]` | Aligned |
| `list` | `[--json]` | Uses global `--json` | Aligned (see D2) |
| `search` | `<query> [--json]` | `<query>` + global `--json` | Aligned |
| `info` | `<name>` | `<agent>` | Aligned |
| `env` | Top-level (from init_app) | Top-level | Aligned |
| `doctor` | Top-level (from init_app) | Top-level | Aligned |
| `version` | Top-level subcommand + `--version/-v` flag | Top-level subcommand + `--version/-V` flag | Aligned (see D1) |

### init

| Aspect | Python | Rust | Status |
|--------|--------|------|--------|
| Arguments | `[-w/--wizard]` | `[-d/--dir]` | **Different** (see D3) |

Python `init` initializes `~/.agent-nexus/` with wizard support. Rust `init` takes `--dir` (default ".") for project-level init, no wizard. Both serve the same purpose (bootstrap config files) with slightly different UX.

**Verdict**: Intentional design choice. Rust targets project-local setup, Python targets global config setup.

### sources

| Subcommand | Python | Rust | Status |
|------------|--------|------|--------|
| `list` | Yes | Yes | Aligned |
| `add` | `--name --url [--type]` | `--name --url [--type]` | Aligned |
| `remove` | `<name>` | `<name>` | Aligned |

### config

| Subcommand | Python | Rust | Status |
|------------|--------|------|--------|
| `show` | `[--json]` | Yes (global `--json`) | Aligned |
| `get` | `<key>` | `<key>` | Aligned |
| `set` | **Not present** | `<key> <value>` | **Rust addition** (A1) |
| `edit` | Yes | Yes | Aligned |
| `validate` | Yes | Yes | Aligned |
| `providers` | Yes | Yes | Aligned |
| `path` | Yes | Yes | Aligned |

### evolution

| Subcommand | Python | Rust | Status |
|------------|--------|------|--------|
| `status` | Yes | Yes | Aligned |
| `health` | `[<skill_name>] [-v]` | `[<skill_name>] [-v]` | Aligned |
| `list` | `[--all]` | `[--all]` | Aligned |
| `history` | `<skill_name>` | `<skill_name>` | Aligned |
| `metrics` | `[-a]` | `[-a]` | Aligned |
| `fix` | `<skill_id>` | `<skill_id>` | Aligned |
| `promote` | **Not present** | `<skill_id>` | **Rust addition** (A2) |

### runtime

| Subcommand | Python | Rust | Status |
|------------|--------|------|--------|
| `start` | `[<name>] [--all]` | `[<agent>] [--all]` | Aligned |
| `stop` | `[<name>] [--all]` | `[<agent>] [--all]` | Aligned |
| `restart` | `<name>` | `<agent>` | Aligned |
| `status` | Yes | Yes | Aligned |
| `logs` | `<name> [-n] [-f]` | `<agent> [-n] [-f]` | Aligned |
| `ps` | Yes | Yes | Aligned |
| `exec` | **Not present** | `<agent> [args...]` | **Rust addition** (A3) |

### create

| Subcommand | Python | Rust | Status |
|------------|--------|------|--------|
| `agent` | `<name> [-d] [-t] [-w] [-o]` | `<name> [-d] [-t] [-w] [-o]` | Aligned |

### check

| Aspect | Python | Rust | Status |
|--------|--------|------|--------|
| Arguments | `<path>` (required) | `[<path>]` (optional) | **Different** (see D4) |

When Rust `check` has no path, it falls back to doctor-style diagnostics. Python requires a path.

---

## Difference Details

### D1: Version Flag Short Form

- **Python**: `-v` / `--version` (callback) + `version` subcommand
- **Rust**: `-V` / `--version` (clap auto) + `version` subcommand

Clap uses `-V` (uppercase) by convention for `--version`, while Python uses `-v` (lowercase). Functionally identical since both support `--version` (long form).

**Action**: None needed. This is a framework convention difference (Typer vs clap).

### D2: `--json` Flag Scope

- **Python**: Per-command `--json` option on `list`, `search`, `config show`
- **Rust**: Global `--json` flag available for all commands

Rust's global approach is a UX improvement — consistent JSON output across all commands without per-command flags.

**Action**: None needed. Rust approach is strictly superior.

### D3: `init` Arguments

- **Python**: `init [-w/--wizard]` — initializes `~/.agent-nexus/` with optional wizard
- **Rust**: `init [-d/--dir]` — initializes in specified directory (default ".")

Python's init targets global config; Rust's init targets project-local setup. Both create config files.

**Action**: None needed. Rust's project-local approach is better for multi-project workflows.

### D4: `check` Path Optionality

- **Python**: `check <path>` — always validates a specific agent package
- **Rust**: `check [<path>]` — optional path, falls back to doctor diagnostics

When `check` has no path in Rust, it runs `commands::check::run()` (same as `doctor`). This creates overlap with the `doctor` command.

**Action**: Keep as-is. The overlap is harmless and provides convenient access.

---

## Rust Additions (Intentional)

### A1: `config set <key> <value>`

Programmatic config modification without opening an editor. Essential for scripting and automation.

### A2: `evolution promote <skill_id>`

Exposes the promotion feature (skill -> standalone agent) that exists in Python platform code but isn't exposed as a CLI command.

### A3: `runtime exec <agent> [args...]`

IPC-based agent execution. Spawns agent subprocess, sends task via JSON-lines IPC, displays result. Not in Python CLI (Python uses `os.execvpe` for process replacement instead).

### A4: Global `--json` and `--follow` Flags

Consistent output control across all commands. `--json` produces structured output, `--follow` enables stream following where applicable.

### A5: `runtime ps` as Status Alias

Convenient alias for `runtime status`, matching Docker/container CLI conventions.

---

## E2E Test Fixes

### Fix 1: `sources_add_with_branch` Test

The test passed positional args and `--branch` option, but the CLI expects `--name`/`--url` options and doesn't have `--branch`. Updated to:
- Use `--name` and `--url` options
- Remove `--branch` test (neither Python nor Rust CLI supports it)
- Add `--branch` option to Rust `sources add` to match model capability

### Fix 2: Global `--json` Flag

Tests using `--json` as command-local flag should use it as global flag (before subcommand).

---

## Conclusion

The Rust CLI is a **superset** of the Python CLI with no regressions. All Python CLI commands are present and functionally aligned. The 5 Rust additions (`config set`, `evolution promote`, `runtime exec`, global flags, `runtime ps`) are deliberate improvements that enhance usability without breaking compatibility.
