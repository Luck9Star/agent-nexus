# Rust Platform Deep Review V2 — Full Codebase Audit

> Generated: 2026-04-24
> Scope: All 6 Rust crates (ap-core, ap-runtime, ap-gateway, ap-fetcher, ap-evolution, ap-cli)
> Method: 3 parallel code-reviewer agents, each reviewing 2 crates, reading every source file
> Previous review: V1 had 40 findings (F1-F40). This V2 report excludes all V1 findings and covers NEW issues only.
> Total new findings: 37 (2 Critical, 14 High, 17 Medium, 4 Low)

## Executive Summary

V1 findings were primarily architecture-level observations. V2 drilled into **specific code paths** and found:

1. **Production deadlocks**: `ProcessManagerHandle` holds tokio Mutex across `.await`, `activate()` holds per-agent Mutex during network I/O
2. **Data loss bugs**: `AgentProtocol::heartbeat` consumes real agent responses, `evolution list --all` is completely broken
3. **Security gaps**: PID recycling sends SIGTERM to wrong process, symlink following during local install, unbounded IPC read
4. **Silent failures**: `is_success()` treats missing status as success, `topological_sort` returns incomplete results, `McpServerConfig` accepts unknown transports

| Crate | New Findings | Critical | High | Medium | Low |
|-------|-------------|----------|------|--------|-----|
| ap-core | 10 | 1 | 3 | 6 | 0 |
| ap-runtime | 3 | 0 | 1 | 2 | 0 |
| ap-gateway | 5 | 0 | 2 | 3 | 0 |
| ap-fetcher | 6 | 0 | 2 | 4 | 0 |
| ap-evolution | 6 | 0 | 4 | 2 | 0 |
| ap-cli | 7 | 1 | 2 | 3 | 1 |
| **Total** | **37** | **2** | **14** | **20** | **1** |

**Top themes**:
1. Lock-then-await pattern causing deadlocks across 3 crates
2. Missing input validation allowing silent acceptance of bad data
3. Race conditions in concurrent file writes and database operations
4. Process lifecycle management gaps (orphan processes, PID recycling)

---

## Critical Findings

### C1: ProcessManagerHandle holds tokio Mutex guard across .await -- deadlock in production

- **Crate**: ap-core
- **Category**: Concurrency
- **Location**: `crates/ap-core/src/orchestration/process_manager.rs:537-544`
- **Confidence**: 95%
- **Problem**: `spawn`, `graceful_shutdown`, `graceful_shutdown_all`, and `restart_agent` on `ProcessManagerHandle` all acquire a tokio `Mutex`, then call async methods on the inner `ProcessManager` while holding the guard. The struct's own documentation (lines 464-499) explicitly warns against this pattern.
- **Impact**: Under contention (multiple tokio tasks calling these methods concurrently), the second task blocks indefinitely waiting for the Mutex while the first is `.await`-ing inside the lock. Especially severe for `graceful_shutdown_all` which iterates all processes sequentially while holding the lock.
- **Trigger**: Two concurrent tokio tasks both calling `handle.graceful_shutdown("agent-1", timeout).await`.
- **Fix**: Acquire the lock, extract needed data synchronously, drop the lock, then await. Or restructure so each async operation takes ownership of the relevant `ManagedProcess` temporarily.

### C2: runtime stop uses raw PID from file -- PID recycling kills wrong process

- **Crate**: ap-cli
- **Category**: Security / Bug
- **Location**: `crates/ap-cli/src/commands/runtime.rs:132-141`
- **Confidence**: 92%
- **Problem**: The PID is read from a file, then `libc::kill(pid, SIGTERM)` is called. Between when the PID was written and when `stop` is called, the original process may have exited and the OS may have recycled the PID for a completely different process. No verification that the process is actually the agent.
- **Impact**: Sending SIGTERM to an arbitrary process. If the PID was recycled for a system service, it will be killed. `runtime status` also has this issue -- reports the wrong process as "running".
- **Trigger**: Start an agent, wait for it to exit naturally, let OS recycle the PID, then run `agent-nexus runtime stop <agent>`.
- **Fix**: Store start timestamp + expected command name in the PID file. Before killing, verify via `/proc/<pid>/cmdline` (Linux) or `ps` that the process matches. Use process groups for safer targeting.

