# Test Audit Report: Gap & Redundancy Analysis (Deep Audit v2)

**Date**: 2026-05-09
**Branch**: nf/serena-using-superpo-ff6f4b
**Auditor**: Automated analysis via Serena LSP + Explore agents + grep/glob
**Iteration**: 4 (深度审计 v2，修正 v1 的误判，新增冗余/同义反复分析)

---

## 1. Overall Statistics

| Metric | Count |
|--------|-------|
| Source modules (non-init) | 99 |
| Public classes | 219 |
| Public functions | 76 |
| **Total public symbols** | **295** |
| Test files (excl. conftest/__init__) | 167 |
| Unit test files | 130 |
| E2E test files | 21 |
| Integration test files | 9 |
| Capability test files | 6 |
| **Total test functions** | **4,437** |

---

## 2. Gap Analysis: Untested Public Symbols (P0-P2)

### P0 — Critical Gaps (Zero test coverage for key public methods)

| Module | Symbol | Risk | Notes |
|--------|--------|------|-------|
| `config/model_db.py` | `ModelDBClient.close()` | High | 资源泄漏风险：httpx.AsyncClient 未关闭 |
| `config/model_db.py` | `ModelDBClient._trigram_candidates()` | Medium | 核心模糊搜索算法无任何单元测试 |
| `config/model_db.py` | `ModelDBClient._build_search_index()` | Medium | 搜索索引构建无测试 |
| `config/model_db.py` | `ModelDBClient._load_disk_index()` | Medium | 磁盘缓存损坏路径无测试 |
| `hooks/executor.py` | `HookExecutor.close()` | High | async 资源清理无测试 |
| `gateway/gateway.py` | `MCPGateway._setup_core_tools()` | High | MCP 核心工具注册的引导路径，失败会导致静默启动 |
| `gateway/gateway.py` | `MCPGateway._check_agent_health()` | Medium | 健康检查无直接测试 |
| `gateway/gateway.py` | `MCPGateway._is_stale_registration()` | Medium | 过期注册检测无测试 |
| `orchestration/process_manager.py` | `ProcessManager._force_kill_and_reap()` | High | 僵尸进程清理，安全关键路径 |
| `orchestration/process_manager.py` | `ProcessManager._cleanup_dead()` | High | 死亡 agent 回收 |
| `local/supervisor.py` | `AgentSupervisor.auto_restart_dead()` | High | 自动重启逻辑 |
| `local/supervisor.py` | `AgentSupervisor._find_dead_agents()` | Medium | 死亡 agent 检测 |

### P1 — Modules with Weak Boundary/Exception Testing

| Module | Gap | Notes |
|--------|-----|-------|
| `config/loader.py` | `invalidate_cache()` | 缓存失效行为无测试 |
| `config/model_db.py` | 网络超时 / DNS 失败 / SSL 错误 | `_do_get` 的 HTTP 5xx 路径无测试 |
| `config/model_db.py` | 磁盘缓存损坏 (malformed JSON) | `_load_disk_index` 无异常路径测试 |
| `config/model_db.py` | 磁盘缓存并发访问 | 多个 ModelDBClient 实例共享同一缓存路径 |
| `evolution/store.py` | 并发 SQLite 写入竞争 | Store 跨线程共享，无竞争测试 |
| `evolution/context_describer.py` | `_build_judgment_history` 深层边缘 | 空 judgments / 超长列表 / 缺字段 |
| `orchestration/ipc.py` | `IPCStream.receive()` 超大消息 | `_MAX_MESSAGE_SIZE` 执行路径 |
| `orchestration/task_graph.py` | `detect_cycles()` 复杂菱形依赖 | 仅简单循环有测试 |
| `runtime/permission_checker.py` | `check_command()` shell 注入 | 危险命令模式无测试 |
| `runtime/security_checker.py` | `clear_cache()` | 缓存失效未测试 |
| `agency/dag_dispatcher.py` | `_dispatch_sequential()` | 顺序 vs 并行分发分支 |
| `agency/llm_client.py` | `_call_cli()` 非零退出码 | CLI 后端失败处理 |
| `skills/loader.py` | `load_agent_skills()` 权限拒绝 | EACCES OSError 路径 |
| `gateway/gateway.py` | `_make_external_tool_func()` 错误传播 | 外部工具调用错误未验证 |

### P2 — Nice-to-Have Coverage Improvements

| Module | Gap |
|--------|-----|
| `platform/utils.py` | `resolve_composition_path()` 符号链接 |
| `platform/utils.py` | `sqlite_connection()` WAL 模式 |
| `models/_common.py` | `FrozenModel` 序列化边缘情况 |
| `agency/json_parse.py` | `robust_json_parse()` 深度嵌套畸形 JSON |
| `agency/prompt_loader.py` | `render()` 缺失模板变量 |
| `agency/parser.py` | `parse_frontmatter()` BOM / 编码问题 |

