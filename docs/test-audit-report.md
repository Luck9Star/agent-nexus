# Test Audit Report: Gap & Redundancy Analysis (Cycle 3)

**Date**: 2026-05-10
**Branch**: nf/serena-using-superpo-ff6f4b
**Iteration**: 11 (Cycle 3 全量重分析，覆盖 v1/v2 发现的修复 + 新发现)
**Total Tests**: 4,778 across 170 files

---

## 1. Overall Statistics

| Metric | v2 (Iter 4) | Current (Cycle 3) | Delta |
|--------|-------------|-------------------|-------|
| Source modules (non-init) | 99 | 99 | 0 |
| Total public symbols | 295 | ~651 | +356 (更精确计数) |
| Test files (excl. conftest/__init__) | 167 | 170 | +3 |
| Unit test files | 130 | 124 | -6 (删除+重分类) |
| E2E test files | 21 | 20 | -1 |
| Integration test files | 9 | 10 | +1 |
| Capability test files | 6 | 16 | +10 |
| **Total test functions** | **4,437** | **4,778** | **+341** |

---

## 2. Gap Analysis: Untested Public Symbols (P0-P2)

### 总体覆盖率

| Module Directory | Public Symbols | With Tests | Without | Coverage |
|---|---|---|---|---|
| `platform/hooks/` | 6 | 6 | 0 | 100% |
| `platform/skills/` | 16 | 16 | 0 | 100% |
| `platform/gateway/` | ~65 | ~60 | ~5 | 92% |
| `platform/runtime/` | ~50 | ~48 | ~2 | 96% |
| `platform/evolution/` | ~110 | ~100 | ~10 | 91% |
| `platform/orchestration/` | ~75 | ~70 | ~5 | 93% |
| `models/` | ~95 | ~90 | ~5 | 95% |
| `platform/agency/` | ~120 | ~105 | ~15 | 88% |
| `platform/config/` | 44 | 38 | 6 | 86% |
| `platform/router/` | ~20 | ~18 | ~2 | 90% |
| `platform/local/` | ~50 | ~42 | ~8 | 84% |
| **TOTAL** | **~651** | **~593** | **~58** | **91%** |

### P0 — 关键缺口（高风险路径零覆盖）

> 注：v2 报告的 P0 缺口（ModelDBClient.close、_trigram_candidates、ProcessManager._force_kill_and_reap、HookExecutor.close、PermissionChecker.check_command、AgentSupervisor.auto_restart_dead）已在 iteration 7 通过新增 128 个测试全部修复。以下为残余和新发现缺口。

| Module | Symbol | Risk | Notes |
|--------|--------|------|-------|
| `evolution/store.py` | `EvolutionStore.get_metrics()` | High | Metrics 聚合被 HealthChecker 和 AgentPromoter 消费，无直接测试 |
| `evolution/store.py` | `EvolutionStore.deactivate_skill()` | Medium | evolve_skill 内部使用，无独立断言 |
| `evolution/health.py` | `HealthChecker.get_health_summary()` | Medium | 公开 API，被 EvolutionEngine.diagnose_all 消费 |
| `local/installer.py` | `GitInstaller._run_git()` / `_run_git_capture()` | High | 核心 git 交互层，install/uninstall/update 依赖 |
| `local/installer.py` | `GitInstaller._create_venv()` / `_run_uv()` | Medium | Agent 安装 venv 创建 |
| `local/supervisor.py` | `AgentSupervisor._resolve_package_name()` 等 4 个方法 | Medium | Agent 启动时的包解析路径 |

### P1 — 中等优先级缺口

| Module | Gap | Notes |
|--------|-----|-------|
| `config/loader.py` | `ConfigLoader.invalidate_cache()` | 缓存失效行为无测试 |
| `config/loader.py` | `ConfigLoader.load_cli_routing()` | CLI 路由配置加载 |
| `config/templates.py` | `load_routing_config()` | 路由配置模板 |
| `config/model_db.py` | `ModelDBClient._do_get()` HTTP 5xx 路径 | 网络异常未覆盖 |
| `agency/dag_dispatcher.py` | `_cleanup_stale_tasks` / `_fail_orphaned_pending` | 清理路径 |
| `agency/task_composer.py` | `_check_deadline` | 超时强制逻辑 |
| `evolution/store.py` | `get_skill_records_batch` / `get_children` | 批量查询路径 |
| `evolution/compaction.py` | `should_alert` / `reset_consecutive_count` | CompactionGuard 边缘 |
| `gateway/external_mcp_adapter.py` | `is_alive` 连接超时路径 | 外部 MCP 断连检测 |
| `orchestration/task_graph.py` | `clear()` 数据清理 | 无直接测试 |

