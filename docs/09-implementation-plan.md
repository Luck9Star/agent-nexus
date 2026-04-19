# 实施计划

> Agent Nexus POC v5 — §11 实施计划：7 个 Phase、风险矩阵、项目结构

## §11 实施计划

### Phase 1：自建编排基础 + 第一个 Agent（Week 1-2）

> **参考模块**: ClawTeam `clawteam/spawn/subprocess_backend.py` — ProcessManager 参考, `clawteam/store/file.py` — TaskGraph 参考; cave-agent `src/cave_agent/agent.py` — Agent 执行循环参考

目标：验证 PydanticAI Agent + 自建编排层基础

- [x] TaskGraph 实现（SQLite + blocked_by + 环检测）
- [x] ProcessManager 实现（asyncio.subprocess + 健康检查）
- [x] IPC 协议实现（stdin/stdout JSON-lines）
- [x] doc-filler Agent 实现
- [x] MCP Server 暴露测试
- [x] Local mode 测试

### Phase 2：Platform Router + 编排集成（Week 3-4）

> **参考模块**: ClawTeam `clawteam/store/file.py` — blocked_by 依赖解析参考; deer-flow `packages/harness/deerflow/subagents/executor.py` — SubagentExecutor; deer-flow `packages/harness/deerflow/agents/lead_agent/prompt.py` — Continue/Spawn 决策

目标：Platform Router 编排多个 Agent

- [x] Platform Router 实现（4-Phase Workflow）
- [x] OrchestrationDSL TOML 解析器
- [x] Continue vs Spawn 决策矩阵
- [x] Model Config 层
- [x] requirements-analyzer + doc-filler 串联测试

### Phase 3：MCP Gateway + Model Config（Week 5）

> **参考模块**: nanobot `nanobot/agent/tools/mcp.py` — MCP 客户端连接; OpenHarness `src/openharness/mcp/client.py` — `McpClientManager`

目标：统一 MCP 暴露和模型配置

- [x] MCP Gateway 实现
- [x] **T1: Agent 级 Deferred Loading** — DeferredAgentRegistry（manifest 注册 + search_and_activate）
- [x] **T2: Tiered Context Loading 基础框架** — TieredContextBuilder（L0/L1 分层注入）
- [x] **T5: Provider-Agnostic Tool Search** — AgentSearchTool MCP tool + Anthropic 原生 `defer_loading` 适配
- [x] Model Config Manager
- [x] 所有 10 个 Atomic Agent 注册
- [ ] 端到端 MCP 测试

### Phase 4：Git-based Distribution + CLI（Week 6-7）

> **参考模式**: Homebrew tap（官方 core + 用户自定义 tap）、Cargo git dependencies

目标：Agent 通过 Git 仓库分发，本地 CLI 安装/运行/管理

- [ ] **T8: Cross-Agent Data Reference** — Mailbox 引用传递格式（~50 tokens 引用 vs 全量传递）
- [x] Git Installer（SourceManager + GitInstaller：clone --sparse → validate → install）
- [x] CLI 命令（install, uninstall, update, run, list, search, info）
- [x] Lockfile 管理（lockfile.json：git source + commit SHA）
- [x] Config 管理（config.toml + sources.yaml + Provider Registry）
- [ ] SemVer 版本解析器（从 git tags 解析）
- [x] Agent Supervisor（asyncio.subprocess，健康检查，自动重启）
- [ ] 质量验证工具（manifest 检查、SKILL.md 检查、安全审计）
- [x] 5 个 Composite Agent 实现

> 详见 [§12 Git-Based Agent 分发与本地架构](10-cloud-local-architecture.md)

### Phase 5：Python Runtime + Self-Evolution（Week 8-10）

> **参考模块**: cave-agent `src/cave_agent/runtime/` — Runtime 集成, `src/cave_agent/security/` — SecurityChecker; OpenSpace `openspace/skill_engine/` — 完整进化引擎

目标：集成 CaveAgent Runtime 和 OpenSpace 进化引擎

- [x] PythonRuntime 集成到 Atomic Agent
- [x] SecurityChecker 实现
- [x] **T3: Runtime Context 分层加载** — TieredRuntimeDescriber（Type Schema L0 名称/L2 完整分离）
- [x] ExecutionAnalyzer 实现
- [x] **T4: 进化数据分层注入** — EvolutionContextDescriber（Metrics L0 / 建议 L1 / 历史 L2）
- [x] **T6: CompactionGuard** — min_turns_between_compactions=5 + 重注入限制 + context_budget_log
- [x] **T7: Context Budget 硬限制** — L0≤800, L1≤3000, bootstrap≤5000, compaction 80%→40%
- [x] FIX / DERIVED / CAPTURED 进化模式
- [x] 编排进化（TOML 优化）
- [x] Skill → Agent Promotion

### Phase 6：完善 + 测试（Week 11-12）

目标：生产就绪

- [x] SKILL.md 三层渐进式加载
- [x] SubtaskController 超时控制
- [x] 完整测试覆盖
- [ ] 文档完善

### Phase 7：Rust 重构（Week 13-20，远期）

> **参考**: §12.6 Rust 重构路径、§12.6.5 核心 Trait 定义、§10.8 Rust 重构约束

目标：Python POC 验证后，上层用 Rust 重构

