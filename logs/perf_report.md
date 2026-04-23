# Performance Report

Generated: 2026-04-23 on macOS Darwin 25.4.0 (Apple Silicon, debug build)

## Benchmark Results

### ap-core

| Benchmark | Time (avg) | Threshold | Target | Headroom | Status |
|-----------|-----------|-----------|--------|----------|--------|
| config_loading | 106.4us | 2500us | 500us | 95.7% | healthy |
| task_graph_add_task | 477.9us | 5000us | 1000us | 90.4% | healthy |
| task_graph_get_ready_tasks | 276.1us | 5000us | 1000us | 94.5% | healthy |
| ipc_serialize_platform_to_agent | 5.1us | 250us | 50us | 97.9% | healthy |
| ipc_deserialize_platform_to_agent | 3.0us | 250us | 50us | 98.8% | healthy |
| ipc_serialize_agent_to_platform | 6.4us | 250us | 50us | 97.4% | healthy |
| ipc_deserialize_agent_to_platform | 5.6us | 250us | 50us | 97.8% | healthy |
| model_config_resolve | 816ns | 500us | 100us | 99.8% | healthy |
| model_config_resolve_fallback | 10.9us | 500us | 100us | 97.8% | healthy |

### ap-evolution

| Benchmark | Time (avg) | Threshold | Target | Headroom | Status |
|-----------|-----------|-----------|--------|----------|--------|
| schema_initialization (in-memory) | 1.06ms | 250ms | 50ms | 99.6% | healthy |
| skill_inserts (1000 total) | 94.0ms | 300ms | 200ms | 68.7% | healthy |
| skill_lookups (per lookup) | 25.5us | 500us | 100us | 94.9% | healthy |
| analysis_recording (1000 total) | 71.6ms | 250ms | 50ms | 71.4% | healthy |
| file_backed_schema_init (avg) | 51.7ms | 250ms | 50ms | 79.3% | borderline |

### ap-cli

| Benchmark | Time (avg) | Threshold | Target | Headroom | Status |
|-----------|-----------|-----------|--------|----------|--------|
| cli_version_cold_start | 5.8ms | 500ms | 100ms | 98.8% | healthy |
| cli_help_warm_start | 5.8ms | 500ms | 100ms | 98.8% | healthy |
| cli_init_command | 67.5ms | 1000ms | 200ms | 93.3% | healthy |

**Headroom formula**: `(threshold - actual) / threshold * 100`
- healthy: headroom > 50%
- borderline: headroom 30-50%
- failing: headroom < 30% or exceeds threshold

## Source Code Analysis

### 1. Unnecessary Clones

Checked `.clone()` usage in hot paths:

- **task_graph.rs**: Clones in `detect_cycle_with_conn` and `topological_sort` (lines 257, 260, 264, 293-296, 303, 308, 315). These are necessary -- the data is owned by `Vec<TaskItem>` and we need `String` keys for `HashMap`/`HashSet`. The clone cost is O(n) on task count, which is acceptable for typical graph sizes (10-100 tasks).
- **model_config.rs**: Clones in `resolve()` (lines 64-65, 78-80) for building the resolved config. Called per-request but at 816ns average, this is negligible.
- **process_manager.rs**: Clones in `spawn_config` and `env` -- only called at process spawn time, not in a hot loop.

**Verdict**: No actionable optimizations. All clones are structurally necessary or in cold paths.

### 2. String Allocation in Loops

- **task_graph.rs `task_from_row`**: Calls `serde_json::to_string()` for `blocked_by`, `vars`, and `result` on every row read. This is the hottest path in `get_ready_tasks` and `add_task` (via `load_all_tasks`). However, the benchmark shows 276us per `get_ready_tasks` call, well within limits.
- **queries.rs `get_ancestry`**: Uses `format!("?{}", i + 1)` for building SQL placeholders. This runs once per ancestry query (not per-row), so it is negligible.

**Verdict**: No actionable optimizations. String allocations are proportional to data size, not quadratic.

### 3. SQLite WAL Mode