### P2 — 低优先级改进

| Module | Gap |
|--------|-----|
| `models/_common.py` | `FrozenModel` 序列化边缘 |
| `agency/json_parse.py` | 深度嵌套畸形 JSON |
| `agency/prompt_loader.py` | 缺失模板变量异常 |
| `agency/parser.py` | BOM / 编码问题 |

### v2 报告 P0 修复状态

| v2 P0 条目 | 当前状态 |
|---|---|
| `ModelDBClient.close()` | **[FIXED]** — `test_model_db_unit.py` 22 tests |
| `ModelDBClient._trigram_candidates()` | **[FIXED]** — `test_model_db_unit.py` |
| `ModelDBClient._load_disk_index()` 损坏路径 | **[FIXED]** — `test_model_db_unit.py` |
| `HookExecutor.close()` | **[FIXED]** — `test_executor_unit.py` + `test_hooks_lifecycle_e2e.py` |
| `ProcessManager._force_kill_and_reap()` | **[FIXED]** — `test_process_manager_unit.py` |
| `ProcessManager._cleanup_dead()` | **[FIXED]** — `test_process_manager_unit.py` |
| `AgentSupervisor.auto_restart_dead()` | **[FIXED]** — `test_supervisor_unit.py` |
| `PermissionChecker.check_command()` shell 注入 | **[FIXED]** — `test_permission_checker_unit.py` |

---

## 3. Redundancy Analysis

### 3.1 无断言测试 — 最大发现（NEW）

**176 个测试函数包含零 `assert` 语句且不使用 `pytest.raises`。** 它们设置场景、调用函数，但从不验证结果。这是本次审计最严重的冗余发现。

**Top 10 文件**：

| File | Count | Nature |
|------|-------|--------|
| `test_process_manager.py` | 13 | 调用方法但未验证返回值 |
| `cli/test_runtime_cmd.py` | 19 | CLI 集成测试仅隐式检查退出码 |
| `integration/test_ipc_e2e.py` | 19 | IPC 调用无结果断言 |
| `test_ipc.py` | 17 | IPC 传输无断言 |
| `test_router_module.py` | 7 | Router 无断言 |
| `integration/test_runtime_deep.py` | 11 | Runtime 深度测试无断言 |
| `test_gateway_module.py` | 7 | Gateway 无断言 |
| `cli/test_evolution_cmd.py` | 12 | CLI 命令无断言 |
| `test_hook_executor.py` | 6 | Hook 解析无断言 |
| `test_importer.py` | 8 | Importer 无断言 |

**典型案例**：
- `test_process_manager.py:130` `test_start_agent_registers_handle` — 调用 `start_agent` 但从未验证 handle 是否注册
- `test_ipc.py:93` `test_send_calls_drain` — 名为"测试 send 调用 drain"但从未验证 drain 被调用
- `test_gateway_module.py:839` `test_run_stdio` — 未对 stdio 模式做任何断言

**小计: ~176 个无断言测试（估计 ~120-150 个真正可删除，部分可能通过 caplog 等间接机制验证）**

### 3.2 同义反复测试（Mock 返回值 == Mock 返回值）

v2 的 49 个同义反复测试中，45 个已在 iteration 7 删除。剩余：

| File | Count | Pattern |
|------|-------|---------|
| `test_token_counter_enhanced.py` | 8 | `mock.return_value = 42; assert result == 42` |
| `test_cli_module.py` | 4 | mock 命令构建后断言构建参数 |
| `test_local_supervisor.py` | 1 | `sup.list_running() == ["a", "b"]` 来自 mock |
| `test_evolution_health.py::test_store_returns_underlying_store` | 1 | `assert checker.store is store` |

**小计: ~14 个同义反复测试**

### 3.3 重复测试文件对

#### 3.3.1 Config 三重奏（最严重的重复）

| File | Tests | Overlap |
|------|-------|---------|
| `test_config.py` | 45 | ConfigLoader + model string + API key |
| `test_config_loader.py` | 28 | ConfigLoader project/global merge |
| `config/test_loader.py` | 58 | ConfigLoader caching/servers/stages |