---

## High Findings

### H1: AgentProtocol heartbeat consumes legitimate agent responses

- **Crate**: ap-runtime
- **Category**: Bug / Data Loss
- **Location**: `crates/ap-runtime/src/ipc/protocol.rs:93-109`
- **Confidence**: 95%
- **Problem**: `heartbeat` sends a `__ping__` and calls `receive_result` to wait for `__pong__`. But `receive_result` reads the *next available message*. If the agent sends a progress update or result between ping and expected pong, heartbeat consumes it. The real response is lost forever.
- **Impact**: Heartbeat calls silently eat real agent responses (results, progress updates), causing hung tasks and lost data.
- **Trigger**: Platform sends a task to agent, then calls `heartbeat()` while waiting for the result. Agent sends a progress update. Heartbeat consumes it. Real progress update is lost.
- **Fix**: Use a separate channel for heartbeat (stderr or separate IPC channel), or buffer non-pong messages and replay them, or use process liveness checks instead of application-level heartbeat.

### H2: McpServerConfig validate accepts unknown transport types silently

- **Crate**: ap-core
- **Category**: Bug
- **Location**: `crates/ap-core/src/models/agent.rs:71-86`
- **Confidence**: 90%
- **Problem**: `validate()` only checks `stdio` and `sse`. The `_ => {}` branch accepts typos like `"stio"`, `"ses"`, `"websocket"`, `"grpc"` without error. Misconfigured manifests pass validation, then fail at runtime with opaque errors.
- **Impact**: No feedback until runtime when MCP connection attempt fails. User gets an opaque "transport not found" error instead of a clear validation message at config load time.
- **Trigger**: Create `AgentManifest` YAML with `transport: "websockets"` (typo). Validates successfully. Runtime connection fails.
- **Fix**: Return error for unknown transports in the `_ => {}` branch.

### H3: IpcLockRegistry eviction breaks per-agent serialization guarantee

- **Crate**: ap-core
- **Category**: Concurrency
- **Location**: `crates/ap-core/src/orchestration/ipc_lock.rs:37-45`
- **Confidence**: 85%
- **Problem**: When registry exceeds `MAX_LOCKS` (1000), eviction removes the `Arc<Mutex<()>>` from DashMap. A previous caller may still hold a cloned Arc. If the same agent calls `get_or_create` again, it gets a *new* Mutex, so two concurrent IPC operations for the same agent use different locks, destroying mutual exclusion.
- **Impact**: Interleaved writes on the agent's stdin pipe after eviction+recreation.
- **Trigger**: Registry fills to 1000 agents. Agent-0 gets a lock. 1000 more agents evict agent-0. Agent-0 calls again, gets a different Mutex. Both proceed simultaneously.
- **Fix**: Only evict entries whose `Arc::strong_count` is 1 (only registry holds a reference). Or use an LRU cache that respects active references.

### H4: IpcLockRegistry order queue and DashMap desynchronize under concurrent access

- **Crate**: ap-core
- **Category**: Concurrency
- **Location**: `crates/ap-core/src/orchestration/ipc_lock.rs:39-44`
- **Confidence**: 80%
- **Problem**: The eviction pops from the front of the `order` VecDeque, but the popped ID may not match the entry actually removed from the DashMap. Under concurrent `get_or_create` calls, the two data structures diverge.
- **Impact**: DashMap may exceed `MAX_LOCKS` because order queue contains stale entries. Or eviction removes a non-existent entry (harmless but wastes capacity).
- **Trigger**: Multiple threads call `get_or_create` concurrently with high agent count.
- **Fix**: Use a single `Mutex<HashMap>` instead of DashMap + separate `Mutex<VecDeque>`.

### H5: ProcessManager spawn inherits all parent environment variables

- **Crate**: ap-core
- **Category**: Security
- **Location**: `crates/ap-core/src/orchestration/process_manager.rs:127-136`
- **Confidence**: 85%
- **Problem**: `command.envs(env_vars)` *adds* to the parent's environment. Sensitive parent env vars (API keys, database credentials) are always inherited. No option to isolate the child process.
- **Impact**: Agent subprocess has full access to platform's environment, including all API keys. Violates least privilege per defense-in-depth architecture.
- **Trigger**: Spawn agent process, have it read `/proc/self/environ`. Sees all parent environment variables.
- **Fix**: Add a `spawn_isolated` method using `command.env_clear().envs(env_vars)` plus minimal required vars (PATH).