### v1 报告修正

v1 报告中以下条目标记为"zero coverage"，经本次深度验证实际已有测试覆盖：

| v1 条目 | 实际状态 |
|---------|---------|
| `ConfigLoader.load_cli_backends()` | **已有测试**: `test_cli_backend_config_loader.py` (4+ tests), `test_cli_backend_e2e.py` (3+ tests) |
| `ConfigLoader.load_external_servers()` | **已有测试**: `config/test_loader.py` (5 tests), `test_external_mcp_adapter.py` (15+ tests) |
| `ConfigLoader.load_cli_routing()` | **已有测试**: `test_cli_backend_config_loader.py` (1 test), `config/test_loader.py` (2 tests) |
| `MCPGateway.run_stdio()` | **已有测试**: `test_gateway_module.py` (mock-based) |
| `MCPGateway.run_sse()` | **已有测试**: `test_gateway_module.py` (mock-based) |
| `MCPGateway.stop()` | **已有测试**: `test_gateway_module.py` |

---

## 3. Redundancy Analysis: Tautological / Duplicate / Low-Value Tests

### 3.1 同义反复测试（Tautological — Mock 返回值 == Mock 返回值）

测试仅验证 `mock.return_value` 通过被测方法原样返回，不验证任何项目逻辑。

| File | Test Count | Pattern | Action |
|------|-----------|---------|--------|
| `tests/unit/evolution/test_engine.py` | 29 | 每个测试替换 EvolutionEngine 所有组件为 MagicMock，断言 `result is mock.return_value` | **DELETE** — 零回归保护 |
| `tests/unit/test_evolution_engine.py` | 16 | 同上，旧版测试，与 `evolution/test_engine.py` 完全重叠 | **DELETE** — 重复且同义反复 |
| `tests/unit/test_token_counter_enhanced.py` | 3 | `_litellm_mod.token_counter.return_value = 42; assert result == 42` | **REWRITE** — 加入 litellm fallback 逻辑验证 |
| `tests/unit/test_evolution_health.py::test_store_returns_underlying_store` | 1 | `assert checker.store is store` — 验证构造函数赋值 | **DELETE** — 零价值 |

**小计: ~49 个同义反复测试**

### 3.2 重复测试文件（同一源类，多个测试文件）

| Files | Overlap | Source Module | Action |
|-------|---------|---------------|--------|
| `test_evolution_engine.py` (16) + `evolution/test_engine.py` (29) | 100% 重叠：evolve routing, check_health, diagnose_all, promote, should_compact | `EvolutionEngine` | **DELETE BOTH** — 都是同义反复 |
| `test_task_composer.py` (2) + `platform/agency/test_task_composer.py` (21) | 100% 重叠：mock wiring vs 真实集成 | `TaskComposer` | **DELETE** `test_task_composer.py` (2 tests，完全被 21 tests 覆盖) |
| `test_config.py` (45) + `test_config_loader.py` (28) | 部分重叠：都测试 `ConfigLoader.load_config()` | `ConfigLoader` | **MERGE** `test_config.py` 的 ConfigLoader 测试到 `test_config_loader.py` |

**小计: 3 对重复文件，~47 个重复/多余测试**

### 3.3 Pydantic 框架测试（测试 Pydantic 行为，非项目代码）

| Pattern | Count | Files | Value |
|---------|-------|-------|-------|
| `test_frozen*` (验证 frozen model 不可变) | ~18 | test_agent_models, test_task_models, test_distribution_models, test_hooks_models, test_context_models | 测试 Pydantic `frozen=True` |
| `test_construction`/`test_defaults`/`test_with_values`/`test_full_construction` | ~60 | 所有 model 测试文件 | 测试 Pydantic 字段赋值 |
| `test_roundtrip`/`test_serialization` (model_dump → Model(**data)) | ~25 | 所有 model 测试文件 | 测试 Pydantic 序列化 |

**注意**: 这些测试在重构 Pydantic model 时有轻微价值（变更检测），但不应计入有效测试覆盖。

**小计: ~103 个 Pydantic 框架测试**

### 3.4 Enum 枚举值测试（测试 Python enum 行为）

| Pattern | Count | Files |
|---------|-------|-------|
| `test_members` / `test_values` / `test_from_string` / `test_invalid_string_raises` | ~35 | test_agent_models, test_task_models, test_distribution_models, test_hooks_models, test_context_models, test_config_models, test_evolution_models, test_permission_models |