**重叠行为被 3 次测试**：
- 空/缺失配置 → 3 个文件各写 1 个测试
- invalid_api_type 默认值 → 3 个文件各写 1 个测试
- sources 解析 → ~7 + ~15 + ~7 = 29 个测试测同一逻辑
- provider 合并 → 3 个文件各写 1 个测试
- TOML 错误 → 3 个文件各写 1 个测试

**建议**: 合并为单一 `config/test_loader.py`，`test_config.py` 仅保留 API key + model tier 逻辑。

**估计冗余: ~15-20 tests**

#### 3.3.2 Permission Checker 重复

| File | Tests | Focus |
|------|-------|-------|
| `test_permission_checker.py` | 75 | 宽泛：check_tool, check_path, check_command |
| `test_permission_checker_unit.py` | 21 | 仅 check_command 深度测试 |

**9 个共享概念被两个文件重复测试**：empty_command, whitespace, default_mode, plan_mode, full_auto, substring_false_positive, denied_command 等。

**估计冗余: ~15 tests（_unit 文件中的 15/21 是重复）**

#### 3.3.3 Process Manager 重复

| File | Tests | Focus |
|------|-------|-------|
| `test_process_manager.py` | 75 | 公开 API |
| `test_process_manager_unit.py` | 19 | 内部方法 |

约 10 个测试在两个文件中测试相同行为（cleanup_dead, force_kill, wait_for_exit 等），仅调用路径不同（公开 API vs 私有方法）。`_unit` 文件中 6 个 env-building 测试是唯一的。

**估计冗余: ~10 tests**

#### 3.3.4 其他重复对

| Pair | Redundant | Nature |
|------|-----------|--------|
| `test_gateway_module.py` vs `test_gateway_tool_adapter.py` | ~5 | 同一 adapter 在不同层级测试 |
| `test_evolution_health.py` vs `test_evolution_analyzer.py` | ~3-4 | dedup/healthy skill 逻辑重叠 |
| `test_llm_planner.py` vs `test_llm_planner_structured.py` | ~5-7 | fallback/parse 路径重复 |

**小计: ~13-16 tests**

### 3.4 Pydantic/Enum/Stdlib 框架测试

| Category | Count | Value |
|----------|-------|-------|
| Pydantic frozen/constructor/serialization 测试 | ~103 | 重构时轻微变更检测 |
| Enum 值测试 | ~35 | 零回归保护 |
| Stdlib 行为测试 (str.replace, str.split) | ~21 | 零项目价值 |

**小计: ~159 个框架测试（不建议删除，但不应计入有效覆盖率）**

### 3.5 冗余总计

| Category | Count | Severity | Action |
|----------|-------|----------|--------|
| **无断言测试** | **~176** | **Critical** | 逐个审查，修复或删除 |
| 同义反复测试 (mock==mock) | ~14 | High | 删除或重写 |
| 重复文件对 | ~53-61 | High | 合并重复 |
| Pydantic/Enum/Stdlib 框架测试 | ~159 | Low | 标记为低价值 |
| **可操作总计** | **~243-251** | | **~130-170 tests 可立即处理** |

---

## 4. E2E Test Quality Assessment (Cycle 3)

### 4.1 分类标准

- **TRUE_E2E**: 真实进程/文件/SQLite/网络，跨越多个子系统边界
- **INTEGRATION**: 真实组件 + stub executor，测真实逻辑路径
- **MISCLASSIFIED**: 应为 unit 测试

### 4.2 分类结果

| Classification | Count | Files |
|---|---|---|
| **TRUE_E2E** | **14** (70%) | hooks_lifecycle, config, dsl_toml, evolution(×3), ipc_real_subprocess, ipc_async_safety, process_manager(×2), runtime(×2), task_graph(×2) |
| **INTEGRATION** | **3** (15%) | agency_pipeline, dag_dispatcher, agency |
| **MISCLASSIFIED** | **3** (15%) | gateway, cli_backend, ipc_mcp_contract |

### 4.3 逐文件分析

#### TRUE_E2E (14 files) — 零 mock，真实资源

