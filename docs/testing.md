# Agent Nexus 测试全景文档

> 最后更新: 2026-05-13 | 测试总数: **4141** (pytest --co 全量) | 全量运行耗时: ~22s

---

## 目录

- [总览](#总览)
- [测试基础设施](#测试基础设施)
- [测试分层](#测试分层)
- [平台测试详情](#平台测试详情)
- [Agent 测试详情](#agent-测试详情)
- [已知短板](#已知短板)
- [运行指南](#运行指南)
- [编写约定](#编写约定)

---

## 总览

```
┌─────────────────────────────────────────────────────┐
│  Agent Nexus 测试全景                                │
├──────────────┬──────────┬──────────┬────────────────┤
│  分类        │ 测试数   │ 占比     │ 耗时           │
├──────────────┼──────────┼──────────┼────────────────┤
│  平台 Unit   │ 3,498    │ 74.5%    │ ~14s           │
│  平台 Integ  │ 159      │ 3.4%     │ ~3s            │
│  平台 E2E    │ 549      │ 11.7%    │ ~4s            │
│  平台 Cap    │ 80       │ 1.7%     │ <1s            │
│  Agent Tests │ 856      │ 17.1%    │ ~1s            │
├──────────────┼──────────┼──────────┼────────────────┤
│  合计        │ ~5,000   │ 100%     │ ~22s           │
└──────────────┴──────────┴──────────┴────────────────┘
```

---

## 测试基础设施

### 工具链

| 工具 | 版本要求 | 用途 |
|------|---------|------|
| pytest | >=8.0 | 测试框架 |
| pytest-asyncio | >=0.23 | 异步测试支持（`asyncio_mode = "auto"`） |
| pytest-cov | >=5.0 | 覆盖率报告 |
| pytest-timeout | >=2.2 | 单测试超时保护 |
| pytest-xdist | >=3.5 | 并行执行（可选） |

### 配置 (pyproject.toml)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "unit: fast, isolated unit tests (no I/O, no network)",
    "integration: tests spanning multiple modules with real I/O",
    "e2e: end-to-end subsystem tests (runs by default, no external services)",
    "capability: agent capability contract tests",
    "capability_release: release acceptance tests (requires --run-release)",
    "requires_api: requires real API/network access (needs --run-e2e)",
]
```

### Marker 自动注入

每个测试目录的 `conftest.py` 自动为目录内测试打 marker：

| 目录 | 自动 Marker | 行为 |
|------|------------|------|
| `tests/unit/` | `@pytest.mark.unit` | conftest.py `pytest_collection_modifyitems` |
| `tests/integration/` | `@pytest.mark.integration` | 同上 |
| `tests/e2e/` | `@pytest.mark.e2e` | 默认执行；`requires_api` 标记的测试需 `--run-e2e` |

### 额外 Markers

| Marker | 用途 | 行为 |
|--------|------|------|
| `@pytest.mark.capability` | Agent 能力契约测试 | 验证 Agent 黑盒端到端行为 |
| `@pytest.mark.capability_release` | 发版验收测试 | 需要 `--run-release` 才执行 |
| `@pytest.mark.requires_api` | 需要真实 API 密钥 | 默认跳过，需要 `--run-e2e` 才执行（CI 中跳过） |

### 关键 Fixtures

| Fixture | Scope | 位置 | 用途 |
|---------|-------|------|------|
| `tmp_db` | function | `tests/conftest.py` | 临时 SQLite 数据库路径 |
| `task_graph` | function | `tests/conftest.py` | 基于 `tmp_db` 的新 TaskGraph 实例 |
| `_shared_executor` | **session** | `tests/conftest.py` | 全局共享 IPythonExecutor（避免创建 40+ shell，每个 50-200MB） |
| `_shared_runtime` | session | `tests/conftest.py` | 全局共享 Runtime 实例 |

---

## 测试分层

```
tests/
  conftest.py              # 共享 fixtures（tmp_db, task_graph, session-scoped executor）
  unit/                    # ~140 test files — 纯内存，无 I/O，无网络
    conftest.py            # 自动打 unit marker
    agents/                # Agent 相关单元测试
    cli/                   # 60 tests — CLI 子命令测试
      conftest.py
      test_init_cmd.py
      test_config_cmd.py
      test_runtime_cmd.py
      test_evolution_cmd.py
      test_shared.py
    config/                # 配置模块测试
    evolution/             # 进化引擎测试
    gateway/               # 网关测试
    hooks/                 # 钩子测试
    local/                 # 本地管理测试
    models/                # 数据模型测试
    platform/              # 平台集成测试
    test_agent_models.py
    test_cli_module.py
    test_config*.py         # 4 files
    test_context*.py        # 2 files
    test_describer.py
    test_distribution_models.py
    test_dsl.py
    test_evolution_*.py     # 9 files — 进化引擎全模块覆盖
    test_executor.py
    test_gateway*.py        # 2 files
    test_hook*.py           # 2 files
    test_ipc*.py            # 2 files
    test_local_*.py         # 4 files
    test_orchestration_pipeline.py
    test_permission*.py     # 2 files
    test_process_manager.py
    test_router*.py         # 3 files
    test_runtime*.py        # 2 files
    test_security*.py       # 2 files
    test_skills*.py         # 3 files
    test_task_*.py          # 3 files
    test_token_tracker.py
  integration/              # 11 test files — 跨模块，真实 I/O
    test_agency_evolution_hook.py
    test_agency_hooks_token_context.py
    test_agency_pipeline.py
    test_cli_backend_integration.py
    test_composition_e2e.py
    test_ipc_e2e.py
    test_llm_pipeline_e2e.py
    test_mcp_ecosystem_integration.py
    test_reasoning_protocol_integration.py
    test_reflect_loop.py
    test_runtime_deep.py
  e2e/                      # ~25 test files — 默认执行；requires_api 标记需 --run-e2e
    conftest.py
    test_a2a_messaging_e2e.py
    test_agency_e2e.py
    test_agency_pipeline_e2e.py
    test_agency_pipeline_true_e2e.py
    test_config_e2e.py
    test_dag_dispatcher_e2e.py
    test_dsl_toml_e2e.py
    test_evolution_async_safety_e2e.py
    test_evolution_e2e.py
    test_evolution_engine_lifecycle_e2e.py
    test_evolution_lifecycle_e2e.py
    test_experimenter_persistence_e2e.py
    test_gateway_hotreload_e2e.py
    test_gateway_lifecycle_e2e.py
    test_gateway_security_e2e.py
    test_gateway_tool_call_e2e.py
    test_hooks_lifecycle_e2e.py
    test_ipc_async_safety_e2e.py
    test_ipc_real_subprocess_e2e.py
    test_process_manager_async_safety_e2e.py
    test_process_manager_cancel_e2e.py
    test_runtime_e2e.py
    test_runtime_security_e2e.py
    test_task_graph_async_safety_e2e.py
    test_task_graph_concurrent_e2e.py

agents/
  atomic/*/tests/           # 614 tests
  composite/*/tests/        # 242 tests
```

---

## 平台测试详情

### 按文件分布 (Top 20)

| 文件 | 测试数 | 覆盖模块 |
|------|--------|---------|
| test_evolution_module.py | 207 | evolution 全模块集成 |
| test_local_module.py | 180 | local/ 全模块（installer, lockfile, sources, supervisor, cli） |
| test_router_module.py | 132 | router/ 全模块 |
| test_gateway_module.py | 120 | gateway + deferred_registry |
| test_permission_checker.py | 106 | runtime/permission_checker |
| test_security_checker.py | 89 | runtime/security_checker |
| test_evolution_store.py | 88 | evolution/store |
| test_agent_models.py | 79 | models/agent |
| test_process_manager.py | 75 | orchestration/process_manager |
| test_context_models.py | 70 | models/context |
| test_cli_module.py | 69 | local/cli 顶层命令 |
| test_dsl.py | 68 | orchestration/dsl |
| test_task_graph.py | 67 | orchestration/task_graph |
| test_ipc.py | 65 | orchestration/ipc |
| test_evolution_models.py | 65 | models/evolution |
| test_skills.py | 61 | skills/ 全模块 |
| test_runtime.py | 58 | runtime/runtime |
| test_distribution_models.py | 58 | models/distribution |
| test_executor.py | 52 | runtime/executor |
| test_hooks_models.py | 46 | models/hooks |

### 按架构层分布

| 架构层 | 测试文件数 | 测试数 | 占平台测试比 |
|--------|-----------|--------|------------|
| Models (数据模型) | 9 | ~470 | 17.4% |
| Evolution (自进化) | 9 | ~540 | 20.0% |
| Runtime (运行时) | 7 | ~390 | 14.4% |
| Orchestration (编排) | 5 | ~284 | 10.5% |
| Gateway (网关) | 2 | 140 | 5.2% |
| Router (路由) | 3 | 180 | 6.7% |
| Local (本地管理) | 6 | ~370 | 13.7% |
| Config (配置) | 4 | ~110 | 4.1% |
| Skills (技能) | 3 | 94 | 3.5% |
| Hooks (钩子) | 2 | 90 | 3.3% |
| CLI 子命令 | 5 | 60 | 2.2% |

### 最慢测试 (Top 5)

| 测试 | 耗时 | 原因 |
|------|------|------|
| test_execute_fails_if_old_thread_never_finishes | 5.02s | 等待线程超时自然结束 |
| test_timeout (teardown) | 2.70s | IPython shell 清理 |
| test_recovery_after_timeout_reset | 2.51s | 真实 IPython 超时 + 恢复 |
| test_io_bound_sleep_timeout (teardown) | 2.50s | 同上 |
| test_timeout_sets_flag | 2.01s | 等待超时触发 |

这些慢测试全部集中在 `test_executor.py` 和 `test_runtime_deep.py`，属于 executor 超时行为验证，时间消耗不可避免。

---

## Agent 测试详情

### Atomic Agent 测试 (614)

| Agent | 测试数 | 覆盖内容 |
|-------|--------|---------|
| contract-analyzer | 85 | 条款提取、风险分析、合规检查 |
| market-intelligence-analyst | 75 | 市场分析、趋势识别、简报生成 |
| test-suite-generator | 71 | AST 解析、测试用例生成、套件构建 |
| accessibility-auditor | 68 | HTML 检查、内容审计、修复建议 |
| security-scanner | 68 | 代码扫描、报告生成、依赖检查 |
| requirements-analyzer | 67 | 需求分析、问题生成、规格构建 |
| localization-specialist | 64 | 文本分析、术语管理、本地化 |
| doc-filler | 58 | 模板分析、模板填充 |
| api-doc-generator | 58 | 端点提取、Schema 推断、OpenAPI 生成 |
| code-reviewer | **0** | 无测试文件 |
| good-skill | **0** | 自进化晋升产物，模板 Agent |

### Composite Agent 测试 (242)

| Agent | 测试数 | 覆盖内容 |
|-------|--------|---------|
| product-documentation-suite | 57 | 并行编排 + 顺序聚合 |
| document-compliance-gateway | 49 | 全并行 + 冲突检测 |
| competitive-intelligence-briefing | 47 | 顺序链编排 |
| feature-delivery-pipeline | 45 | 顺序 -> 并行编排 |
| cicd-quality-gate | 44 | 全并行质量关卡 |

---


## 能力契约测试

> 详见 [capability-testing.md](capability-testing.md)

能力测试（`tests/capabilities/`，80 个测试）验证 Agent 作为黑盒的端到端行为，采用契约驱动架构：

- **三层能力**: 20 Atomic + 5 Composite + 1 Agency Pipeline
- **两种模式**: CLI（subprocess）+ API（LLMClient 真实调用）
- **两级验证**: CI Gate（结构断言）+ Release（语义质量）

```bash
uv run pytest tests/capabilities/ -v                          # CI 快速门禁
uv run pytest tests/capabilities/ --run-release --run-api -v  # 发版前全量
```

## 已知短板

### 无测试的模块

| 模块 | 状态 | 说明 |
|------|------|------|
| `local/cli/create_cmd.py` | 缺失 | 新增的 `agent-nexus create agent` 脚手架命令，尚无专属测试 |
| `code-reviewer` (Agent) | 缺失 | Atomic Agent 无 tests/ 目录 |
| `good-skill` (Agent) | 缺失 | 自进化晋升产物，模板代码，优先级低 |
| `gateway/deferred_registry.py` | 间接覆盖 | 通过 `test_gateway_module.py` 测试，无专属测试文件 |

### 改进建议

1. **create_cmd 测试** — 高优先级。scaffold_agent 的文件生成、wizard 交互、参数校验都需要覆盖
2. **code-reviewer Agent 测试** — 中优先级。10 个 Atomic Agent 中唯一无测试的正式 Agent
3. **E2E 测试** — `requires_api` 标记的测试默认跳过（需 `--run-e2e`），其余 E2E 测试默认执行

---

## 运行指南

### 基本命令

```bash
# 全量测试（平台 + Agent）
uv run pytest tests/ agents/ -v

# 仅平台测试
uv run pytest tests/ -v

# 仅 Agent 测试
uv run pytest agents/ -v

# 单个模块
uv run pytest tests/unit/test_evolution_module.py -v

# 单个 Agent
uv run pytest agents/atomic/doc-filler/ -v
```

### 按分类运行

```bash
# Unit only
uv run pytest tests/unit/ -v

# Integration only
uv run pytest tests/integration/ -v

# E2E（全部运行；requires_api 测试需 --run-e2e）
uv run pytest tests/e2e/ -v

# 排除慢测试
uv run pytest tests/ agents/ -v -m "not slow" --timeout=60
```

### 覆盖率

```bash
# HTML 覆盖率报告
uv run pytest tests/ --cov=agent_nexus --cov-report=html

# 终端概览
uv run pytest tests/ --cov=agent_nexus --cov-report=term-missing
```

### 并行执行

```bash
# 自动并行（利用多核）
uv run pytest tests/ agents/ -v -n auto
```

---

## 编写约定

### 测试命名

```
tests/
  unit/
    test_<module_name>.py      # 对应 src/agent_nexus/platform/<layer>/<module_name>.py
    cli/
      test_<cmd_name>_cmd.py   # 对应 local/cli/<cmd_name>_cmd.py
  integration/
    test_<feature>_e2e.py      # 端到端集成测试
agents/
  <type>/<agent-name>/tests/
    test_<agent_name>.py       # Agent 包测试
```

### 类组织

每个测试文件按 `TestClass` 组织，一个类对应被测类或功能组：

```python
class TestTaskGraph:
    """TaskGraph 核心功能测试"""

    def test_add_task(self): ...
    def test_cycle_detection(self): ...

class TestTaskGraphConcurrency:
    """TaskGraph 并发安全测试"""

    def test_concurrent_writes(self): ...
```

### 异步测试

`asyncio_mode = "auto"` — 无需显式标记，`async def test_*` 自动被 pytest-asyncio 识别：

```python
async def test_async_operation():
    result = await some_async_call()
    assert result == expected
```

### Mock 原则

- **IPC mock**：`read`/`readline` 必须返回 `b""` 以避免无限循环
- **IPython**：使用 session-scoped `_shared_executor` fixture，不创建 per-test shell
- **进程 mock**：`asyncio.create_subprocess_exec` 使用 `AsyncMock`，不启动真实进程

### 性能底线

- 单个测试不超过 5s（timeout 保护）
- 全量测试不超过 60s（当前 ~33s）
- 不创建不可终止的线程
- 不创建 per-test IPython shell（每个 50-200MB）