这些验证 `AgentType.ATOMIC == "atomic"` 等 enum 字面值。重构时 source 和 test 需同步修改，回归保护为零。

**小计: ~35 个 enum 测试**

### 3.5 Stdlib 行为测试

| File | Tests | What it tests |
|------|-------|---------------|
| `test_utils.py::TestAgentNameToPackage` | 7 | `str.replace("-", "_")` |
| `test_utils.py::TestToClassName` | 7 | `str.split("-") + str.capitalize()` |
| `test_config_model_config.py::TestParseModelString` | 3 | `str.split(":", 1)` |
| `test_local_installer.py::TestUrlToSourceName` | 4 | `rsplit("/") + removesuffix(".git")` |

**小计: ~21 个 stdlib 测试**

### 3.6 冗余总计

| Category | Count | Severity | Action |
|----------|-------|----------|--------|
| 同义反复测试 (mock==mock) | ~49 | **High** | 删除 |
| 重复文件 | ~47 | **High** | 删除重复文件 |
| Pydantic 框架测试 | ~103 | **Medium** | 保留但标记为低价值 |
| Enum 枚举值测试 | ~35 | **Low** | 保留作为变更检测 |
| Stdlib 行为测试 | ~21 | **Low** | 保留 |
| **总计** | **~255** | | **~96 tests 可立即删除** |

---

## 4. E2E Test Quality Assessment

### 4.1 真正的 E2E 测试（测试真实流程）

| Test File | What it Tests | Real E2E? |
|-----------|--------------|-----------|
| `test_runtime_e2e.py` | PythonRuntime execute/inject/retrieve 生命周期 | **YES** — 真实 IPython 内核 |
| `test_runtime_security_e2e.py` | SecurityChecker + SecurityRule 集成 | **YES** — 真实 AST 检查 |
| `test_task_graph_concurrent_e2e.py` | 并发 SQLite 写入 TaskGraph | **YES** — 真实并发 |
| `test_task_graph_async_safety_e2e.py` | Async TaskGraph 包装器 | **YES** — 真实 async SQLite |
| `test_ipc_real_subprocess_e2e.py` | 真实子进程 IPC (stdin/stdout) | **YES** — 真实进程 |
| `test_ipc_async_safety_e2e.py` | IPC stream 并发访问 | **YES** — 真实 IPC |
| `test_process_manager_async_safety_e2e.py` | ProcessManager 真实进程管理 | **YES** — 真实子进程 |
| `test_process_manager_cancel_e2e.py` | 进程取消和清理 | **YES** — 真实信号处理 |
| `test_ipc_mcp_contract_e2e.py` | IPC 消息序列化契约 | **YES** — 真实 Pydantic 验证 |
| `test_evolution_lifecycle_e2e.py` | 完整进化生命周期 (SQLite) | **YES** — 真实 SQLite |
| `test_evolution_async_safety_e2e.py` | 并发进化存储访问 | **YES** — 真实并发 |
| `test_dsl_toml_e2e.py` | TOML 解析 roundtrip | **YES** — 真实文件 I/O |
| `test_config_e2e.py` | Config 加载 + env vars + 缓存 | **YES** — 真实 TOML + 文件 I/O |

### 4.2 Mock-Heavy 的"E2E"测试（应重新分类）

| Test File | Mock 引用数 | Issue | Recommendation |
|-----------|-----------|-------|----------------|
| `test_agency_pipeline_e2e.py` | 64 | 所有 LLM 调用和 agent 执行均为 MagicMock | **降级为 integration** |
| `test_dag_dispatcher_e2e.py` | 59 | executor 完全 mock | 部分有效 — DAG 逻辑真实，executor mock 应为 unit |
| `test_gateway_e2e.py` | 12 | 3 个纯 Python 逻辑测试，其余为真实集成 | **删除 3 个同义反复测试** |
| `test_cli_backend_e2e.py` | 11 | mock subprocess 但 config-to-response 管线真实 | 可接受 |
| `test_agency_e2e.py` | 5 | importer/registry/selector 流程真实 | 可接受 |

### 4.3 需要新增的 E2E 场景

1. **Gateway 完整工具调用流** (P0): 注册 agent → 启动子进程 → 发现 tools → MCP 调用 → 获取结果 → 清理
2. **Router composite 4-phase 流** (P0): 加载 DSL → 创建 TaskGraph → 用 echo agent 执行 → 聚合结果
3. **Evolution 完整周期** (P1): 种子 skills → 分析 → 进化 → 推广 → 验证推广后 agent 可用
4. **External MCP adapter 真实连接** (P1): 连接 stdio MCP server → 发现 tools → 调用 → 断开
5. **CLI init + install + run 流** (P2): agent-nexus init → install → run 完整流程

