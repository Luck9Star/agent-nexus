# Agent 体系

> Agent Nexus Design Doc — §7 Agent 体系：两种 Agent 类型、Atomic Agent 组成、Composite Agent 组成、三种运行模式、概念映射表、Agent Package 结构、Agent 目录

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/models/agent.py`, `agents/atomic/`, `agents/composite/`
> **Tests**: `tests/unit/test_agent_models.py`

## §7 Agent 体系

### 7.1 两种 Agent 类型

| 特征 | Atomic Agent | Composite Agent |
|------|-------------|----------------|
| **定义** | 单一专业能力，深度优化 | 多个 Atomic Agent 编排 |
| **编排原语** | 单进程（PydanticAI + Runtime） | OrchestrationDSL（TOML DAG）|
| **独立运行** | 支持（standalone MCP）| 不支持 |
| **Platform 运行** | 支持 | 支持 |
| **编排角色** | 被编排者（Worker） | 编排者（Coordinator）|
| **SKILL.md** | 必须 | 必须（包含编排 DSL）|
| **composition.toml** | 无 | 必须 |
| **MCP 工具** | 领域专用工具 | 编排工具（deliver/validate 等）|
| **依赖声明** | 无 | 必须声明 atomic_agents |

### 7.2 Atomic Agent 组成

Atomic Agent = PydanticAI + PythonRuntime + MCP Server

特征：
1. **单一专业能力**：每个 Atomic Agent 专精一个领域
2. **PydanticAI 驱动**：使用 PydanticAI 框架定义 Agent 逻辑和工具
3. **深度优化**：领域知识库、专项工具、边缘 case 处理
4. **独立测试验证**：每个 Atomic Agent 有独立的测试套件
5. **可被发现**：通过 SKILL.md 声明能力，Git 仓库 index.yaml 可索引
6. **MCP Server 暴露**：支持作为独立 MCP Server 运行

### 7.3 Composite Agent 组成

Composite Agent = OrchestrationDSL（TOML DAG）

特征：
1. **TOML DAG 定义**：通过 `composition.toml` 声明编排拓扑（依赖图 + blocked_by）
2. **Platform Router 编排**：使用 Platform Router 的 4-Phase Workflow 协调
3. **并行/条件/串行**：通过 blocked_by 依赖图自然支持顺序 + 并行混合
4. **统一接口**：对外暴露为单一 MCP Server
5. **质量保证**：通过交叉验证确保输出质量

### 7.4 三种运行模式

| 模式 | TOML/YAML 值 | Python Enum | 说明 | 用途 |
|------|-------------|-------------|------|------|
| **MCP Standalone** | `mcp` | `RunMode.MCP_STANDALONE` | 直接作为 MCP Server 运行（`uvx agent-name`） | 外部框架（nanobot/Hermes）直接调用 |
| **Platform Router** | `local` | `RunMode.PLATFORM_ROUTER` | 通过 Platform Router 管理（stdin/stdout JSON-lines） | Web UI、Composite Agent 编排 |
| **CLI Standalone** | `cli` | `RunMode.CLI_STANDALONE` | 直接命令行运行（`agent-name run`） | 开发调试、快速测试 |

```python
# 双模式入口（main.py）
# RunMode enum: MCP_STANDALONE="mcp", PLATFORM_ROUTER="local", CLI_STANDALONE="cli"
def main():
    mode = os.getenv("AGENT_MODE", RunMode.MCP_STANDALONE)

    if mode == RunMode.PLATFORM_ROUTER:
        # Platform Router 模式：stdin/stdout JSON-lines
        asyncio.run(serve(my_pydantic_agent))
    elif mode == RunMode.CLI_STANDALONE:
        # CLI Standalone 模式
        asyncio.run(run_cli(my_pydantic_agent))
    else:
        # MCP Standalone 模式
        mcp_serve(my_pydantic_agent)