| File | Asserts | Real Resources |
|------|---------|---------------|
| `test_hooks_lifecycle_e2e.py` | 32 | 真实子进程 (echo/false), 真实 HTTP 客户端 |
| `test_config_e2e.py` | 30 | 真实 TOML 文件 I/O + 缓存失效 |
| `test_dsl_toml_e2e.py` | 22 | 真实 TOML 解析 + SQLite TaskGraph |
| `test_evolution_e2e.py` | 11 | 真实 SQLite |
| `test_evolution_lifecycle_e2e.py` | 37 | 真实 SQLite + EvolutionEngine 生命周期 |
| `test_evolution_async_safety_e2e.py` | 13 | 真实并发 SQLite |
| `test_ipc_real_subprocess_e2e.py` | 14 | 真实 OS 进程 + pipe IPC |
| `test_ipc_async_safety_e2e.py` | 18 | 真实进程 + 并发 pipe |
| `test_process_manager_async_safety_e2e.py` | 34 | 真实子进程 + SIGTERM |
| `test_process_manager_cancel_e2e.py` | 118 | 真实进程 + 信号处理 + 取消传播 |
| `test_runtime_e2e.py` | 9 | 真实 Python 执行 (IPythonExecutor) |
| `test_runtime_security_e2e.py` | 72 | 真实 AST 分析 + 安全绕过尝试 |
| `test_task_graph_async_safety_e2e.py` | 32 | 真实并发 SQLite |
| `test_task_graph_concurrent_e2e.py` | 24 | 真实 batch + DAG + cycle 检测 |

#### INTEGRATION (3 files) — 真实组件 + stub executor

| File | Mocks (strict) | Asserts | Notes |
|------|---------------|---------|-------|
| `test_agency_pipeline_e2e.py` | 0 | 131 | v2 报告 64 mocks 已不准确，当前 0 个 strict mocks |
| `test_dag_dispatcher_e2e.py` | 0 | 29 | v2 报告 59 mocks 已不准确，当前 0 个 strict mocks |
| `test_agency_e2e.py` | 0 | 106 | 真实 DAGDispatcher + TaskGraph + Importer |

> v2 报告中 `test_agency_pipeline_e2e.py` (64 mocks) 和 `test_dag_dispatcher_e2e.py` (59 mocks) 的计数对当前代码已不准确。两个文件均已清理，仅使用 deterministic stub function 替代 LLM executor，无 MagicMock/AsyncMock/patch。

#### MISCLASSIFIED (3 files) — 应降级为 unit/integration

| File | Strict Mocks | Asserts | Issue | Recommendation |
|------|-------------|---------|-------|----------------|
| `test_gateway_e2e.py` | 9 | 25 | 16/17 测试为纯 unit 测试 | **降级为 unit**，仅保留 adapter 错误传播测试 |
| `test_cli_backend_e2e.py` | 52 | 16 | @patch subprocess, 52 mock refs | **降级为 integration** |
| `test_ipc_mcp_contract_e2e.py` | 3 | 57 | 纯 Pydantic 序列化测试 | **降级为 unit** |

**test_gateway_e2e.py 详细问题**：
- `TestDeferredRegistryE2E` (7 tests): 使用 MagicMock ProcessManager，测试 dict 查询
- `TestDeferredRegistryToolLifecycle` (4 tests): mock 注入，测试 tool 注册/移除
- `TestMcpToolAdapterContract` (6 tests): 纯 string formatting + dict 结构测试
- 仅 `test_execute_on_dead_process_returns_error` 有集成特征

**test_ipc_mcp_contract_e2e.py 详细问题**：
- 33 个测试全部验证 Pydantic model 序列化/字段验证
- 无真实进程、数据库或网络连接
- 应重命名为 `test_ipc_models_contract.py` 移至 unit/

### 4.4 需要新增的 E2E 场景

| Priority | Scenario | Description |
|----------|----------|-------------|
| **P0** | Gateway 完整工具调用流 | 注册 agent → 启动子进程 → 发现 tools → MCP 调用 → 获取结果 → 清理 |
| **P0** | Router composite 4-phase 流 | 加载 DSL → 创建 TaskGraph → 用 echo agent 执行 → 聚合结果 |
| **P1** | External MCP adapter 真实连接 | 连接 stdio MCP server → 发现 tools → 调用 → 断开 |
| **P1** | CLI init + install + run 流 | agent-nexus init → install → run 完整流程 |
| **P2** | Evolution 完整周期 | 种子 skills → 分析 → 进化 → 推广 → 验证推广后可用 |

---

## 5. Module-by-Module Coverage Summary

