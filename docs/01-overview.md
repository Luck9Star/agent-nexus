# 产品定位与核心架构

> Agent Nexus Design Doc — §1 产品定位与愿景 + §2 竞争格局与差异化 + §3 核心架构

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/platform/` (11 子模块，持续增长), `src/agent_nexus/models/` (17 个模型文件)
> **Tests**: `tests/unit/` (140 test files), `tests/integration/` (11 test files), `tests/e2e/` (25 test files)

## §1 产品定位与愿景

### 1.1 战略定位

Agent Platform 提供自建的多 Agent 编排基础设施 + MCP-native Agent 市场。编排层参考 ClawTeam 经过验证的 TaskStore、MailboxManager、SpawnBackend 等模块，按需精简自建，不作为外部依赖引入：

- **编排基础设施（自建，参考 ClawTeam）**：TaskGraph（任务依赖图 + 状态机）、IPC（进程间通信）、ProcessManager（子进程管理 + 健康检查）、OrchestrationDSL（TOML 编排模板）
- **增强层（自研）**：MCP Gateway、Git-based Distribution、Model Config、Python Runtime、Self-Evolution Engine

### 1.2 核心价值

> MCP-native Agent 生态市场，基于自建多 Agent 编排能力，专攻需要深度领域知识、多 Agent 协作和质量保证的任务场景

| 维度 | 通用 Agent + Skills | Agent Platform |
|------|---------------------|----------------|
| **深度** | Skills 通用化，跨领域泛化 | Atomic Agent 专精单一领域，深度优化 |
| **并行** | 顺序执行，难以并行 | Composite Agent 原生支持并行编排（blocked_by DAG） |
| **质量** | 无交叉验证 | 多 Agent 交叉 Review，质量门禁 |
| **模型** | 固定模型 | 用户自配模型，灵活切换 |
| **协作** | 单 Agent 为主 | 自建多 Agent 编排，协同工作流 |
| **编排基础设施** | 自建 | ClawTeam TaskStore、Mailbox、SpawnBackend（参考） |
| **计费** | 按调用计费 | 免费（用户自备模型） |

### 1.3 自建编排层（参考 ClawTeam 实现）

ClawTeam 是参考实现，不是依赖。我们参考其经过验证的编排模式，按需精简自建：

```
参考来源（ClawTeam）→ 自建组件
    ├── TaskStore (Kanban + fcntl.flock) → TaskGraph (SQLite + asyncio lock)
    ├── MailboxManager (原子写入)       → IPC (stdin/stdout JSON-lines)
    ├── SpawnBackend (ABC + tmux/subprocess) → ProcessManager (asyncio.subprocess + 健康检查)
    ├── Transport (File + P2P ZeroMQ)   → 初期仅本地 IPC，远程按需扩展
    └── Team Template (TOML)            → OrchestrationDSL (TOML DAG 定义)

Agent Platform（自研增强层）
    ├── MCP Gateway（MCP 统一暴露）
    ├── Git-based Distribution（Agent 分发）
    ├── Model Config（模型配置）
    ├── Python Runtime（IPythonRuntime 执行层）
    └── Self-Evolution Engine（自建进化引擎）