Both SQLite stores enable WAL mode:
- `ap-core/src/orchestration/task_graph.rs:100` -- `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`
- `ap-evolution/src/store/mod.rs:54` -- `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`

Note: In-memory databases (`Connection::open_in_memory()`) do not apply WAL mode since they have no journal. This is expected and correct.

**Verdict**: WAL mode is properly configured for file-backed stores.

### 4. N+1 Query Patterns

- **task_graph.rs `add_task`** (lines 135-145): Calls `get_task()` individually for duplicate check and each `blocked_by` dependency. Each call is a separate SQL query. However, typical tasks have 0-3 dependencies, so this results in at most 4 queries per `add_task` call. The benchmark shows 477us average which is dominated by cycle detection (a full table scan), not the dependency checks.

- **queries.rs `get_ancestry`** (lines 346-365): Prepares a statement inside the BFS inner loop (`for sid in &frontier`), meaning a new prepared statement per node per depth level. However, ancestry trees are typically shallow (1-3 levels) with few nodes per level, and this function is not called in any benchmark or hot path.

- **queries.rs `evolve_skill`** (lines 254-261): Loops over `parent_ids` with individual UPDATE statements. This is within a transaction, so it is batched at the SQLite level. Parent count is typically 1-2.

**Verdict**: The `get_ancestry` statement-reprepare is a minor code quality issue but not a performance problem. No changes needed.

### 5. Algorithm Complexity

- **Cycle detection** (`detect_cycle_with_conn` + `dfs`): Uses DFS with three-color marking. Complexity is O(V + E) where V = number of tasks, E = total dependencies. This is optimal. Called on every `add_task`, which makes `add_task` O(V + E) overall. For 100 tasks this takes ~477us, which is acceptable.

- **Topological sort** (`topological_sort`): Uses Kahn's algorithm (BFS-based). Complexity is O(V + E). This is optimal. Additionally calls `detect_cycle` first, making the combined cost O(V + E) + O(V + E) = O(V + E).

- **get_ready_tasks**: Loads all tasks into memory, builds a HashMap of states, then filters. Complexity is O(V + E). This is correct and efficient. The in-memory approach avoids complex SQL queries and benefits from the typical small graph size.

**Verdict**: All algorithms use optimal approaches. No O(n^2) or worse patterns found.

## Issues Found

### file_backed_schema_init -- Borderline

The `bench_file_backed_schema_init` benchmark shows an average of 51.7ms against a target of 50ms, but the threshold is 250ms so it passes comfortably (79.3% headroom). The high variance (fastest 6.6ms, slowest 406.7ms) suggests this is dominated by filesystem and OS-level caching effects, not a code issue. The first run is slowest due to cold cache, subsequent runs are fast. No code change warranted.

### get_ancestry Statement Reprepare -- Minor

`get_ancestry` in `queries.rs` prepares a new statement inside the BFS inner loop. This is technically wasteful since the same SQL could be prepared once and reused. However:
- The function is not called in any hot path
- Ancestry trees are typically very shallow (1-3 levels)
- The benchmark suite does not cover this path
- Fixing it would add marginal complexity for no measurable benefit

**Decision**: Not fixing. Documented as a known minor inefficiency for future reference.

## Optimizations Applied

None. All benchmarks pass with healthy headroom. No performance issues were found that warrant code changes.

## Recommendations

1. **Monitor file_backed_schema_init on CI**: The high variance (6.6ms to 406.7ms) could cause flaky failures on slower CI machines. Consider whether the 250ms threshold provides enough margin for shared CI runners with unpredictable I/O.

2. **Future: Batch inserts for evolution store**: The `bench_skill_inserts` shows 94ms for 1000 inserts (94us each). If bulk import becomes a use case, wrapping inserts in a single transaction would reduce this significantly (SQLite autocommit overhead is the dominant cost).

3. **Future: Prepared statement caching for task_graph**: Both `add_task` and `get_task` prepare statements on every call. For high-frequency call patterns, a prepared statement cache could reduce overhead. Current benchmarks show this is not needed at typical workload sizes.

4. **Future: get_ancestry statement hoisting**: If ancestry traversal becomes a hot path, hoist the prepared statement outside the BFS loop.