---

## 5. Module-by-Module Coverage Summary

| Module | Public Methods | With Tests | Without Tests | Coverage | Priority |
|--------|---------------|------------|---------------|----------|----------|
| **config/** | 42 | 36 (86%) | 6 | Good，但 model_db 异常路径缺失 | **P0** |
| **gateway/** | 66 | 66 (100%) | 0 (但有 3 个私有方法无直接测试) | Strong | **P1** |
| **hooks/** | 22 | 15 (68%) | 7 | Good，lifecycle gaps | **P1** |
| **evolution/** | 118 | 118 (100%) | 0 | Excellent | **P2** |
| **skills/** | 18 | 16 (89%) | 2 | Excellent | **P2** |
| **orchestration/** | ~40 | 35 (88%) | 5 | Good | **P0** |
| **runtime/** | ~25 | 22 (88%) | 3 | Good | **P1** |
| **agency/** | ~120 | 115 (96%) | 5 | Strong | **P1** |
| **local/** | ~30 | 25 (83%) | 5 | Moderate | **P1** |
| **router/** | ~15 | 12 (80%) | 3 | Moderate | **P1** |
| **models/** | ~40 | 40 (100%) | 0 | Strong (但多数是 Pydantic 框架测试) | **P2** |

---

## 6. Immediate Actions (Priority Queue)

### P0 — 立即删除/修复（零回归风险）

| # | Action | Impact | Savings |
|---|--------|--------|---------|
| 1 | 删除 `tests/unit/evolution/test_engine.py` | 29 同义反复测试 | 311 行 |
| 2 | 删除 `tests/unit/test_evolution_engine.py` | 16 重复+同义反复测试 | ~280 行 |
| 3 | 删除 `tests/unit/test_task_composer.py` | 2 被完全覆盖的测试 | ~80 行 |
| 4 | 删除 `test_gateway_e2e.py` 中 3 个同义反复测试 | 纯 Python 逻辑测试 | ~40 行 |
| **合计** | **4 个操作** | **~50 tests, ~711 lines** | |

### P1 — 新增关键测试

| # | Test | Module | Risk Addressed |
|---|------|--------|----------------|
| 1 | `ModelDBClient.close()` 资源清理 | config/model_db | 资源泄漏 |
| 2 | `ModelDBClient._trigram_candidates()` 算法正确性 | config/model_db | 搜索质量 |
| 3 | `ModelDBClient._load_disk_index()` 损坏 JSON | config/model_db | 生产稳定性 |
| 4 | `HookExecutor.close()` async 清理 | hooks/executor | 资源泄漏 |
| 5 | `ProcessManager._force_kill_and_reap()` | orchestration | 僵尸进程 |
| 6 | `ProcessManager._cleanup_dead()` | orchestration | agent 泄漏 |
| 7 | `MCPGateway._setup_core_tools()` 引导失败 | gateway | MCP 服务启动 |
| 8 | `PermissionChecker.check_command()` shell 注入 | runtime | 安全 |
| 9 | `AgentSupervisor.auto_restart_dead()` | local/supervisor | 生产可靠性 |

### P2 — 长期改进

| # | Action |
|---|--------|
| 1 | 合并 `test_config.py` ConfigLoader 测试到 `config/test_loader.py` |
| 2 | 创建 1-2 个真正的 E2E 测试（gateway + router + echo agent） |
| 3 | 添加 evolution 并发写入竞争测试 |
| 4 | 标记 Pydantic/Enum 框架测试为 `@pytest.mark.low_value` |

---

## 7. Risk Assessment

### High-Risk Untested Paths

1. **ModelDB 资源泄漏** — `close()` 从未被调用，httpx.AsyncClient 可能泄漏
2. **Gateway 引导失败** — `_setup_core_tools()` 失败后 gateway 静默启动，无 core tools
3. **僵尸进程** — `_force_kill_and_reap()` 未测试，生产可靠性风险
4. **搜索质量** — `_trigram_candidates()` 核心算法无测试，模糊搜索可能静默退化

### Medium-Risk Areas

5. **IPC 消息大小限制** — `_MAX_MESSAGE_SIZE` 执行路径无测试
6. **Config 缓存失效** — `invalidate_cache()` 行为未验证
7. **Hook 资源清理** — `close()` async 清理无测试
8. **Config 磁盘缓存损坏** — malformed JSON 路径无测试

---

*Report generated by Serena LSP + 3 parallel Explore agents + grep/glob verification. All findings cross-verified against actual test file contents. v1 misclassifications corrected.*