| Module | Public Symbols | Covered | Gaps | Coverage | Priority |
|--------|---------------|---------|------|----------|----------|
| **hooks/** | 6 | 6 | 0 | 100% | Done |
| **skills/** | 16 | 16 | 0 | 100% | Done |
| **runtime/** | ~50 | ~48 | 2 | 96% | P2 |
| **models/** | ~95 | ~90 | 5 | 95% | P2 |
| **orchestration/** | ~75 | ~70 | 5 | 93% | P1 |
| **gateway/** | ~65 | ~60 | 5 | 92% | P1 |
| **evolution/** | ~110 | ~100 | 10 | 91% | P1 |
| **router/** | ~20 | ~18 | 2 | 90% | P1 |
| **agency/** | ~120 | ~105 | 15 | 88% | P1 |
| **config/** | 44 | 38 | 6 | 86% | P0 |
| **local/** | ~50 | ~42 | 8 | 84% | P0 |

---

## 6. Priority Actions (Cycle 3)

### P0 — 立即行动（零/低回归风险）

| # | Action | Impact | Est. Savings |
|---|--------|--------|-------------|
| 1 | 审查 176 个无断言测试：添加断言或删除 | 最大单笔冗余清理 | ~120-150 tests |
| 2 | 删除 14 个同义反复测试 | mock==mock 清理 | ~14 tests |
| 3 | 降级 test_gateway_e2e.py 为 unit | 准确分类 | 17 tests 重分类 |
| 4 | 降级 test_ipc_mcp_contract_e2e.py 为 unit | 准确分类 | 33 tests 重分类 |

### P1 — 中等优先级

| # | Action | Impact |
|---|--------|--------|
| 1 | 合并 config 三重奏为单一文件 | ~15-20 tests 冗余消除 |
| 2 | 去重 test_permission_checker_unit.py 与 test_permission_checker.py | ~15 tests 合并 |
| 3 | 去重 test_process_manager_unit.py 与 test_process_manager.py | ~10 tests 合并 |
| 4 | 新增 EvolutionStore.get_metrics/deactivate_skill 测试 | 关键路径覆盖 |
| 5 | 新增 GitInstaller._run_git/_create_venv 测试 | 安装可靠性 |
| 6 | 降级 test_cli_backend_e2e.py 为 integration | 准确分类 |

### P2 — 长期改进

| # | Action |
|---|--------|
| 1 | 标记 159 个 Pydantic/Enum/Stdlib 测试为 `@pytest.mark.low_value` |
| 2 | 创建 1-2 个真正的 Gateway + Router E2E 测试 |
| 3 | 添加 evolution 并发写入竞争测试 |
| 4 | 合并 test_llm_planner.py + test_llm_planner_structured.py |

---

## 7. Risk Assessment

### High-Risk Untested Paths (残余)

1. **EvolutionStore.get_metrics** — 聚合逻辑被 HealthChecker/Promoter 消费，错误聚合会导致进化决策偏差
2. **GitInstaller._run_git** — 核心 git 交互，install/uninstall/update 的唯一路径
3. **AgentSupervisor 包解析** — 4 个无测试方法决定 agent 能否启动

### No-Assertion Tests (新发现最高风险)

176 个无断言测试意味着 **~3.7% 的测试套件不验证任何行为**。这些测试：
- 在 CI 中永远通过（green-washing）
- 掩盖潜在回归（代码变更不会导致测试失败）
- 给人虚假的安全感

最严重的文件是 `test_process_manager.py`（13 个）和 `cli/test_runtime_cmd.py`（19 个），因为它们涉及进程安全和 CLI 关键路径。

---

## 8. Iteration History

| Iteration | Action | Tests Affected |
|-----------|--------|---------------|
| v1 (Iter 1) | 初始覆盖分析 | Baseline: 4,437 tests |
| v2 (Iter 4) | 深度审计：修正误判、冗余分类 | Identified ~96 removable |
| Iter 7 | 删除 47 冗余测试 + 新增 128 P0 测试 | +71 net (4,437→4,508→4,778) |
| Cycle 3 (Iter 11) | 全量重分析：无断言测试、E2E 真实度、API 覆盖 | Identified ~243-251 actionable |

---

*Report generated by Serena LSP + 3 parallel Explore agents. All findings cross-verified against current codebase state (post iteration 7-10 changes). E2E mock counts re-verified from source.*