```

### 7.5 概念映射表

| 我们的概念 | 自建组件 | 说明 |
|------------|---------|------|
| Atomic Agent | PydanticAI + PythonRuntime + MCP Server | 单进程，自包含 |
| Composite Agent | OrchestrationDSL（TOML DAG） | 声明式编排，Platform Router 执行 |
| Agent 间通信 | IPC（stdin/stdout JSON-lines） | 管道通信，零文件 IO |
| 任务管理 | TaskGraph（SQLite + 状态机） | blocked_by 依赖图 + 环检测 |
| 进程管理 | ProcessManager（asyncio.subprocess + asyncio.Lock） | 健康检查 + 自动重启 + 并发安全 |
| Token 优化 | DeferredAgentRegistry | Agent 级 Deferred Loading |
| MCP 暴露 | MCP Gateway | 聚合所有 Agent 为单一 MCP Server |

### 7.6 Agent Package 结构（Plugin 聚合模式）

> **参考模块**: OpenHarness `src/openharness/plugins/types.py` — `LoadedPlugin` 数据类, `src/openharness/plugins/loader.py` — Plugin 发现与加载系统

借鉴 OpenHarness 的 `LoadedPlugin` 架构，Agent Package 采用 Plugin 聚合模式：每个 Package 是自包含的插件单元，将 Skills、Commands、Agents、Hooks、MCP Servers 等所有贡献聚合为单一可加载单元，支持 Git 仓库分发与动态发现。

#### Agent Package 数据模型

```python
class AgentPackage:
    """Agent Package = Plugin 聚合容器"""
    manifest: AgentManifest          # 元数据 (name, version, type, description)
    skills: list[SkillDefinition]    # SKILL.md 文件列表
    commands: list[CommandDef]         # 斜杠命令 / 工具定义
    agents: list[AgentDefinition]    # 子 Agent 定义（Composite 可包含）
    hooks: dict[str, list[HookDef]]  # 生命周期钩子 (event → hooks)
    mcp_servers: dict[str, McpConfig] # 依赖的外部 MCP Server
    permissions: PermissionConfig     # 权限配置
```

#### 加载优先级（发现顺序）

| 优先级 | 来源 | 路径 |
|--------|------|------|
| 1 | Bundled（内置） | `agents/atomic/`, `agents/composite/` |
| 2 | User（用户安装） | `~/.agent-nexus/agents/` |
| 3 | Project（项目级） | `.agent-nexus/agents/` |

#### Atomic Agent Package 目录结构

**设计目标**：

```
agent-doc-filler/
├── agent-manifest.yaml       # 元数据 + 权限 + 模型配置
├── SKILL.md                  # 行为定义（三层渐进式）
├── agent.py                  # PydanticAI 核心逻辑
├── tools/                    # 领域专用工具
│   ├── analyze_template.py
│   └── fill_template.py
├── hooks/                    # 生命周期钩子
│   └── hooks.yaml            # pre/post execution hooks
├── mcp_servers/              # 依赖的外部 MCP Server 配置
│   └── filesystem.json
├── mcp_adapter.py            # MCP Server 适配器
├── local_adapter.py          # Local mode 适配器（stdin/stdout JSON-lines）
├── main.py                   # 入口（自动检测模式）
├── models.py                 # PydanticAI 数据模型
├── pyproject.toml            # 包配置
└── tests/
    └── test_agent.py
```

**当前实现**（以 doc-filler 为例）：

```
agents/atomic/doc-filler/
├── agent-manifest.yaml       # ✅ 已实现
├── SKILL.md                  # ✅ 已实现
├── agent.py                  # ✅ 已实现（PydanticAI 核心逻辑）
└── pyproject.toml            # ✅ 已实现
```

> tools/、hooks/、mcp_servers/、mcp_adapter.py、local_adapter.py 等目录和文件为设计目标，将在后续迭代中按需添加。当前 Agent 通过 Platform 的 MCP Gateway 和 IPC 基础设施统一管理。

#### Composite Agent Package 目录结构

**设计目标**：

```
agent-feature-delivery-pipeline/
├── agent-manifest.yaml       # 元数据 + 依赖声明
├── SKILL.md                  # 包含 Team Template 的行为定义
├── composition.toml          # 编排 DAG 定义
├── hooks/                    # 编排级钩子
│   └── hooks.yaml
├── mcp_adapter.py            # MCP Server 适配器
├── main.py                   # 入口
├── pyproject.toml            # 包配置
└── tests/
    └── test_composition.py