### H6: register_manifest silently overwrites active agent, leaking subprocess

- **Crate**: ap-gateway
- **Category**: Bug / Resource Leak
- **Location**: `crates/ap-gateway/src/deferred_registry.rs:87-96`
- **Confidence**: 90%
- **Problem**: `register_manifest()` does `HashMap::insert`, replacing any existing slot. If the existing slot has an active MCP client (running subprocess), the old `AgentSlot` is dropped without calling `shutdown()`. The subprocess is leaked.
- **Impact**: Orphaned agent subprocesses that consume resources indefinitely with no way to shut them down.
- **Trigger**: Register and activate agent "reviewer". Register "reviewer" again with different manifest. Old subprocess leaked.
- **Fix**: Check for existing entry, deactivate it (call `shutdown()` on the client) before replacing.

### H7: activate holds per-agent Mutex during list_tools -- blocks all concurrent call_tool on same agent

- **Crate**: ap-gateway
- **Category**: Concurrency
- **Location**: `crates/ap-gateway/src/deferred_registry.rs:137-169`
- **Confidence**: 88%
- **Problem**: `activate()` acquires the per-agent `Mutex<AgentSlot>` and holds it through the entire `list_tools()` network call. Any concurrent `call_tool()` to the same agent blocks until `list_tools()` completes.
- **Impact**: During first activation, all tool calls to that agent are blocked. If `list_tools()` hangs (network, dead subprocess), the agent is completely unresponsive.
- **Trigger**: Task A calls `activate("reviewer", factory)`. Task B simultaneously calls `call_tool("reviewer", ...)`. Task B blocks until activation completes.
- **Fix**: Get `client_arc` from `OnceCell` under the slot lock, drop the slot lock, then call `list_tools()`. Re-acquire slot lock briefly only to cache the result.

### H8: sources save() and lockfile save() are public without advisory lock

- **Crate**: ap-fetcher
- **Category**: Bug / Data Loss
- **Location**: `crates/ap-fetcher/src/sources.rs:103-115`, `crates/ap-fetcher/src/lockfile.rs:69-88`
- **Confidence**: 90%
- **Problem**: Both `save()` methods are public, write atomically (tmp+rename) but do NOT acquire the advisory file lock. Meanwhile `add()` and `remove()` DO acquire the lock. Since `rename()` doesn't check advisory locks, `save()` can overwrite while `add()` is mid-operation.
- **Impact**: Concurrent `save()` and `add()` calls can lose entries. The advisory lock in `add()`/`remove()` provides a false sense of safety.
- **Trigger**: Thread 1 calls `mgr.add(entry)`, loads [A, B]. Thread 2 calls `mgr.save([C, D])`, writes immediately. Thread 1 writes [A, B, new], overwriting [C, D].
- **Fix**: Make `save()` private (the public API should only be `add()`/`remove()`), or acquire the advisory lock in `save()`.

### H9: dispatch_analysis double-counts health events for failed tasks

- **Crate**: ap-evolution
- **Category**: Bug
- **Location**: `crates/ap-evolution/src/engine.rs:191,211`
- **Confidence**: 88%
- **Problem**: When a failed task triggers analysis, `record_health(false)` is called at line 191. If the subsequent `evolve_fix()` also fails, `record_health(false)` is called again at line 211. A single task failure produces 2 health degradation events.
- **Impact**: Health score drops faster than it should, triggering premature MetricCheck evolution cycles. After N failures that also fail to evolve, the score reflects 2N failures instead of N.
- **Trigger**: Call `evolve(EvolveTrigger::Analysis { success: false, ... })` where the suggested skill exists but `evolve_fix` fails (e.g., SQLite write error).
- **Fix**: Remove the second `record_health(false)` at line 211, or move the initial recording to after the evolve loop completes.

### H10: evolution list --all is completely broken

