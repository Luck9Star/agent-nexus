# 产品定位与核心架构

> Agent Nexus POC v5 — §1 产品定位与愿景 + §2 竞争格局与差异化 + §3 核心架构
>
> **v5.1 更新**：移除 ClawTeam pip 依赖，改为自建编排层（参考 ClawTeam 实现）。详见 §3.2 设计决策 D1。

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

**Atomic Agents（10）**：requirements-analyzer、doc-filler、code-reviewer、contract-analyzer、api-doc-generator、security-scanner、accessibility-auditor、localization-specialist、market-intelligence-analyst、test-suite-generator

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
│  │  Layer 4: Self-Evolution Engine（借鉴 OpenSpace）           │  │
│  │                                                            │  │
│  │  Layer 1: Atomic Skill Evolution                          │  │
│  │  ├── ExecutionAnalyzer (per-task LLM 分析)                │  │
│  │  ├── FIX: 修复 broken skills                              │  │
│  │  ├── CAPTURED: 提取成功模式为新 skill                      │  │
│  │  └── 质量: applied_rate, completion_rate, effective_rate   │  │
│  │                                                            │  │
│  │  Layer 2: Composite Orchestration Evolution                │  │
│  │  ├── 编排分析 (调用链效率、并行机会、缺失步骤)              │  │
│  │  ├── DERIVED: 优化 TOML 模板                              │  │
│  │  └── CAPTURED: 创建新 Composite Agent                     │  │
│  │                                                            │  │
│  │  Layer 3: Agent Promotion                                  │  │
│  │  └── Skill → Agent 提升 (effective_rate > 阈值)            │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 3: Python Runtime（借鉴 CaveAgent）                  │  │
│  │                                                            │  │
│  │  每个 Atomic Agent 内部:                                   │  │
│  │  ├── IPythonRuntime（同进程，Agent 已是子进程无需二次隔离）  │  │
│  │  │   ├── Variables（业务对象, Pydantic models）             │  │
│  │  │   ├── Functions（领域函数）                             │  │
│  │  │   └── Types（类型 schema, 自动注入）                   │  │
│  │  ├── SecurityChecker（AST 级安全检查）                     │  │
│  │  └── LLM 生成 Python 代码操作对象（优先于 tool_call）      │  │
│  │                                                            │  │
│  │  Agent 间通信:                                             │  │
│  │  ├── 内部: runtime.retrieve() / Variable 传递             │  │
│  │  └── 外部: MCP / IPC（stdin/stdout JSON-lines）          │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 2: 编排层（自建，参考 ClawTeam）                     │  │
│  │  ├── OrchestrationDSL (TOML DAG 定义)                     │  │
│  │  ├── TaskGraph（依赖图 + 状态机 + 环检测）                 │  │
│  │  ├── IPC（stdin/stdout JSON-lines）                       │  │
│  │  └── ProcessManager（asyncio.subprocess + 健康检查）       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Layer 1: MCP 暴露层（对外通信）                           │  │
│  │  ├── FastMCP Server（每个 Agent 独立）                    │  │
│  │  ├── MCP Gateway（路由/发现）                             │  │
│  │  └── 职责: 跨进程调用、外部工具、非 Python 客户端         │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 关键设计决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 自建编排层 | 参考 ClawTeam 实现按需精简自建，不作为 pip 依赖引入。避免 API 不稳定风险，完全控制接口 |
| D2 | 双层 Agent 架构 | Atomic（PydanticAI + Runtime + MCP）+ Composite（OrchestrationDSL TOML） |
| D3 | Agent 三种运行模式 | MCP standalone / Platform Router / CLI standalone |
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

---