```

**当前实现**：

```
agents/composite/feature-delivery-pipeline/
├── agent-manifest.yaml       # ✅ 已实现
├── SKILL.md                  # ✅ 已实现
└── pyproject.toml            # ✅ 已实现
```

> composition.toml、hooks/ 等为设计目标。编排由 Platform Router 通过 OrchestrationDSL 统一管理。

#### agent-manifest.yaml 规范

```yaml
name: doc-filler
version: 1.0.0
type: atomic  # atomic | composite
description: Word 文档模板填充专家

# 模型配置
model_config:
  recommended: "standard"
  fallback: "lightweight"

# 权限
permissions:
  mode: default  # default | plan | full_auto
  allowed_tools: [file_read, file_write, mcp__docx__*]
  denied_tools: [bash]
  path_rules:
    - pattern: "*.docx"
      access: read-write

# 依赖（Composite Agent 专用）
dependencies:
  atomic_agents:
    - requirements-analyzer
    - api-doc-generator

# MCP Server 依赖
mcp_servers:
  filesystem:
    transport: stdio
    command: "uvx"
    args: ["mcp-server-filesystem"]

# Hook 配置
hooks:
  pre_execution:
    - type: prompt
      prompt: "验证输入文件存在且格式正确"
      block_on_failure: true
  post_execution:
    - type: command
      command: "notify-send '文档填充完成'"
      block_on_failure: false
```

---

### 7.7 Agent 目录

**Atomic Agents（11，含 1 个自进化晋升的 good-skill）：**

| Name | Domain | Model Tier | Key Differentiator |
|------|--------|------------|-------------------|
| Requirements Analyzer | 软件工程 - 需求 | Powerful | 多轮对话追踪提问策略 |
| Doc Filler | 文档/内容 - 模板自动化 | Lightweight/Standard | 两阶段管道，样式继承链处理 |
| Code Review Specialist | 软件工程 - 代码质量 | Premium | 每语言规则数据库，跨文件推理 |
| Contract Analyzer | 文档/内容 - 法律分析 | Premium | 条款间依赖理解 |
| API Doc Generator | 软件工程 - 文档 | Standard | OpenAPI 3.1 标准 |
| Security Scanner | 质量/安全 - 应用安全 | Standard | 实时漏洞数据库集成 |
| Accessibility Auditor | 质量/安全 - 无障碍 | Lightweight/Standard | 87 条 WCAG 2.2 AA 标准 |
| Localization Specialist | 文档/内容 - 翻译与适配 | Standard | 术语表管理，语域识别 |
| Market Intelligence Analyst | 研究/分析 - 市场研究 | Standard | Porter/SWOT/PESTEL 方法论 |
| Test Suite Generator | 软件工程 - 测试 | Standard | 每范式测试策略 |
| Good Skill * | 通用 | Standard | 自进化自动晋升示例（from sk-1, effective_rate=0.9）|

**Composite Agents（5）：**

| Name | Orchestration | 编排模式 | Key Differentiator |
|------|---------------|---------|-------------------|
| Feature Delivery Pipeline | Requirements → [API Doc + Test + Review] | 顺序→并行 | Spec-driven 并行执行 |
| Document Compliance Gateway | [Legal + Accessibility + Localization] | 全并行 | 跨维度冲突检测 |
| CI/CD Quality Gate | [Security + Code Review + Test] | 全并行 | 多模型并行 |
| Competitive Intelligence Briefing | Market Intel → Doc Filler → [Localization] | 顺序链 | 原始研究 → 精装简报 |
| Product Documentation Suite | [API Doc + Code Review] → [Localization] | 并行→顺序→并行 | 文档-代码漂移验证 |

#### Agent 角色化设计

> **参考模块**: OpenHarness `src/openharness/coordinator/agent_definitions.py` — `AgentDefinition` 模型及内置角色 (Explore, Plan, Worker, Verification)

参考 OpenHarness 的 Agent 类型体系，Atomic Agent 可在声明式规格中指定角色类型（role），每个角色有预设的工具集约束和推荐模型。角色不是强制的——通用 Agent 不指定 role 时拥有完整工具集。

| 角色 | 工具集 | 推荐模型 | 目的 |
|------|--------|---------|------|
| **Explore** | glob, grep, file_read, web_fetch/web_search | haiku / lightweight | 快速代码/文档探索 |
| **Plan** | file_read, glob, grep（只读） | standard | 架构设计、方案规划 |
| **Worker** | 全部工具 | inherit | 通用实现、代码修改 |
| **Verification** | file_read, file_write（仅临时文件）, glob, grep | standard | 对抗性验证、测试 |

```yaml
---
name: codebase-explorer
type: atomic
role: explore          # 预设工具集约束
tools: [glob, grep, file_read, web_fetch]  # 可进一步限制
model_config:
  recommended: lightweight