- [ ] ap-core crate：核心类型（AgentId, Version, Manifest, Dependency）
- [ ] ap-fetcher crate：Git-based 包获取（git2 + semver，替代 ap-registry + ap-client + ap-store）
- [ ] ap-runtime crate：Agent Supervisor（tokio::process + DashMap）
- [ ] ap-gateway crate：MCP 网关（rmcp + 多路复用）
- [ ] ap-cli crate：CLI（clap 命令行）
- [ ] 逐模块替换验证（每个 crate 独立测试后替换 Python 模块）
- [ ] 接口兼容性测试（Rust 平台 vs Python 平台读写同一 lockfile.json）

> 关键约束：Agent Runtime（Python）不动，MCP 协议边界是语言边界。详见 §10.8 和 §12.6。

### 风险矩阵

| # | 风险 | 概率 | 影响 | 应对 |
|---|------|------|------|------|
| R1 | **编排层自建复杂度** | 中 | 中 | 参考 ClawTeam 经过验证的实现，必要时直接搬运代码 |
| R2 | **集成复杂度** | 高 | 中 | 分阶段实施，先跑通 Core Loop |
| R3 | **PydanticAI 版本变化** | 中 | 中 | 抽象接口，便于替换实现 |
| R5 | **Agent 子进程崩溃** | 中 | 高 | Router 自动重启，捕获 stderr |
| R6 | **Python Runtime 安全风险** | 中 | 高 | SecurityChecker AST 级检查 + 进程边界隔离 |
| R7 | **进化引擎 LLM 成本** | 中 | 中 | Apply-Retry 限制（≤5 轮），三触发器防循环 |
| R8 | **Compaction 死循环** | 中 | 高 | CompactionGuard（min_turns=5 + 只重注入 L0/L1 摘要） |
| R9 | **Context Budget 溢出** | 中 | 高 | 硬限制（L0≤800, L1≤3000）+ 强制截断阈值 90% |
| R10 | **长对话超 Token 限制** | 中 | 中 | Agent 内部压缩历史或截断 |
| R11 | **Rust 重构迁移风险** | 中 | 中 | 不变接口约束 + 逐模块替换 + Python/Rust 共存测试 |

### 项目结构

```
agent-nexus/
├── src/agent_nexus/
│   ├── platform/                # Platform 核心
│   │   ├── router/              # Platform Router（4-Phase Workflow）
│   │   │   ├── router.py
│   │   │   ├── workflow.py
│   │   │   └── subtask.py       # SubtaskController（超时/重试/并行控制）
│   │   ├── orchestration/       # 自建编排层
│   │   │   ├── task_graph.py    # TaskGraph（SQLite + blocked_by + 环检测）
│   │   │   ├── process_manager.py # ProcessManager（asyncio.subprocess + 健康检查）
│   │   │   ├── ipc.py           # IPC 协议（stdin/stdout JSON-lines）
│   │   │   └── dsl.py           # OrchestrationDSL（TOML DAG 解析）
│   │   ├── gateway/             # MCP Gateway 聚合
│   │   │   ├── gateway.py
│   │   │   ├── deferred_registry.py
│   │   │   └── tool_adapter.py
│   │   ├── config/              # 模型配置（Provider Registry + pydantic-ai）
│   │   │   ├── model_config.py
│   │   │   ├── loader.py
│   │   │   └── defaults.py
│   │   ├── hooks/               # Hook 执行器
│   │   │   └── executor.py
│   │   ├── local/               # Local Platform（CLI + Git Installer + Supervisor）
│   │   │   ├── cli.py           # CLI 入口 (Typer)
│   │   │   ├── sources.py       # 包源管理（sources.yaml 解析）
│   │   │   ├── installer.py     # Git Installer（clone --sparse + venv）
│   │   │   ├── lockfile.py      # 锁文件管理（lockfile.json）
│   │   │   └── supervisor.py    # Agent 进程管理
│   │   ├── skills/              # Skill 加载器
│   │   │   ├── loader.py
│   │   │   └── models.py
│   │   ├── evolution/           # Self-Evolution Engine
│   │   │   ├── engine.py
│   │   │   ├── evolver.py
│   │   │   ├── store.py
│   │   │   ├── analyzer.py
│   │   │   ├── compaction.py
│   │   │   ├── context_describer.py
│   │   │   ├── health.py
│   │   │   ├── promotion.py
│   │   │   └── thresholds.py
│   │   └── runtime/             # Python Runtime（CaveAgent 集成）
│   │       ├── runtime.py
│   │       ├── executor.py
│   │       ├── describer.py
│   │       ├── permission_checker.py
│   │       ├── security_checker.py
│   │       ├── security_rules.py
│   │       └── token_tracker.py
│   └── models/                  # 共享数据模型
│       ├── agent.py
│       ├── config.py
│       ├── context.py
│       ├── distribution.py
│       ├── evolution.py
│       ├── hooks.py
│       ├── ipc.py
│       ├── permission.py
│       ├── runtime.py
│       └── task.py
├── agents/                      # 官方 Agent
│   ├── atomic/                  # 10 Atomic Agents
│   └── composite/               # 5 Composite Agents
├── tests/                       # 测试
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── templates/                   # OrchestrationDSL TOML 模板
├── crates/                      # Rust 重构（远期）
│   ├── ap-core/                 # 核心类型、配置、协议
│   ├── ap-fetcher/              # Git-based 包获取（git2 + semver）
│   ├── ap-runtime/              # Agent 运行时 (tokio)
│   ├── ap-gateway/              # MCP 网关 (rmcp)
│   └── ap-cli/                  # CLI (clap)
└── pyproject.toml
```

---