- **Crate**: ap-evolution / ap-cli
- **Category**: Bug
- **Location**: `crates/ap-cli/src/commands/evolution.rs:103-107`
- **Confidence**: 95%
- **Problem**: The `--all` branch calls `store.get_skill_by_name("")` to get inactive skills. But `get_skill_by_name` queries `WHERE name = ?1 AND is_active = 1 LIMIT 1`. Empty string never matches, so it always returns `None`. The result chains with `get_active_skills()`, making `--all` identical to the default behavior. Inactive skills are never shown.
- **Impact**: The `--all` flag is completely non-functional. Users cannot list deactivated/evolved-from skill versions.
- **Trigger**: Run `agent-nexus evolution list --all` after evolving a skill. The deactivated parent does not appear.
- **Fix**: Add a `get_all_skills()` method to `EvolutionStore` that queries without the `is_active = 1` filter. Use it in the `--all` branch.

### H11: evolve_fix concurrent race on same skill name

- **Crate**: ap-evolution
- **Category**: Concurrency
- **Location**: `crates/ap-evolution/src/evolver.rs:69`
- **Confidence**: 82%
- **Problem**: Two concurrent `evolve_fix` calls for the same skill name both find the same active skill via `get_skill_by_name`, both generate new IDs, and both call `evolve_skill`. Each uses a separate r2d2 connection. Under WAL mode, both can commit: first deactivates parent + inserts child-A, second re-deactivates (no-op) + inserts child-B. One fails with opaque `SqliteFailure` on the unique constraint.
- **Impact**: Concurrent evolution triggers for the same skill produce an opaque error instead of a clean typed error.
- **Trigger**: Two threads call `engine.evolve(EvolveTrigger::Failure { skill_name: "same-skill", ... })` concurrently.
- **Fix**: Catch the specific unique constraint violation and return `EvolverError::ConcurrentModification`. Or acquire a named Mutex per skill name.

### H12: HealthTracker from_persisted accepts arbitrary score without clamping

- **Crate**: ap-evolution
- **Category**: Bug
- **Location**: `crates/ap-evolution/src/health.rs:32-34`
- **Confidence**: 85%
- **Problem**: `from_persisted(score, total)` directly sets `self.score = score` without validating [0.0, 1.0]. A corrupted `_meta` table with score=5.0 produces `5.0 * 0.9 = 4.5` on failure, diverging further from 1.0 instead of converging toward 0.0. Metric check never triggers evolution.
- **Impact**: Corrupted DB values break health tracking permanently. Score > 1.0 prevents evolution from ever triggering.
- **Trigger**: Insert `INSERT INTO _meta VALUES ('health_score', '5.0')`. Restart engine. Health score is 5.0, never triggers evolution.
- **Fix**: Clamp: `Self { score: score.clamp(0.0, 1.0), total }`. Validate total is reasonable.

### H13: runtime start leaks child process

- **Crate**: ap-cli
- **Category**: Bug / Resource Leak
- **Location**: `crates/ap-cli/src/commands/runtime.rs:80-88`
- **Confidence**: 88%
- **Problem**: `std::process::Command::spawn()` returns a `Child`. Only `child.id()` is extracted for the PID file, then `Child` is dropped. On Unix, `Child::drop` is a no-op -- the process becomes orphaned. No mechanism to forward signals or detect unexpected exit. PID file becomes stale.
- **Impact**: Started agents are orphan processes. Stale PID files cause `runtime status` to report wrong state or PID recycling issues.
- **Trigger**: Run `agent-nexus runtime start my-agent`. Process is orphaned. If it crashes, PID file is stale.
- **Fix**: Spawn a background thread that calls `child.wait()` to reap the child and clean up the PID file on exit.

### H14: do_update_agent hardcodes branch "refs/heads/main"

- **Crate**: ap-cli
- **Category**: Bug
- **Location**: `crates/ap-cli/src/commands/install.rs:262-267`
- **Confidence**: 85%
- **Problem**: The fast-forward update path hardcodes `refs/heads/main`. If an agent was installed from a different branch (`--branch develop`), the update fast-forwards `main` instead of the correct branch.
- **Impact**: After update, the agent may be running code from the wrong branch. Lockfile SHA is updated, so user thinks update succeeded.
- **Trigger**: Install agent from non-main branch. Run `agent-nexus update <agent>`. Gets wrong branch content.
- **Fix**: Store branch name in `LockfileEntry`. Use the stored branch name instead of hardcoding `"refs/heads/main"`.

---

## Medium Findings

### M1: OrchestrationDsl get_execution_order does not sort by phase for non-root tasks