effort: low
max_turns: 5
---
```

角色类型通过 §7.8 的声明式规格 `role` 字段指定。角色预设的工具集可作为白名单起点，开发者可通过 `tools` 字段进一步限制或扩展。

### 7.7.1 Agent 封装厚度指南

> **设计原则**: Agent 的封装厚度应匹配其领域复杂度。过度封装增加维护成本，封装不足则丢失领域价值。

**封装厚度判定矩阵：**

| 封装维度 | 薄封装（Runtime + Skill 即可） | 厚封装（完整 Atomic Agent） |
|---------|------------------------------|---------------------------|
| **流程** | 单步调用（prompt → result） | 多阶段管道（analyze → check → review） |
| **领域知识** | LLM 自身能力即可（如 SWOT 分析） | 需要规则库/标准库（如 OWASP Top 10、WCAG 2.2） |
| **分发需求** | 仅本机使用 | 需要跨团队安装、版本管理 |
| **编排需求** | 独立使用 | 被 Composite Agent 编排 |
| **进化需求** | 无 | 参与自进化（FIX/DERIVED/CAPTURED/Promotion） |

**当前 Agent 封装厚度评估：**

| Agent | 封装厚度 | 理由 |
|-------|---------|------|
| code-reviewer | 厚 | 三阶段管道 + 多语言规则库 + 评分系统 |
| doc-filler | 厚 | 两阶段管道 + 样式继承链 + 模板领域知识 |
| requirements-analyzer | 厚 | 多轮对话追踪策略 + 交互流程控制 |
| test-suite-generator | 中厚 | AST 解析 + 每范式测试策略 |
| security-scanner | 中厚 | OWASP Top 10 模式匹配 + 规则引擎 |
| accessibility-auditor | 中厚 | WCAG 2.2 AA 87 条标准库 |
| contract-analyzer | 中 | 条款依赖理解 + 多法域合规（核心是 LLM 能力） |
| localization-specialist | 薄 | 术语表管理 + 语域识别（核心是 API 翻译调用） |
| api-doc-generator | 中 | OpenAPI 3.1 标准生成 |
| market-intelligence-analyst | 薄 | Porter/SWOT/PESTEL 框架化方法论（LLM 本身具备） |
| good-skill | 薄 | 自进化晋升产物，任务简单 |

**薄封装 Agent 的替代路径**：

对于领域逻辑较薄的 Agent（如 market-intelligence-analyst），可通过以下方式按需生成而非预封装：

1. **Skill + Runtime 直出**：通过 Skill Evolution 的 CAPTURED 模式，从成功的 Runtime 交互中提取 Skill
2. **动态 Agent**：Runtime + SKILL.md 即可实现"一个 prompt 的专业化"，无需完整的 Agent Package 结构
3. **自进化晋升**：good-skill 已验证了从 Skill → Agent 的自动晋升路径，薄封装 Agent 可以按需进化而来

### 7.8 Agent Definition（声明式规格）

#### 7.8.1 声明式 Agent 规格概述

> **参考模块**: OpenHarness `src/openharness/coordinator/agent_definitions.py` — 完整 `AgentDefinition` 字段定义; deer-flow `packages/harness/deerflow/config/agents_config.py` — `AgentConfig` + `SOUL.md` 模式

声明式 Agent 规格借鉴 OpenHarness 的 `AgentDefinition` 模式，通过 YAML frontmatter 声明式地定义 Agent 的工具、模型、权限、Skill 等元数据。开发者无需修改代码即可调整 Agent 行为，实现行为可观测性与运行时配置的分离。

这种设计与 OrchestrationDSL 互补——TOML 定义编排层面（依赖图、任务分配），YAML 定义个体层面（工具权限、模型选择、记忆作用域）。

#### 7.8.2 字段参考表

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| name | string | 是 | Agent 唯一标识 |
| type | atomic/composite | 是 | Agent 类型 |
| description | string | 是 | 功能描述 |
| tools | list[string] | 否 | 允许的工具列表（白名单），空=全部 |
| denied_tools | list[string] | 否 | 禁止的工具列表（黑名单）|
| model | string | 否 | 推荐模型（如 haiku/sonnet/opus）|
| model_config.recommended | string | 否 | 推荐模型层级（lightweight/standard/powerful/premium）|
| model_config.fallback | string | 否 | 降级模型层级 |
| effort | low/medium/high | 否 | 推理努力程度 |
| permission_mode | default/plan/full_auto | 否 | 权限模式 |
| max_turns | int | 否 | 最大对话轮次 |
| skills | list[string] | 否 | 依赖的 Skill 列表 |
| mcp_servers | dict | 否 | 依赖的 MCP Server |
| hooks | dict | 否 | 生命周期钩子配置 |
| memory_scope | none/shared/isolated | 否 | 记忆作用域 |
| color | string | 否 | 终端显示颜色标识 |
| background | bool | 否 | 是否后台运行 |
| initial_prompt | string | 否 | 首次启动时自动发送的提示 |
| isolation | none/worktree | 否 | 文件系统隔离模式 |

#### 7.8.3 Atomic Agent 定义示例

```markdown
---
name: requirements-analyzer
type: atomic
description: 多轮对话分析模糊需求，输出结构化需求说明书
tools: [file_read, file_write, web_search, mcp__*]
denied_tools: [bash]
model_config:
  recommended: powerful
  fallback: standard
