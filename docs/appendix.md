# 附录

> Agent Nexus Design Doc — 附录 A-D：OrchestrationDSL TOML Schema、Agent 类型对比表、模型分层配置、参考项目与本地路径

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/models/` (12 model files, 1,577 lines), `src/agent_nexus/platform/config/` (defaults, loader, model_config)

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