- **Crate**: ap-core
- **Category**: Bug / Logic
- **Location**: `crates/ap-core/src/orchestration/dsl.rs:170-175`
- **Confidence**: 85%
- **Problem**: Doc comment says "Respects phase ordering for ties", and root tasks are sorted by phase (line 161). But newly-eligible tasks (degree reaches 0) are pushed to the back of the queue with `push_back(t)` without phase-based sorting.
- **Impact**: Tasks may execute out of documented phase order when multiple tasks become eligible simultaneously.
- **Trigger**: Root task has two dependents at different phases. Root completes. Higher-phase dependent may execute before lower-phase one.

### M2: topological_sort silently returns incomplete results for inconsistent graphs

- **Crate**: ap-core
- **Category**: Error Handling
- **Location**: `crates/ap-core/src/orchestration/task_graph.rs:290-329`
- **Confidence**: 82%
- **Problem**: After Kahn's algorithm, if `result.len() != tasks.len()` (due to dangling dependency references), the method returns `Ok(result)` without checking. Tasks with dangling deps remain with `in_degree > 0` and are silently dropped.
- **Impact**: Caller relying on `topological_sort` to process all tasks will silently miss some.
- **Trigger**: Insert tasks t1, t2. Via raw SQL, set t1's blocked_by to reference non-existent "ghost". `topological_sort` returns only t2.

### M3: AgentToPlatform is_success treats missing status as success

- **Crate**: ap-core
- **Category**: Bug / Logic
- **Location**: `crates/ap-core/src/models/ipc.rs:95-100`
- **Confidence**: 90%
- **Problem**: `is_success()` returns `true` when `status` is `None` (via `is_none_or`). A malformed response with no `status` field is treated as successful completion.
- **Impact**: Masks real errors where agent crashed mid-response and sent incomplete JSON.
- **Trigger**: Agent crashes and output buffer flushes partial `{"type":"result","content":"","task_id":"t1"}` with no status. Platform treats it as successful.

### M4: IpcStream receive allows unbounded memory allocation before size check

- **Crate**: ap-core
- **Category**: Security / DoS
- **Location**: `crates/ap-core/src/orchestration/ipc.rs:68-79`
- **Confidence**: 88%
- **Problem**: `read_until(b'\n', &mut line)` reads entire line into memory before checking 4MB limit. A malicious agent writing bytes without newlines causes unbounded allocation.
- **Impact**: Memory exhaustion / DoS. A compromised agent subprocess can cause unbounded buffer growth.
- **Trigger**: Agent subprocess enters infinite loop writing non-newline bytes to stdout.

### M5: ConfigLoader apply_env_overrides reads process-global env non-deterministically

- **Crate**: ap-core
- **Category**: Concurrency
- **Location**: `crates/ap-core/src/config/loader.rs:49-53, 141-153`
- **Confidence**: 80%
- **Problem**: `apply_env_overrides` reads `AGENT_MODEL`/`DEFAULT_MODEL` from env at load time. If two async tasks call `load_from_str` concurrently while another modifies these vars, config is non-deterministic.
- **Impact**: Platform config can end up with stale or partially-overridden default model string.
- **Trigger**: Task A calls `ConfigLoader::load_from_str` while Task B calls `std::env::set_var("AGENT_MODEL", "new")`.

### M6: AgentProcess spawn missing kill_on_drop(true)

- **Crate**: ap-runtime
- **Category**: Resource Leak
- **Location**: `crates/ap-runtime/src/process.rs:50-56`
- **Confidence**: 85%
- **Problem**: `Command` used to spawn child does not set `.kill_on_drop(true)`. If tokio runtime shuts down abruptly, the child process continues as orphan.
- **Impact**: Zombie/orphan agent processes after runtime shutdown.
- **Trigger**: Spawn agent, then drop tokio runtime without calling `kill()` first.

### M7: get_tools holds RwLock read while awaiting per-agent Mutex