effort: high
permission_mode: default
max_turns: 12
memory_scope: shared
isolation: none
---

# Requirements Analyzer Agent

## 角色
你是一个专业的需求分析师，擅长通过多轮对话澄清模糊需求...

## 核心能力
- 需求完整性校验
- 术语标准化
- 优先级排序
```

#### 7.8.4 Composite Agent 定义示例

```markdown
---
name: feature-delivery-pipeline
type: composite
description: 需求驱动并行生成 API 文档、测试套件和代码审查
dependencies:
  - requirements-analyzer
  - api-doc-generator
  - test-suite-generator
  - code-reviewer
model_config:
  recommended: standard
  fallback: lightweight
permission_mode: default
max_turns: 20
hooks:
  pre_execution:
    - type: prompt
      prompt: "验证所有依赖 Agent 已就绪"
      block_on_failure: true
---

# Feature Delivery Pipeline

## 概述
端到端特性交付流水线，从需求分析到代码审查一体化...
```

#### 7.8.5 与 OrchestrationDSL 的关系

声明式 Agent 规格与 OrchestrationDSL 形成互补的分层架构：

| 维度 | YAML frontmatter | TOML（composition.toml）|
|------|-----------------|------------------------|
| **定义对象** | Agent 个体 | Agent 编排 |
| **声明内容** | 工具、模型、权限、记忆 | 依赖图、任务分配、加载策略 |
| **文件位置** | agent-manifest.yaml / SKILL.md | composition.toml |
| **修改时机** | 运行时可调 | 部署前确定 |

**协同模式**：YAML frontmatter 声明"我是谁"（身份与能力），TOML 声明"我们怎么协作"（依赖图与流程）。Platform Router 在加载 Composite Agent 时合并两者，生成完整的运行时配置。

---
