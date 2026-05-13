# 附录

> Agent Nexus Design Doc — 附录 A-D：OrchestrationDSL TOML Schema、Agent 类型对比表、模型分层配置、参考项目与本地路径

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/models/` (17 files), `src/agent_nexus/platform/config/` (defaults, loader, model_config)

## 附录

### 附录 A：OrchestrationDSL TOML Schema

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `[goal]` | section | 是 | 编排目标 |
| `goal.description` | string | 是 | 目标描述 |
| `[[agents]]` | array | 是 | Agent 成员列表 |
| `agents[].name` | string | 是 | Agent 名称 |
| `agents[].description` | string | 否 | Agent 描述 |
| `agents[].tool_loading` | string | 否 | 加载策略：eager / lazy / manifest_only（默认 lazy） |
| `[[tasks]]` | array | 是 | 任务列表 |
| `tasks[].id` | string | 是 | 任务 ID |
| `tasks[].description` | string | 是 | 任务描述 |
| `tasks[].agent` | string | 是 | 任务分配给哪个 Agent |
| `tasks[].blocked_by` | array | 否 | 阻塞任务 ID 列表 |
| `tasks[].vars` | object | 否 | 任务变量 |
| `[tool_loading]` | section | 否 | 全局工具加载策略 |
| `tool_loading.strategy` | string | 否 | 全局策略：eager / lazy / manifest_only（默认 lazy） |
| `tool_loading.preload_agents` | array | 否 | eager 策略下预加载的 Agent 列表 |

### 附录 B：Agent 类型对比表

| 特征 | Atomic Agent | Composite Agent |
|------|-------------|----------------|
| **定义** | 单一专业能力，深度优化 | 多个 Atomic Agent 编排 |
| **编排原语** | 单进程（PydanticAI + Runtime） | OrchestrationDSL（TOML DAG）|
| **独立运行** | 支持（standalone MCP）| 不支持 |
| **Platform 运行** | 支持 | 支持 |
| **编排角色** | 被编排者（Worker） | 编排者（Coordinator）|
| **SKILL.md** | 必须 | 必须（包含 Team Template）|
| **manifest type** | atomic | composite |
| **composition.toml** | 无 | 必须 |
| **MCP 工具** | 领域专用工具 | 编排工具（deliver/validate 等）|
| **依赖声明** | 无 | 必须声明 atomic_agents |

### 附录 C：模型分层配置

| Tier | 模型示例 | 用途 | 价格敏感度 | Agent 示例 |
|------|---------|------|-----------|------------|
| **Lightweight** | GPT-4o-mini | 快速任务、映射阶段 | 高 | doc-filler（填充）|
| **Standard** | GPT-4o、Claude Sonnet | 通用任务 | 中 | api-doc-generator |
| **Powerful** | Claude Sonnet 4 | 复杂推理 | 低 | requirements-analyzer |
| **Premium** | Claude Opus 4 | 深度分析、代码审查 | 低 | code-reviewer、contract-analyzer |

### 附录 D：参考项目与本地路径

| 项目 | 许可证 | 用途 | 本地路径 | 关键模块 |
|------|--------|------|---------|---------|
| **ClawTeam** | MIT | 编排层参考实现 | `/Users/yangyitian/Documents/dev/Agents/ClawTeam/` | `clawteam/store/` TaskStore（TaskGraph 参考）, `clawteam/team/mailbox.py` MailboxManager（IPC 参考）, `clawteam/spawn/` SpawnBackend（ProcessManager 参考）, `clawteam/templates/` Team Template（OrchestrationDSL 参考）|
| **OpenSpace** | MIT | Self-Evolution Engine 参考 | `/Users/yangyitian/Documents/dev/Agents/OpenSpace/` | `openspace/skill_engine/` 进化引擎, `openspace/grounding/core/quality/manager.py` ToolQualityManager, `openspace/tool_layer.py` 主入口 |
| **CaveAgent** | MIT | Python Runtime 参考 | `/Users/yangyitian/Documents/dev/Agents/cave-agent/` | `src/cave_agent/runtime/` Runtime 核心, `src/cave_agent/security/` SecurityChecker, `src/cave_agent/agent.py` 执行循环 |
| **deer-flow** | Apache-2.0 | Harness/App 分离、Skill loading 参考 | `/Users/yangyitian/Documents/dev/Agents/deer-flow/` | `packages/harness/deerflow/skills/` Skill 加载, `packages/harness/deerflow/subagents/` 子任务控制, `packages/harness/deerflow/agents/lead_agent/prompt.py` Continue/Spawn 决策 |
| **OpenHarness** | MIT | Permission/Hook/Plugin 架构参考 | `/Users/yangyitian/Documents/dev/Agents/OpenHarness/` | `src/openharness/plugins/` Plugin 系统, `src/openharness/permissions/` 权限, `src/openharness/hooks/` Hook, `src/openharness/coordinator/agent_definitions.py` Agent 定义, `src/openharness/swarm/` 进程管理 |
| **nanobot** | MIT | 轻量级 Agent + MCP 集成参考 | `/Users/yangyitian/Documents/dev/Agents/nanobot/` | `nanobot/agent/tools/mcp.py` MCP 客户端, `nanobot/agent/loop.py` Agent 循环, `nanobot/agent/skills.py` Skill 加载 |
| **agent-nexus** | — | 本项目 | `/Users/yangyitian/Documents/dev/Agents/agent-nexus/` | — |

### 附录 E：共享数据模型

> **Code**: `src/agent_nexus/models/` (17 files)

#### E.1 模型层次结构

所有值对象模型继承自 `FrozenModel`（`_common.py`），它基于 Pydantic `BaseModel` 启用 `frozen=True`，提供不可变性和可哈希性。

```
FrozenModel (BaseModel, frozen=True)
  ├── AgentDefinition, AgentManifest, AgentPackage
  ├── ModelConfig, PlatformConfig, ProviderConfig
  ├── ContextBudget, ContextBudgetLogEntry, TokenUsage
  ├── CompositionTask, Composition
  ├── HookDefinition, HookExecution, AggregatedHookResult
  ├── IPCMessage, PlatformToAgent, AgentToPlatform
  ├── A2AMessage, AgentAddress
  ├── LockfileEntry, IndexEntry, SourceEntry
  ├── EvolutionMetrics, SkillRecord
  ├── TaskItem, TaskGraphSnapshot
  ├── TeamStatus
  ├── PermissionConfig, PathRule
  ├── ExecutionResult, Variable, Function
  └── ExternalServerConfig