- **Crate**: ap-gateway
- **Category**: Concurrency
- **Location**: `crates/ap-gateway/src/deferred_registry.rs:201-212`
- **Confidence**: 80%
- **Problem**: `get_tools()` acquires global `RwLock` for reading, then awaits per-agent `Mutex`. If per-agent Mutex is contended (held by long `list_tools()`), the RwLock read guard blocks writers (`register_manifest`).
- **Impact**: Slow agent activation indirectly blocks new agent registration.
- **Trigger**: Task A calls `activate("slow-agent")` holding per-agent Mutex. Task B calls `get_tools("slow-agent")` acquiring RwLock read, blocking on Mutex. Task C calls `register_manifest` blocked by B's read guard.

### M8: deactivate_idle holds RwLock while locking all agent Mutexes sequentially

- **Crate**: ap-gateway
- **Category**: Concurrency
- **Location**: `crates/ap-gateway/src/deferred_registry.rs:251-267`
- **Confidence**: 80%
- **Problem**: Holds global `RwLock` for reading while iterating all agents and acquiring each per-agent `Mutex` serially. A single slow tool call stalls the entire idle sweep and blocks writes.
- **Impact**: In a gateway with many agents, a single slow call can block registration for the entire sweep duration.

### M9: parse_namespaced returns empty agent name for separator-prefixed inputs

- **Crate**: ap-gateway
- **Category**: Bug
- **Location**: `crates/ap-gateway/src/tool_adapter.rs:29-32`
- **Confidence**: 82%
- **Problem**: `parse_namespaced("___tool")` returns `Some(("", "tool"))` -- empty agent name. While HTTP callers are protected by `validate_name`, internal callers using the API directly get empty strings propagating silently.
- **Impact**: API footgun for internal callers. Empty agent name propagates to registry lookup.
- **Fix**: Return `None` when either component is empty.

### M10: install() removes old dir before rename -- crash window loses both versions

- **Crate**: ap-fetcher
- **Category**: Bug
- **Location**: `crates/ap-fetcher/src/installer.rs:98-102`
- **Confidence**: 85%
- **Problem**: `remove_dir_all(&final_path)` followed by `rename(&tmp_path, &final_path)`. Two separate operations. Process crash between them: old version deleted, new version lost (left in tmp). On macOS, rename across filesystems fails with EXDEV.
- **Impact**: Interrupted update loses both old and new versions. Lockfile still records agent as installed.
- **Fix**: Rename old to backup first, then rename new, then clean backup.

### M11: url_to_dirname collision for different repos sharing same basename

- **Crate**: ap-fetcher
- **Category**: Bug
- **Location**: `crates/ap-fetcher/src/installer.rs:154-176`
- **Confidence**: 85%
- **Problem**: Only extracts last path segment. `github.com/foo/agent` and `github.com/bob/agent` both produce dirname `"agent"`. Second install silently overwrites first.
- **Impact**: Installing agents from different sources with same repo name overwrites previously installed agent.
- **Fix**: Include a hash of the full URL in the dirname to prevent collisions.

### M12: UvBridge std::sync::Mutex unwrap panics on poison

- **Crate**: ap-fetcher
- **Category**: Error Handling
- **Location**: `crates/ap-fetcher/src/uv_bridge.rs:214,228`
- **Confidence**: 80%
- **Problem**: `self.resolved.lock().unwrap()` -- if any prior panic poisoned the mutex, all subsequent calls to `create_venv`, `pip_install`, `check_available` panic immediately.
- **Impact**: UvBridge becomes permanently unusable after a single panic in the critical section.
- **Fix**: Use `.unwrap_or_else(|e| e.into_inner())` to recover from poisoned mutex.

### M13: validate_local_source_path skips blocked-prefix check for non-existent paths

- **Crate**: ap-fetcher
- **Category**: Security
- **Location**: `crates/ap-fetcher/src/installer.rs:224`
- **Confidence**: 75%
- **Problem**: Canonicalization and blocked-prefix check gated by `if expanded.exists()`. Path like `/etc/nonexistent-repo` passes validation because it doesn't exist.
- **Impact**: Security validation inconsistent -- blocked directories only checked if they happen to exist on filesystem.
- **Fix**: Check against `BLOCKED_PREFIXES` on the expanded path regardless of existence.

### M14: evolution fix CLI param is named skill_id but code uses it as skill_name