```

> **参考模块**:
> - ClawTeam `clawteam/store/` — TaskStore（自建 TaskGraph 参考）
> - ClawTeam `clawteam/team/mailbox.py` — MailboxManager（IPC 模式参考）
> - ClawTeam `clawteam/spawn/` — SpawnBackend（ProcessManager 参考）
> - ClawTeam `clawteam/templates/` — Team Template（OrchestrationDSL 参考）
> - OpenHarness `src/openharness/plugins/` — Plugin 聚合模式
> - deer-flow `packages/harness/deerflow/skills/` — 3-tier Skill loading

### 1.4 首批 Agent

**Atomic Agents（20）**：requirements-analyzer、doc-filler、code-reviewer、contract-analyzer、api-doc-generator、security-scanner、accessibility-auditor、localization-specialist、market-intelligence-analyst、test-suite-generator、good-skill、api-contract-tester、config-linter、data-pipeline-validator、db-schema-analyzer、dependency-auditor、error-analyzer、generic-expert-agent、i18n-validator、performance-profiler

**Composite Agents（5）**：feature-delivery-pipeline、document-compliance-gateway、cicd-quality-gate、competitive-intelligence-briefing、product-documentation-suite

---

## §2 竞争格局与差异化

### 2.1 ClawTeam：参考实现

ClawTeam 是 HKUDS 开发的多 Agent 协作框架（MIT License），其编排模式（TaskStore + MailboxManager + SpawnBackend + TOML Template）是我们自建编排层的主要参考。我们不直接依赖 ClawTeam，而是参考其经过验证的实现，按需精简自建。

| ClawTeam 模块 | 我们的对应 | 参考方式 |
|---|---|---|
| TaskStore (blocked_by + 环检测) | TaskGraph (SQLite + asyncio) | 参考实现 |
| MailboxManager (原子写入) | IPC (stdin/stdout JSON-lines) | 简化 |
| SpawnBackend (ABC + tmux/subprocess) | ProcessManager (asyncio.subprocess) | 精简 |
| Team Template (TOML) | OrchestrationDSL (TOML DAG) | 参考格式 |
| Transport (File + P2P ZeroMQ) | 初期仅本地 IPC | 暂不实现 |

### 2.2 竞争对比

| 维度 | Agent Platform | OpenClaw + Skills | Dify | MaxKB |
|------|---------------|-------------------|------|-------|
| **定位** | 专精领域 Agent 市场 | 通用 Agent 框架 + Skills | LLM 应用开发平台 | 知识库 RAG 平台 |
| **Agent 类型** | 双层（Atomic + Composite） | 单层 Skill | 无（LLM App） | 基础 Chatbot |
| **编排** | 自建 OrchestrationDSL（声明式 DAG） | 手动编排 | 可视化拖拽 | 简单对话流 |
| **领域深度** | 高 | 低（泛化） | 低 | 中 |
| **质量保证** | 多 Agent 交叉 Review | 无 | 无 | 无 |
| **本地运行** | 是 | 否 | 可 | 可 |
| **MCP-native** | 是 | 是 | 否 | 否 |

### 2.3 deer-flow 可借鉴内容

deer-flow（Apache-2.0, ByteDance）提供有价值的架构参考：

| 设计 | 借鉴方式 |
|------|----------|
| Harness/App 分离 | Platform Router 编排，Worker 负责执行 |
| 三层渐进式 Skill Loading | SKILL.md 三层规范（metadata → body → resources） |
| Subagent 超时 + 并行限制 | SubtaskController 超时和并行控制 |
| Skill Eval Framework | Agent quality rating system |

不借鉴：LangGraph 依赖（强耦合）、完整 14 步中间件（过度工程化）、完整记忆系统（与自建 TaskGraph/IPC 重复）。

> **参考模块**: deer-flow `packages/harness/deerflow/` — Harness/App 分离架构, `packages/harness/deerflow/agents/lead_agent/prompt.py` — Continue/Spawn 决策逻辑, `packages/harness/deerflow/subagents/executor.py` — SubagentExecutor

### 2.4 我们的差异化

> 专精领域 + 自建多 Agent 编排 + 质量保证 + 用户自控 + MCP-native

适用于需要深度领域知识（如法律合同分析）、多 Agent 协作（如合规审查）、质量保证（如代码审查）的任务场景。

---

## §3 核心架构

### 3.1 四层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      Agent Platform                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 4: Self-Evolution Engine ✅                          │  │
│  │  Code: platform/evolution/ (18 files)                      │  │
│  │                                                            │  │
│  │  Layer 1: Atomic Skill Evolution                          │  │
│  │  ├── ExecutionAnalyzer (per-task LLM 分析)                │  │
│  │  ├── SkillEvolver: FIX / DERIVED / CAPTURED               │  │
│  │  ├── HealthChecker: 规则引擎预过滤                         │  │
│  │  ├── EvolutionStore: SQLite DAG 版本管理                   │  │
│  │  ├── SkillStore: 独立 Skill 持久化                        │  │
│  │  ├── Experimenter: A/B 实验框架                           │  │
│  │  ├── SkillPatch: 技能补丁机制                             │  │
│  │  ├── AnalysisStore / BudgetStore / Metrics: 专项存储       │  │
│  │  └── 质量: applied_rate, completion_rate, effective_rate   │  │
│  │                                                            │  │
│  │  Layer 2: Composite Orchestration Evolution                │  │
│  │  ├── 编排分析 (调用链效率、并行机会、缺失步骤)              │  │
│  │  ├── DERIVED: 优化 TOML 模板                              │  │
│  │  └── CAPTURED: 创建新 Composite Agent                     │  │
│  │                                                            │  │
│  │  Layer 3: Agent Promotion                                  │  │
│  │  └── AgentPromoter: Skill → Agent 提升 (effective_rate > 阈值) │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 3: Python Runtime ✅                                 │  │
│  │  Code: platform/runtime/ (8 files)                         │  │
│  │                                                            │  │
│  │  每个 Atomic Agent 内部:                                   │  │
│  │  ├── IPythonExecutor（同进程 InteractiveShell）              │  │
│  │  ├── PythonRuntime（Variables/Functions/Types 管理）        │  │
│  │  ├── SecurityChecker（AST 级安全检查 + 规则集）            │  │
│  │  ├── PermissionChecker（DEFAULT/PLAN/FULL_AUTO 权限）      │  │
│  │  ├── TieredRuntimeDescriber（L0-L3 分层描述）              │  │
│  │  ├── TokenTracker（Token 用量追踪）                        │  │
│  │  └── LLM 生成 Python 代码操作对象（优先于 tool_call）      │  │
│  │                                                            │  │
│  │  Agent 间通信:                                             │  │
│  │  ├── 内部: runtime.retrieve() / Variable 传递             │  │
│  │  └── 外部: MCP / IPC（stdin/stdout JSON-lines）          │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 2: 编排层 ✅                                         │  │
│  │  Code: platform/orchestration/ (7 files)                   │  │
│  │  ├── TaskGraph（SQLite + 依赖图 + 状态机 + 环检测）        │  │
│  │  ├── IPC（stdin/stdout JSON-lines 管道协议）               │  │
│  │  ├── ProcessManager（asyncio.subprocess + 健康检查）       │  │
│  │  ├── OrchestrationDSL (TOML DAG 定义 + 验证)              │  │
│  │  ├── AgentDirectory（Agent 发现与注册）                    │  │
│  │  ├── MessageBroker（消息路由与分发）                       │  │
│  │  └── TeamManager（团队生命周期管理）                       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 1: MCP 暴露层 ✅                                     │  │
│  │  Code: platform/gateway/ (7 files)                         │  │
│  │  ├── MCPGateway（FastMCP Server 聚合）                    │  │
│  │  ├── DeferredAgentRegistry（Agent 级延迟加载）            │  │
│  │  ├── ToolAdapter（Agent 工具适配）                         │  │
│  │  ├── Auth（请求认证）                                      │  │
│  │  ├── Audit（操作审计）                                     │  │
│  │  ├── ExternalMcpAdapter（外部 MCP Server 桥接）           │  │
│  │  ├── SchemaTransformer（Schema 转换）                     │  │
│  │  └── 职责: 跨进程调用、外部工具、非 Python 客户端         │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Platform Router ✅                                         │  │
│  │  Code: platform/router/                                     │  │
│  │  ├── 4-Phase Workflow (Research → Synthesis → Implementation → Verification) │
│  │  └── SubtaskController (超时/重试/并行控制)                │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Agency Pipeline ✅                                         │  │
│  │  Code: platform/agency/ (24 files)                          │  │
│  │  ├── LLMPlanner (LLM 驱动任务分解)                         │  │
│  │  ├── DAGDispatcher (并行 DAG 调度)                         │  │
│  │  ├── LLMExecutor (per-expert LLM 调用)                     │  │
│  │  ├── LLMIntegrator (语义合成)                               │  │
│  │  ├── LLMQualityGate (质量评估)                              │  │
│  │  ├── Registry / Selector (专家注册与选择)                  │  │
│  │  ├── TokenCounter / LLMClient (模型能力感知)               │  │
│  │  ├── Policy / Allowlist (策略与白名单)                     │  │
│  │  └── CLI / Parser / Importer (CLI 入口与解析)              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  基础设施 ✅                                                │  │
│  │  Code: platform/config/ + local/ + skills/ + hooks/        │  │
│  │  ├── ConfigLoader + ModelConfigManager (模型配置)          │  │
│  │  ├── GitInstaller + Lockfile + Sources (Git 分发)          │  │
│  │  ├── AgentSupervisor (Agent 生命周期管理)                  │  │
│  │  ├── CLI (Typer, agent-nexus 命令行)                      │  │
│  │  ├── SkillLoader (SKILL.md 加载)                          │  │
│  │  └── HookExecutor (Hook 执行器)                           │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 关键设计决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 自建编排层 | 参考 ClawTeam 实现按需精简自建，不作为 pip 依赖引入。避免 API 不稳定风险，完全控制接口 |
| D2 | 双层 Agent 架构 | Atomic（PydanticAI + Runtime + MCP）+ Composite（OrchestrationDSL TOML） |
| D3 | Agent 三种运行模式 | MCP standalone / Platform Router / CLI standalone（Agency Pipeline 是独立编排管道，非 RunMode） |
| D4 | 用户本地执行 | 无计费，用户管理模型配置 |
| D5 | MCP-native | 所有 Agent 暴露为 MCP Server |
| D6 | 外部通信用 MCP | stdio + SSE 双模式 |
| D7 | 内部通信用 IPC | stdin/stdout JSON-lines（本地）；远程按需扩展 |
| D8 | Git Worktree 隔离 | 适用于编码场景 |
| D9 | Git-based 分发 | 通过 Git 仓库分发 Agent 包（官方 monorepo + 私有 repo），初期不建 Cloud Registry |
| D10 | deer-flow 借鉴 | Harness/App 分离、3-tier Skill loading、Skill eval、子任务超时 |
| D11 | 自建进化引擎 | 借鉴 OpenSpace 架构，扩展到 Agent 级别 + 编排级别 |
| D12 | IPythonRuntime | 同进程，Agent 已是子进程无需二次隔离 |
| D13 | Runtime-First Hybrid | Python Runtime 优先，MCP 用于外部通信 |
| D14 | 不完全抛弃 Tool Call | Runtime 是主要执行方式，MCP 是辅助通信方式 |
| D15 | 全部 MIT/Apache-2.0 | 无商业限制 |
| D16 | Agent 级 Deferred Loading | MCP Gateway 以 Agent 为粒度做 deferred loading（不是 Tool 级），LLM 先看 manifest，按需激活 Agent 的完整 tool schema |
| D17 | Tiered Context Loading（四层） | Layer 0 身份核心（每轮）→ Layer 1 执行上下文（首轮）→ Layer 2 扩展知识（按需）→ Layer 3 实时数据（运行时动态）。从 SKILL.md 单文件三层升级为 Agent 全上下文分层 |
| D18 | Provider-Agnostic Tool Search | Gateway 层提供 `search_agents()` 标准 function calling 作为基础方案，Anthropic 用户可选用原生 `defer_loading` 零 round-trip 加速 |
| D19 | Compaction 防死循环 | Context 溢出时只重注入 Layer 0 + Layer 1 摘要，设 `min_turns_between_compactions=5` 防止正反馈死循环（OpenClaw #68032 教训） |
| D20 | Context Budget 硬限制 | Layer 0 ≤ 800 tokens，Layer 1 ≤ 3000 tokens，bootstrap 总量 ≤ 5000 tokens，compaction 触发阈值 80%，目标压缩到 40% |
| D21 | Agency Pipeline | LLM 驱动的专家编排管道：Planner（任务分解）→ Executor（per-expert LLM 调用）→ Integrator（语义合成）→ QAGate（质量评估）。共享 ModelCapabilityRegistry 提供模型能力数据 |

### 3.3 Agency Pipeline 架构

Agency Pipeline 是独立于 Agent RunMode 之外的编排管道，用于 LLM 驱动的多专家编排。Agent 本身只有 3 种 RunMode（MCP standalone / Platform Router / CLI standalone），Agency Pipeline 是平台级能力。核心架构为 5 阶段流水线：

```
用户任务 → LLMPlanner → DAGDispatcher → LLMExecutor → LLMIntegrator → LLMQualityGate → 最终输出
```

**阶段职责**：

| 阶段 | 模块 | 职责 |
|------|------|------|
| 任务分解 | `llm_planner.py` | LLM 将用户任务分解为专家子任务列表，生成执行 DAG |
| 并行调度 | `dag_dispatcher.py` | 按 DAG 依赖关系并行调度无阻塞子任务，管理最大并行度 |
| 专家执行 | `llm_executor.py` | 为每个子任务选择专家 profile，调用 LLM 生成输出 |
| 语义合成 | `llm_integrator.py` | 将多个专家输出合成为统一结果，处理冲突 |
| 质量评估 | `llm_qa_gate.py` | LLM 评估合成质量，不达标则回退重试 |

**支撑模块**：

| 模块 | 职责 |
|------|------|
| `registry.py` | 专家注册表（profile 加载、能力搜索） |
| `selector.py` | 专家选择器（能力匹配、多样性去重） |
| `parser.py` + `policy.py` + `allowlist.py` | 专家 profile 解析、内容策略检查、白名单验证 |
| `llm_client.py` | 统一 LLM 客户端（litellm + streaming），共享连接池 |
| `model_capability.py` | ModelCapabilityRegistry：17 模型 × 6 Provider 的能力数据（max_tokens、temperature、vision） |
| `token_counter.py` | CJK 感知的 Token 计数，用于预算管理 |
| `context_provider.py` | 管道上下文注入 |
| `prompt_loader.py` | Prompt 模板加载 |
| `task_composer.py` | 子任务组装 |
| `reflector.py` | 结果反思与优化建议 |
| `hooks.py` | 管道生命周期 hook |
| `json_parse.py` | JSON 解析容错工具 |
| `planner.py` / `executor.py` / `integrator.py` / `qa_gate.py` | 非LLM 基础类 |
| `cli_backend/` | CLI 会话管理 + 命令路由 |

**ModelCapabilityRegistry 4 级回退**：精确匹配 → 去日期后缀 → 去尾部数字 → Provider 默认值。所有管道阶段共享同一 Registry 实例，避免重复模型数据获取。

---