```

#### E.2 核心模型分类

| 分类 | 文件 | 关键类型 | 用途 |
|------|------|---------|------|
| **Agent** | `agent.py` | `AgentManifest`, `AgentDefinition`, `RunMode`, `AgentType` | Agent 元数据和运行配置 |
| **IPC** | `ipc.py` | `IPCMessage`, `PlatformToAgent`, `AgentToPlatform`, `MessageDirection` | 子进程间通信协议（stdin/stdout JSON-lines） |
| **A2A** | `ipc.py` | `A2AMessage`, `AgentAddress` | Agent-to-Agent 消息（Platform 作为 Broker 中继） |
| **Hooks** | `hooks.py` | `HookDefinition`, `HookEvent`, `HookType`, `AggregatedHookResult` | 生命周期钩子定义与执行结果 |
| **Context** | `context.py` | `ContextBudget`, `TokenUsage`, `ContextLevel`, `BudgetAlertLevel` | Token 预算管理与分层加载 |
| **Config** | `config.py` | `PlatformConfig`, `ModelConfig`, `ProviderConfig`, `RuntimeConfig` | 平台配置模型 |
| **Distribution** | `distribution.py` | `LockfileEntry`, `SourceEntry`, `IndexEntry`, `InstallationStatus` | Git 分发与安装状态 |
| **Evolution** | `evolution.py` | `EvolutionMetrics`, `SkillRecord`, `EvolutionType`, `SkillLineage` | 自进化引擎数据模型 |
| **Composition** | `composition.py` | `Composition`, `CompositionTask`, `CompositionError` | Composite Agent 编排图 |
| **Permission** | `permission.py` | `PermissionMode`, `PermissionConfig`, `PathRule`, `PermissionDecision` | 三模式权限系统 |
| **Runtime** | `runtime.py` | `ExecutionResult`, `Variable`, `Function`, `SecurityViolation` | Python Runtime 执行模型 |
| **Task** | `task.py` | `TaskItem`, `TaskState`, `TaskGraphSnapshot` | TaskGraph 任务模型 |
| **Team** | `team.py` | `TeamState`, `TeamStatus`, `TeamEvent` | 团队生命周期管理 |
| **External MCP** | `external_mcp.py` | `ExternalServerConfig`, `TransportType`, `ExternalServerAuth` | 外部 MCP Server 桥接配置 |
| **Capability** | `capability.py` | `ModelCapability`, `ModelCapabilityRegistry` | 模型能力查询（17 模型 × 5 Provider: Anthropic 5 + OpenAI 5 + DeepSeek 2 + Qwen 3 + MiniMax 2） |
| **Errors** | `errors.py` | `AgentNexusError` | 统一异常基类 |

#### E.3 IPC 消息协议

IPC 使用 JSON-lines 格式通过 stdin/stdout 通信，消息类型通过枚举约束：

**Platform → Agent**（`PlatformToAgentType`）：`chat`, `task`, `data_reference`, `receive_message`, `receive_request`, `receive_broadcast`, `receive_reply`

**Agent → Platform**（`AgentToPlatformType`）：`result`, `progress`, `error`, `send_message`, `send_request`, `broadcast`, `reply`

所有消息封装在 `IPCMessage` 信封中，通过 `MessageDirection` 字段区分方向。`A2AMessage` 用于 Agent 间通信，由 Platform 作为 Broker 中继。