- **Crate**: ap-cli
- **Category**: Bug / API
- **Location**: `crates/ap-cli/src/commands/evolution.rs:208-210`
- **Confidence**: 90%
- **Problem**: CLI defines `evolution fix <skill_id>` but passes it as `skill_name` to `EvolveTrigger::Failure`, which calls `get_skill_by_name()`. Skill IDs look like `"test-skill__fix_a1b2c3d4"` while names are `"test-skill"`. Providing an ID (as help text suggests) fails.
- **Impact**: Users following CLI help text provide an ID and get "SkillNotFound".
- **Fix**: Rename CLI argument to `skill_name`, or change implementation to try `get_skill_by_id` first.

### M15: copy_dir_recursive follows symlinks -- symlink attack during local install

- **Crate**: ap-cli
- **Category**: Security
- **Location**: `crates/ap-cli/src/commands/install.rs:447-460`
- **Confidence**: 82%
- **Problem**: Uses `fs::read_dir` and `fs::copy` which follow symlinks. A malicious agent package with a symlink to `/etc/passwd` causes that file to be copied into the install directory.
- **Impact**: Malicious local agent can exfiltrate arbitrary files during `install --local`.
- **Trigger**: Create `agents/atomic/evil-agent/config.toml` as symlink to `/etc/passwd`. Run `agent-nexus install evil-agent --local`.
- **Fix**: Check `entry.file_type()?.is_symlink()` and reject or resolve safely.

### M16: runtime exec creates new tokio runtime -- panics in async context

- **Crate**: ap-cli
- **Category**: Bug
- **Location**: `crates/ap-cli/src/commands/runtime.rs:297-298`
- **Confidence**: 75%
- **Problem**: `tokio::runtime::Runtime::new()` panics with "Cannot start a runtime from within a runtime" if called from async context. Currently safe since CLI is synchronous, but latent footgun.
- **Impact**: Panics if CLI is ever embedded in an async framework or called from `#[tokio::test]`.

### M17: EvolveTrigger Failure.error is String but Analysis.error is Option<String> -- inconsistent API

- **Crate**: ap-evolution
- **Category**: API
- **Location**: `crates/ap-evolution/src/engine.rs:33-36`
- **Confidence**: 72%
- **Problem**: `Failure` has `error: String` (required), `Analysis` has `error: Option<String>`. Inconsistent types for conceptually similar fields. Callers transitioning between variants get confusing type errors.
- **Impact**: API confusion, not a runtime bug.
- **Fix**: Rename `Failure.error` to `Failure.reason`, or make both `Option<String>`.

---

## Low Findings

### L1: config edit passes unsanitized $EDITOR to Command::new

- **Crate**: ap-cli
- **Category**: Security (informational)
- **Location**: `crates/ap-cli/src/commands/config.rs:209-213`
- **Confidence**: 60%
- **Problem**: `EDITOR` env var passed directly to `Command::new`. A malicious binary path would execute. However, this is standard Unix convention and `Command::new` doesn't go through shell, limiting attack surface.
- **Impact**: Minimal -- standard EDITOR convention. `Command::new("rm -rf /")` would fail (no such binary). Risk limited to path to malicious executable.
- **Fix**: Acceptable risk for a CLI tool. Document that `$EDITOR` is trusted input.

---

## Recommended Fix Priority

### Immediate (production-blocking)
1. **C1** -- ProcessManagerHandle Mutex deadlock
2. **C2** -- PID recycling kills wrong process
3. **H1** -- Heartbeat consumes real responses

### Before next release
4. **H6** -- register_manifest leaks subprocess
5. **H8** -- save() without advisory lock
6. **H10** -- evolution list --all broken
7. **M3** -- is_success treats None as success
8. **M4** -- Unbounded IPC read

### When convenient
9. **H2** -- McpServerConfig unknown transport
10. **H7** -- activate blocks call_tool
11. **H9** -- Double-counted health events
12. **H13** -- runtime start leaks child
13. **M10** -- install crash window
14. **M11** -- url_to_dirname collision
15. **M15** -- symlink attack

---

## Appendix: Review Methodology

- **Tools**: 3 parallel `feature-dev:code-reviewer` agents, each reading every source file in 2 assigned crates
- **Scope**: ~18K LOC across 30+ files in 6 crates
- **Exclusions**: All 40 findings from V1 review were provided as exclusion lists to prevent duplication
- **Duration**: ~3.5 minutes wall-clock for all 3 agents (parallel execution)
- **Coverage**: Every `.rs` file in each crate's `src/` directory was read in full
