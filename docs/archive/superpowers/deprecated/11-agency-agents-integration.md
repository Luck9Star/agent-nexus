# Agency Agents 专家池集成方案

> Agent Nexus Design Doc — §11B Agency Agents Integration：将 `agency-agents` 作为专家画像内容源接入 Agent Nexus，用于 Composite Agent 的动态任务拆解、专家选择、结果集成和质量门禁。
>
> **Status**: Proposal
> **Scope**: Content pack import, internal expert planner, task-composer workflow, dynamic composition, integration workflow
> **External source**: <https://github.com/msitarzewski/agency-agents>

## 1. 决策结论

建议集成，但只做 **专家内容池 + Adapter 导入 + Composite Agent 动态编排**，不把 `agency-agents` 作为运行时依赖。

`agency-agents` 的价值在于它提供大量已经结构化的专家角色、工作流程、交付模板和成功指标。Agent Nexus 的价值在于它已经拥有 MCP-native 运行边界、Atomic/Composite Agent 架构、TaskGraph、IPC、ProcessManager、权限模型、Git-based 分发和质量门禁。

因此，最佳集成方式是：

```text
User Task
  -> Task Intake Composite Agent
  -> Expert Planner
  -> agency-agents Markdown Expert Config
  -> Agency Importer
  -> Nexus Expert Profile + Markdown Config Index
  -> Capability Registry
  -> Generic Expert Agent + Selected Profile
  -> Virtual Specialist Workers
  -> Integrator
  -> QA / GitNexus Gate
```

核心原则：

- `agency-agents` 是专家知识库，不是 Agent Nexus 的 runtime。
- Agent Nexus 是执行、编排、权限、审计和质量门禁的唯一控制面。
- 只实现一个 Nexus-native 的通用专家 Agent；不同专家通过不同 Markdown 配置文件实例化。
- 导入 N 个专家时，只增加 N 份 Expert Profile 和路由元数据，不生成 N 份重复 Agent 代码。
- 导入后的专家默认是 `persona-only`，不直接拥有 shell、文件写入、网络或外部工具权限。
- 用户不需要知道有哪些专家，也不需要手动选择专家；只需要发布任务。
- Composite Agent 或 Expert Planner 根据任务自动拆分、自动选择专家、自动组合结果。
- 最终输出必须通过固定的 Integrator 和 QA Gate 汇总，避免把多个专家的原始输出直接暴露给用户。

### 1.1 核心实现判断

`agency-agents` 的专家资产本质上是一组 Markdown 专家配置文件。每个文件描述一个角色的人设、职责边界、工作流、输出格式和成功标准，而不是一个需要独立运行的程序。

因此，Agent Nexus 不需要为每个专家生成独立 Atomic Agent。更优实现是新增一个共享的 `generic-expert-agent`，它在运行时读取指定的 Expert Profile 和 Markdown 配置，将该配置注入系统提示词、输出契约和路由元数据中。这样同一个 Agent 运行器可以自动实例化大量专家：

```text
nexus.generic-expert-agent + profiles/software-architect/source.md
  = agency.software-architect

nexus.generic-expert-agent + profiles/security-engineer/source.md
  = agency.security-engineer

nexus.generic-expert-agent + profiles/test-results-analyzer/source.md
  = agency.test-results-analyzer
```

这让集成的主要工作从“生成很多 Agent 代码”转为“导入、规范化、索引和约束很多专家配置”。运行时保持单一，专家能力通过配置扩展。

### 1.2 用户交互目标

目标产品形态不是“专家市场”或“专家列表”，而是“任务入口”。用户只提交任务、上下文和少量约束，系统内部完成专家规划。

```text
用户输入任务
  -> Composite Agent 接收任务
  -> Expert Planner 判断任务类型和风险
  -> 自动拆分子任务
  -> 自动选择合适专家画像
  -> 并行或串行执行虚拟专家
  -> Integrator 汇总专家产物
  -> QA Gate 验证输出质量和门禁
  -> 返回一个统一答案、方案、报告或执行结果
```

专家库对用户默认不可见。专家选择原因、专家产物和临时 DAG 可以作为 debug trace、审计报告或开发者模式信息保留，但不应成为普通用户完成任务的前置知识。

## 2. 背景与动机

Agent Nexus 当前已经支持两类 Agent：

| 类型 | 当前职责 | 与本方案关系 |
|------|----------|--------------|
| Atomic Agent | 单一专业能力，作为 Worker 执行任务 | 导入的专家画像可以被物化为轻量 Atomic Worker |
| Composite Agent | 通过 TOML DAG 编排多个 Atomic Agent | 新增动态专家选择能力，按任务组建专家团队 |

`agency-agents` 与 Agent Nexus 是互补关系：

| 维度 | agency-agents | Agent Nexus |
|------|---------------|-------------|
| 主要资产 | Markdown 专家角色、流程、模板 | Runtime、编排、MCP、权限、分发 |
| 抽象重点 | Persona、workflow、deliverables | Agent package、capability、tools、TaskGraph |
| 执行能力 | 无内置 runtime | Python Runtime、MCP Gateway、IPC |
| 质量控制 | 结构 lint 为主 | Manifest check、权限审计、测试、GitNexus 门禁 |
| 适合集成层 | 内容源、模板源、路由元数据 | 控制面、执行面、质量面 |

本方案的目标是让 Composite Agent 不再只依赖预先写死的 Atomic Agent 列表，而是能基于任务需要自动规划专家团队。普通用户只感知“发布任务 -> 获得集成结果”，系统内部完成专家选择、任务分派、结果汇总和质量验证。

## 3. 目标与非目标

### 3.1 目标

1. 将 `agency-agents` 中精选专家导入为 Agent Nexus 可索引、可路由、可编排的 Expert Profile。
2. 为 Composite Agent 增加基于 capability、tag、output contract、risk level 的动态专家选择能力。
3. 支持基于任务自动生成临时 Composite DAG，完成任务拆解、并行专家执行、结果集成和验证。
4. 保留 Agent Nexus 现有运行边界，所有工具、权限、文件写入和代码修改继续受 Nexus 控制。
5. 提供可重复、可审计、可版本锁定的内容包导入流程。
6. 通过一个通用专家 Agent 读取不同 Markdown 配置，批量生成 Virtual Atomic Agent。
7. 提供用户不可见的 Expert Planner，使用户只发布任务即可获得组合后的最终结果。

### 3.2 非目标

1. 不直接运行 `agency-agents/scripts/install.sh`。
2. 不把 `agency-agents` 作为 Agent Nexus 的 runtime、scheduler 或 subprocess manager。
3. 不全量无筛选导入所有角色。
4. 不让导入角色默认获得 shell、file write、network、MCP tool call 权限。
5. 不用 persona 描述替代 Agent Nexus 的 typed capability contract。
6. 不为每个 Markdown 专家生成独立 Python/Rust Agent 工程，避免重复代码和维护成本。
7. 不把“浏览专家列表、手动挑选专家”作为主用户流程；这只适合开发者调试和治理。

## 4. 总体架构

### 4.1 逻辑组件

```text
┌──────────────────────────────────────────────────────────┐
│ External Content Source                                  │
│  agency-agents repo                                      │
│  - engineering/*.md                                      │
│  - testing/*.md                                          │
│  - specialized/*.md                                      │
└─────────────────────────────┬────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ Agency Importer                                          │
│  - clone / fetch pinned ref                              │
│  - parse markdown frontmatter                            │
│  - normalize body sections                               │
│  - validate license / structure / content policy          │
└─────────────────────────────┬────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ Expert Profile Store                                     │
│  - source metadata                                       │
│  - role prompt                                           │
│  - capability tags                                       │
│  - output contracts                                      │
│  - permissions                                           │
│  - quality metadata                                      │
└─────────────────────────────┬────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ Dynamic Composite Planner                                │
│  - task classification                                   │
│  - subtask decomposition                                 │
│  - specialist selection                                  │
│  - temporary DAG generation                              │
└─────────────────────────────┬────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ Platform Router / TaskGraph                              │
│  - run specialist workers                                │
│  - enforce timeout / retry / max_parallel                 │
│  - collect artifacts                                     │
└─────────────────────────────┬────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ Integrator + QA Gate                                     │
│  - merge outputs                                         │
│  - resolve conflicts                                     │
│  - validate contracts                                    │
│  - run GitNexus gates for code-changing workflows         │
└──────────────────────────────────────────────────────────┘
```

### 4.2 运行时关系

导入的专家不需要每个都生成独立 Python Agent 代码。推荐引入一个共享的 `generic-expert-agent` 运行器：

```text
Expert Profile
  + shared generic-expert-agent runtime
  + task-specific prompt
  + output contract
  + restricted permissions
  = Virtual Atomic Agent
```

这样可以避免为 100+ 个角色生成重复代码，也能保留 Agent Nexus 的进程、权限和上下文控制。

### 4.3 通用专家 Agent 模式

`generic-expert-agent` 是唯一需要维护的执行实现。它负责读取专家配置、构造上下文、调用模型并按输出契约返回 artifact。专家身份由 profile 决定，而不是由不同代码目录决定。

```text
Virtual Agent ID              Shared Runtime              Profile Config
──────────────────────────    ────────────────────────    ─────────────────────────────────────
agency.software-architect  ->  nexus.generic-expert-agent  profiles/software-architect/source.md
agency.code-reviewer       ->  nexus.generic-expert-agent  profiles/code-reviewer/source.md
agency.security-engineer   ->  nexus.generic-expert-agent  profiles/security-engineer/source.md
```

运行时绑定可以通过 manifest 参数、环境变量或 router request 传入：

```yaml
runtime_binding:
  runner: nexus.generic-expert-agent
  implementation: python-pydanticai
  profile_id: agency.software-architect
  profile_path: profiles/software-architect/expert-profile.yaml
  prompt_source: profiles/software-architect/source.md
  output_contract: profiles/software-architect/output-contract.yaml
```

该模式带来四个直接收益：

1. 新增专家只需要新增 Markdown 配置和索引记录。
2. 修复运行器 bug 时，所有专家同时受益。
3. 权限、超时、上下文预算和输出校验集中实现。
4. Selector 可以把专家当作普通 Atomic Agent 路由，而 Router 只需要解析到同一个共享 runner。

### 4.4 实现语言决策

首版 `generic-expert-agent` 应使用 **PydanticAI** 实现，而不是重新开发一个类似 `nanobot` 的独立智能体运行时。

原因：

1. Agent Nexus 已经有 Router、TaskGraph、ProcessManager、权限模型、MCP Gateway 和 GitNexus Gate，不需要重复实现调度和控制面。
2. `generic-expert-agent` 的职责是 leaf executor：读取 profile、装配 prompt、调用模型、校验结构化输出并返回 artifact。
3. PydanticAI 已经适合动态 instructions、依赖注入、工具接入、MCP 集成和 typed output，能快速验证专家库质量。
4. 独立开发 `nanobot` 类 runtime 会引入第二套生命周期、工具权限、状态管理和调度模型，增加架构分叉风险。

推荐分层如下：

| 层级 | 推荐实现 | 职责 |
|------|----------|------|
| Expert Planner | Agent Nexus Composite / Rust Router | 拆任务、选专家、生成临时 DAG、控制权限和并发 |
| Generic Expert Agent v1 | PydanticAI | profile 注入、模型调用、结构化输出、artifact 返回 |
| Integrator / QA Gate | Agent Nexus | 合并结果、验证契约、执行 GitNexus 门禁 |
| Generic Expert Executor v2 | Rust 可选 | 在专家库和流程稳定后，减少 Python 子进程并统一平台 runtime |

Rust 版值得做，但它应是后续的 **infra-native executor**，不是新的 agent framework。Rust 版本只需要覆盖 PydanticAI leaf executor 的最小职责：

```text
ExpertProfile YAML
  -> load source.md / normalized-profile.md
  -> assemble prompt
  -> call model provider
  -> validate JSON artifact
  -> return IPC / MCP response
```

Rust 实现建议使用：

| 能力 | Rust 选择 | 说明 |
|------|-----------|------|
| 异步运行 | `tokio` | 复用现有 workspace runtime |
| 序列化 | `serde` / `serde_json` / `serde_yml` | 复用现有数据模型生态 |
| JSON Schema | `schemars` | 由 Rust struct 生成输出契约 schema |
| Schema 校验 | `jsonschema` | 校验专家 artifact 是否满足 output contract |
| OpenAI-compatible LLM 调用 | `async-openai` | 适合 Responses / Chat / streaming / tool call 基础能力 |
| MCP server/client | `rmcp` | Rust MCP SDK，用于 MCP 暴露或工具接入 |
| 多 provider / RAG 可选层 | `rig-core` | 只有在需要跨 provider agent abstraction、embedding 或 vector store 时引入 |

阶段判断：

- POC 和前几轮 eval 使用 PydanticAI，优先验证专家规划和输出质量。
- 当 `task-composer`、output contract、profile schema 和 eval 指标稳定后，再实现 Rust `ap-expert` crate。
- Rust `ap-expert` 不应重新实现 Expert Planner、DAG、权限或 QA Gate；这些继续由 Agent Nexus 控制面负责。
- 如果未来需要脱离 Agent Nexus 独立运行、多 agent 自主调度或长时自治，再重新评估是否需要 nanobot-like runtime。

## 5. 数据模型设计

### 5.1 Expert Profile

`ExpertProfile` 是导入后的规范化专家画像。它不是外部 Markdown 的简单复制，而是 Agent Nexus 的路由和编排单位。

```yaml
id: agency.software-architect
name: Software Architect
source:
  kind: git
  repo: https://github.com/msitarzewski/agency-agents
  ref: 29c2a88fad8ab6e340c7ee6b97d71ee1736920e0
  path: engineering/engineering-software-architect.md
  license: MIT

profile:
  category: engineering
  description: System design, DDD, architectural patterns, trade-off analysis
  source_md_path: source.md
  normalized_prompt_path: normalized-profile.md
  imported_at: "2026-04-25"

capabilities:
  - system_design
  - architecture_review
  - tradeoff_analysis
  - decomposition

routing:
  task_types:
    - architecture_review
    - feature_planning
    - integration_design
  positive_signals:
    - "architecture"
    - "trade-off"
    - "system design"
    - "bounded context"
  negative_signals:
    - "pixel-perfect UI"
    - "production incident"

runtime:
  mode: persona_only
  runner: nexus.generic-expert-agent
  implementation: python-pydanticai
  rust_executor: planned
  instance_strategy: virtual_atomic_agent
  profile_arg: "--profile"
  profile_path: expert-profile.yaml
  model_tier: standard
  max_context_tokens: 12000

permissions:
  mode: plan
  allowed_tools: []
  denied_tools:
    - bash
    - file_write
    - network

output_contract:
  artifact_type: architecture_plan
  required_sections:
    - context
    - assumptions
    - proposed_design
    - tradeoffs
    - risks
    - next_steps

quality:
  status: experimental
  eval_score: null
  human_reviewed: false
```

### 5.2 Capability Taxonomy

专家选择不能只靠自然语言名字。需要建立一组稳定 capability taxonomy。

第一阶段建议支持以下 capability：

| Capability | 说明 | 示例专家 |
|------------|------|----------|
| `system_design` | 系统架构、模块边界、技术取舍 | Software Architect |
| `backend_design` | API、数据库、服务端架构 | Backend Architect |
| `code_review` | 代码质量、安全、可维护性审查 | Code Reviewer |
| `security_review` | 威胁建模、权限、漏洞风险 | Security Engineer |
| `reliability_review` | SLO、故障、可观测性、运维风险 | SRE |
| `test_design` | 测试策略、覆盖率、测试结果分析 | Test Results Analyzer |
| `technical_writing` | 方案、API 文档、教程 | Technical Writer |
| `codebase_onboarding` | 只读代码理解、路径追踪 | Codebase Onboarding Engineer |
| `tool_evaluation` | 工具选型、技术评估 | Tool Evaluator |
| `lsp_indexing` | LSP、代码索引、语义图 | LSP/Index Engineer |

后续可以通过 Self-Evolution Engine 根据真实任务效果调整 capability 权重。

### 5.3 Output Contract

每个专家必须声明输出契约，Integrator 才能稳定合并结果。

```yaml
output_contract:
  artifact_type: risk_report
  format: markdown
  required_sections:
    - findings
    - severity
    - affected_components
    - mitigation
  constraints:
    max_findings: 10
    require_evidence: true
```

输出契约的作用：

1. 限制专家发散。
2. 让 Integrator 可以按结构合并。
3. 让 QA Gate 可以检查缺失章节。
4. 为后续 eval 和自进化提供稳定样本。

## 6. 导入流程

### 6.1 CLI 设计

建议新增 `agent-nexus agency` 命令组，避免和通用 install 混淆。

```bash
# 预览可导入角色
agent-nexus agency list \
  --repo https://github.com/msitarzewski/agency-agents \
  --ref main

# 导入精选 allowlist
agent-nexus agency import \
  --repo https://github.com/msitarzewski/agency-agents \
  --ref 29c2a88fad8ab6e340c7ee6b97d71ee1736920e0 \
  --allowlist config/agency-agents.allowlist.yaml \
  --runner nexus.generic-expert-agent \
  --virtual

# 校验导入结果
agent-nexus agency check

# 查看已导入专家
agent-nexus search agents --source agency-agents
```

### 6.2 Allowlist

第一阶段必须使用 allowlist，不做全量导入。

```yaml
source:
  repo: https://github.com/msitarzewski/agency-agents
  ref: 29c2a88fad8ab6e340c7ee6b97d71ee1736920e0

agents:
  - source_path: engineering/engineering-software-architect.md
    id: agency.software-architect
    capabilities: [system_design, architecture_review, tradeoff_analysis]
    output_contract: architecture_plan

  - source_path: engineering/engineering-code-reviewer.md
    id: agency.code-reviewer
    capabilities: [code_review, security_review, maintainability_review]
    output_contract: review_report

  - source_path: engineering/engineering-security-engineer.md
    id: agency.security-engineer
    capabilities: [security_review, threat_modeling]
    output_contract: risk_report
```

### 6.3 导入步骤

```text
agency import
  -> fetch repo at pinned ref
  -> read allowlist
  -> parse each Markdown frontmatter
  -> extract body sections
  -> normalize name / category / description
  -> infer default capabilities
  -> merge allowlist overrides
  -> assign output contract
  -> run content policy validation
  -> generate ExpertProfile package
  -> generate runtime binding to nexus.generic-expert-agent
  -> update local registry and lockfile
```

导入器的输出重点是 profile 数据，而不是运行时代码。每个 Markdown 文件对应一个 Expert Profile；所有 Expert Profile 都绑定到同一个 `nexus.generic-expert-agent`。后续新增专家时，导入器只需要更新 allowlist、profile 目录、`index.yaml` 和 lockfile。

### 6.4 生成目录结构

```text
~/.agent-nexus/agents/agency-agents/
├── source.lock.yaml
├── index.yaml
└── profiles/
    ├── software-architect/
    │   ├── expert-profile.yaml
    │   ├── normalized-profile.md
    │   ├── source.md
    │   └── output-contract.yaml
    ├── code-reviewer/
    │   ├── expert-profile.yaml
    │   ├── normalized-profile.md
    │   ├── source.md
    │   └── output-contract.yaml
    └── security-engineer/
        ├── expert-profile.yaml
        ├── normalized-profile.md
        ├── source.md
        └── output-contract.yaml
```

`source.md` 保留原始内容用于审计；`normalized-profile.md` 是规范化后给 `generic-expert-agent` 使用的版本。

目录中的每个专家包都是数据包，不是独立 Agent 工程。它不需要 `agent.py`、`pyproject.toml` 或单独的依赖环境。运行时由 `nexus.generic-expert-agent` 提供，profile 目录只负责声明“这个虚拟专家是谁、使用哪份 Markdown、有哪些 capability、输出必须满足什么契约”。

如果需要兼容现有 Agent 安装视图，可以为每个 profile 生成轻量 manifest wrapper：

```yaml
name: agency.software-architect
agent_type: atomic
runtime:
  runner: nexus.generic-expert-agent
  args:
    - --profile
    - ~/.agent-nexus/agents/agency-agents/profiles/software-architect/expert-profile.yaml
permissions:
  mode: plan
```

这个 wrapper 只做身份映射和运行时绑定，不复制运行器代码。

## 7. Dynamic Composite Agent 编排

### 7.1 编排目标

现有 Composite Agent 主要通过静态 TOML DAG 编排固定 Atomic Agent。集成专家池后，Composite Agent 应升级为“任务入口 + 内部专家规划器”：用户提交任务，Composite Agent 自动拆分任务、选择专家画像、组合执行结果，并只返回最终集成产物。

推荐采用 Hybrid 模式：

- 工作流骨架保持静态。
- 每个阶段的专家由 Expert Planner 动态选择。
- Integrator 和 QA Gate 固定。
- 用户界面不暴露专家选择步骤，除非开启 debug 或 audit 模式。

这样既有自动组队能力，又避免每次完全由 LLM 自由生成流程。系统可以在内部形成专家团队，但用户仍然只面对一个 Composite Agent。

### 7.2 标准流程

```text
1. Task Intake
   接收用户任务和上下文。

2. Task Classification
   判断任务类型、风险等级、是否涉及代码修改。

3. Decomposition
   拆成结构化子任务，声明需要的 capability 和输出类型。

4. Expert Planning
   从 Expert Profile Store 中选择 1-5 个专家，并为每个专家生成子任务说明。

5. DAG Generation
   生成临时 composition graph。

6. Specialist Execution
   Platform Router 将虚拟专家解析为 `generic-expert-agent + profile`，并行执行专家任务。

7. Integration
   Integrator 合并所有 artifact，解决冲突。

8. Validation
   QA Gate 检查输出契约、风险和 GitNexus 门禁。

9. Final Response
   输出统一方案、报告或执行结果。默认不展示专家清单，只展示必要依据和最终结论。
```

### 7.3 示例：架构方案任务

用户任务：

```text
分析并设计 agency-agents 与 Agent Nexus 的集成方案。
```

Expert Planner 内部生成：

```yaml
task_type: integration_design
risk_level: medium
requires_code_change: false

subtasks:
  - id: architecture
    goal: design integration architecture
    needed_capabilities: [system_design, agent_orchestration]
    output_contract: architecture_plan

  - id: runtime_risk
    goal: identify runtime, permission, and lifecycle risks
    needed_capabilities: [security_review, reliability_review]
    output_contract: risk_report

  - id: implementation_plan
    goal: define phased implementation roadmap
    needed_capabilities: [technical_planning, tool_evaluation]
    output_contract: implementation_plan
```

Expert Planner 内部选择：

```yaml
selected_specialists:
  - subtask: architecture
    agent: agency.software-architect
  - subtask: runtime_risk
    agent: agency.security-engineer
  - subtask: runtime_risk
    agent: agency.sre
  - subtask: implementation_plan
    agent: agency.tool-evaluator
```

临时 DAG。该 DAG 默认只进入 trace，不直接展示给用户：

```toml
[composition]
name = "dynamic-agency-integration-design"
max_parallel = 3

[[tasks]]
id = "architecture"
agent = "agency.software-architect"
output = "architecture_plan"

[[tasks]]
id = "runtime_risk"
agent = "agency.security-engineer"
output = "risk_report"

[[tasks]]
id = "reliability_risk"
agent = "agency.sre"
output = "risk_report"

[[tasks]]
id = "implementation_plan"
agent = "agency.tool-evaluator"
output = "implementation_plan"

[[tasks]]
id = "integrate"
agent = "nexus.integrator"
blocked_by = ["architecture", "runtime_risk", "reliability_risk", "implementation_plan"]
output = "final_plan"

[[tasks]]
id = "validate"
agent = "nexus.qa-gate"
blocked_by = ["integrate"]
output = "validated_plan"
```

### 7.4 示例：代码修改任务

代码修改任务需要额外门禁。

```text
User Task
  -> Planner
  -> GitNexus impact analysis
  -> Specialist design/review
  -> Implementation worker
  -> Code Reviewer
  -> GitNexus detect_changes
  -> Final response
```

规则：

1. 任何专家都不能直接绕过 GitNexus impact analysis。
2. `persona-only` 专家默认只能产出建议和计划。
3. 代码写入只能由具备明确权限的 implementation worker 执行。
4. 提交前必须运行 `gitnexus_detect_changes()`。

## 8. Expert Planner 与内部 Selector 设计

> Specialist Selector 是内部实现名称；产品能力应对外表现为 Expert Planner 或 `task-composer` 的自动规划能力。

Specialist Selector 是 Expert Planner 的内部模块，不是用户交互界面。它的输入来自 Task Classification 和 Decomposition，输出进入临时 DAG 或 TaskGraph。用户不需要看到专家 ID，也不需要理解 capability taxonomy。

### 8.1 内部选择输入

```yaml
selection_request:
  user_visible: false
  task_type: architecture_review
  goal: design integration plan
  required_capabilities:
    - system_design
    - agent_orchestration
  optional_capabilities:
    - security_review
    - technical_writing
  constraints:
    max_agents: 3
    require_independent_review: true
    permissions: plan_only
    expose_specialists_to_user: false
```

### 8.2 评分因子

| 因子 | 权重 | 说明 |
|------|------|------|
| Capability match | 高 | required capability 是否覆盖 |
| Task type match | 高 | routing.task_types 是否匹配 |
| Output contract match | 中 | 是否能产出需要的 artifact |
| Quality score | 中 | eval_score、human_reviewed、成功率 |
| Token cost | 中 | role prompt 体积、上下文预算 |
| Diversity | 中 | 避免选多个高度重复专家 |
| Permission fit | 高 | 任务要求与权限是否匹配 |
| Source trust | 中 | 来源、版本、审查状态 |

### 8.3 选择策略

第一阶段使用确定性策略：

```text
1. 过滤 required_capabilities 不满足的专家
2. 过滤权限不匹配的专家
3. 按 task_type、capability overlap、quality score 排序
4. 做 diversity 去重
5. 选择 top N
```

第二阶段可加入 LLM-based reranker，但最终选择结果必须可解释。解释默认写入 trace，不直接进入最终用户答案：

```yaml
selected:
  - agent: agency.software-architect
    score: 0.91
    reasons:
      - matches system_design
      - matches integration_design task type
      - outputs architecture_plan
visibility:
  final_response: hidden
  audit_trace: visible
  debug_mode: visible
```

### 8.4 用户不可见策略

Expert Planner 需要区分“内部可解释”和“用户可见”。内部必须保存专家选择原因，用于审计、调试、eval 和自进化；外部默认只展示综合后的结论。

```yaml
planner_trace:
  stored: true
  default_user_visibility: hidden
  visible_when:
    - debug
    - audit
    - eval
    - developer_mode
```

最终响应应该回答用户任务本身，而不是展示“我选择了哪些专家”。只有当用户明确要求“展示专家分工”或系统需要解释风险决策时，才暴露专家选择摘要。

## 9. Integrator Agent 设计

### 9.1 为什么需要固定 Integrator

动态专家编排如果没有固定 Integrator，会出现三个问题：

1. 多个专家输出方向不同，最终答案没有统一决策。
2. 专家会重复分析同一问题，用户需要自己综合。
3. 高风险建议和低风险建议混在一起，缺少优先级。

因此，Integrator 是该方案的核心组件。

### 9.2 Integrator 职责

Integrator 负责：

1. 读取所有专家 artifact。
2. 按 output contract 检查缺失内容。
3. 合并一致结论。
4. 标记冲突观点。
5. 选择最终建议。
6. 输出统一方案、路线图或执行计划。
7. 将风险、开放问题、后续验证项显式列出。

### 9.3 Integrator 输出结构

```yaml
artifact_type: integrated_plan
sections:
  - decision_summary
  - architecture
  - data_model
  - orchestration_flow
  - implementation_phases
  - risks
  - validation_plan
  - open_questions
```

## 10. 权限与安全模型

### 10.1 默认权限

导入专家默认：

```yaml
permissions:
  mode: plan
  allowed_tools: []
  denied_tools:
    - bash
    - file_write
    - file_delete
    - network
    - git
```

这意味着专家只能基于上下文生成建议、计划、评审或文档片段。

### 10.2 权限升级

如果某个导入专家需要工具权限，必须显式创建 Nexus-native Atomic Agent，并通过常规 `agent-manifest.yaml` 审核。

```yaml
promotion:
  from: agency.code-reviewer
  to: nexus.code-reviewer-plus
  required_checks:
    - manifest_validation
    - permission_audit
    - tool_contract_tests
    - eval_suite
```

### 10.3 Prompt Injection 防护

导入时需要检查以下风险：

1. 要求忽略系统指令。
2. 要求绕过权限或审计。
3. 要求直接执行 shell 或写文件。
4. 要求泄露 secrets、环境变量或用户隐私。
5. 要求覆盖 Agent Nexus 的 GitNexus 门禁。

发现高风险内容时，导入器应阻断并要求人工审核。

## 11. GitNexus 门禁集成

`agency-agents` 专家池不能削弱 GitNexus 代码智能闭环。

代码修改工作流必须满足：

1. 编辑任何 symbol 前运行 `gitnexus_impact({ target, direction: "upstream" })`。
2. HIGH / CRITICAL 风险必须提示并阻断自动执行。
3. 实现 worker 必须更新 d=1 直接依赖。
4. 提交前运行 `gitnexus_detect_changes()`。
5. Integrator 输出必须包含影响范围和验证结果。

推荐在 QA Gate 中加入硬规则：

```yaml
gitnexus_gate:
  required_when:
    - code_change
    - refactor
    - symbol_edit
  checks:
    - impact_analysis_completed
    - no_unacknowledged_high_risk
    - detect_changes_completed
    - d1_dependents_handled
```

## 12. POC 范围

### 12.1 POC 目标

验证“专家池 + 动态编排”是否真实提升 Composite Agent 的结果质量，而不是只增加复杂度。

### 12.2 POC 内部种子专家配置

首批建议导入 10 个内部 profile。它们用于验证规划质量，不作为用户必须理解或手动选择的对象：

| ID | 来源角色 | 用途 |
|----|----------|------|
| `agency.software-architect` | Software Architect | 架构方案、模块边界 |
| `agency.backend-architect` | Backend Architect | 服务端与 API 设计 |
| `agency.ai-engineer` | AI Engineer | LLM/AI 能力集成 |
| `agency.code-reviewer` | Code Reviewer | 代码审查 |
| `agency.security-engineer` | Security Engineer | 安全风险 |
| `agency.sre` | SRE | 可靠性与运维风险 |
| `agency.test-results-analyzer` | Test Results Analyzer | 测试结果分析 |
| `agency.technical-writer` | Technical Writer | 文档输出 |
| `agency.codebase-onboarding-engineer` | Codebase Onboarding Engineer | 只读代码理解 |
| `agency.tool-evaluator` | Tool Evaluator | 工具和方案评估 |

可以额外加入：

| ID | 来源角色 | 用途 |
|----|----------|------|
| `agency.lsp-index-engineer` | LSP/Index Engineer | 代码智能、语义索引、GitNexus 相关设计 |
| `agency.agents-orchestrator` | Agents Orchestrator | 仅作为编排流程参考，不直接作为顶层 runtime |

### 12.3 POC 工作流

POC 实现一个新的 Composite Agent：

```text
task-composer
```

输入：

```yaml
task: string
context_paths: list[string]
mode: plan | review | implementation_plan
constraints:
  max_parallel: 3
  risk_level: auto
  expose_planner_trace: false
```

输出：

```yaml
result:
  summary: markdown
  recommendation: markdown
  risks: list
  validation: list
trace:
  selected_specialists: hidden_by_default
  subtask_plan: hidden_by_default
  specialist_artifacts: hidden_by_default
```

### 12.4 POC 成功标准

必须通过：

1. 导入流程可重复，且锁定 source commit。
2. Expert Planner 能在 trace 中解释为什么选择某些专家。
3. 专家默认无写权限，不能绕过权限模型。
4. Integrator 能产出统一结构化结论。
5. 对同一任务，动态专家编排比单一通用 agent 产出更完整的风险和路线图。
6. 新增一个专家只需要新增 Markdown allowlist 条目和 profile 数据，不需要新增 Agent 代码。
7. 用户只提交任务即可获得结果，不需要知道、搜索或手动选择专家。

应该通过：

1. Prompt token 成本可控。
2. 专家输出可被 eval。
3. 失败时能定位到具体专家、任务或 Integrator。
4. 用户不需要理解 `agency-agents` 原始仓库结构。

## 13. 分阶段实施计划

### Phase A：文档与数据模型

目标：确定内容包边界和 Expert Profile 规范。

任务：

- [ ] 定义 `ExpertProfile` schema。
- [ ] 定义 capability taxonomy。
- [ ] 定义 output contract schema。
- [ ] 创建 allowlist 格式。
- [ ] 明确 source attribution 和 license metadata。

交付物：

- `docs/11-agency-agents-integration.md`
- `schemas/expert-profile.schema.json`
- `schemas/output-contract.schema.json`
- `config/agency-agents.allowlist.yaml`

### Phase B：Importer

目标：从 pinned git ref 导入精选专家。

任务：

- [ ] 实现 markdown frontmatter parser。
- [ ] 实现 allowlist loader。
- [ ] 实现 source lock。
- [ ] 实现 content policy validation。
- [ ] 生成 profile package。
- [ ] 生成 virtual manifest wrapper 和 runtime binding。
- [ ] 更新 local registry。

命令：

```bash
agent-nexus agency import --allowlist config/agency-agents.allowlist.yaml
agent-nexus agency check
```

### Phase C：Generic Expert Agent

目标：先用 PydanticAI 实现一个通用专家 Agent，让 Expert Profile 可以作为 Virtual Atomic Agent 执行。

任务：

- [ ] 实现共享 `nexus.generic-expert-agent`，首版使用 PydanticAI。
- [ ] 支持按 profile 注入 role prompt。
- [ ] 支持 output contract enforcement。
- [ ] 支持 plan-only 权限。
- [ ] 支持 artifact 输出。
- [ ] 支持通过 manifest 参数或 router request 选择不同 profile。
- [ ] 明确运行器只做 leaf executor，不承载 DAG 调度、专家选择或权限治理。

### Phase D：Specialist Selector

目标：Composite Agent 能按任务选择专家。

任务：

- [ ] 实现 deterministic selector。
- [ ] 支持 capability filtering。
- [ ] 支持 top-N ranking。
- [ ] 输出 selection reasons。
- [ ] 加入 diversity 去重。

### Phase E：Dynamic Composite Planner

目标：生成临时 DAG 并交给 Platform Router 执行。

任务：

- [ ] 定义 dynamic composition artifact。
- [ ] 将 subtasks 映射到 selected specialists。
- [ ] 与 TaskGraph 集成。
- [ ] 保留 max_parallel、timeout、retry 控制。

### Phase F：Integrator 和 QA Gate

目标：将多专家输出合并成一个可执行结论。

任务：

- [ ] 实现 fixed Integrator Agent。
- [ ] 实现 output contract validation。
- [ ] 实现 conflict detection。
- [ ] 实现 GitNexus gate hook。
- [ ] 输出 integrated artifact。

### Phase G：Eval 与自进化

目标：衡量专家编排是否真的变好。

任务：

- [ ] 准备 20 个典型任务 eval。
- [ ] 对比 single-agent、static-composite、dynamic-composite。
- [ ] 记录 specialist selection success rate。
- [ ] 将高质量专家标记为 `human_reviewed` 或提升 quality score。
- [ ] 接入 Self-Evolution Engine 优化 routing metadata。

### Phase H：Rust Native Expert Executor（可选）

目标：在 PydanticAI 版本验证成功后，实现 Rust 原生专家执行器，降低 Python 子进程成本并统一平台 runtime。

任务：

- [ ] 新增 `ap-expert` crate。
- [ ] 用 `serde` / `serde_yml` 加载 `ExpertProfile`。
- [ ] 用 `schemars` 生成 output contract schema。
- [ ] 用 `jsonschema` 校验专家 artifact。
- [ ] 用 `async-openai` 调用 OpenAI-compatible 模型。
- [ ] 用 `rmcp` 暴露 MCP server/client 能力。
- [ ] 与 PydanticAI 版本做同一批 eval 的输出一致性对比。

非目标：

- [ ] 不实现新的 DAG scheduler。
- [ ] 不实现新的权限系统。
- [ ] 不实现新的 nanobot-like agent runtime。

## 14. 测试计划

| 层级 | 测试内容 |
|------|----------|
| Unit | Markdown parser、allowlist parser、schema validation、selector scoring |
| Integration | 导入 pinned repo、生成 profile package、registry search |
| Router | 动态 DAG 生成、TaskGraph 状态流转、并行限制 |
| Permission | persona-only 不能调用 shell/file write/network |
| QA | output contract 缺失章节时失败 |
| GitNexus | code-change workflow 必须触发 impact/detect_changes gate |
| Eval | 动态专家编排与 baseline 对比 |
| Rust Parity | Rust `ap-expert` 与 PydanticAI `generic-expert-agent` 在同一 profile 和任务下输出同类 artifact |

关键测试用例：

```text
1. 导入缺少 frontmatter 的 Markdown -> fail
2. 导入含高风险 prompt injection 内容 -> fail
3. allowlist capability 覆盖原始推断 -> pass
4. selector 在 architecture task 中选择 software-architect -> pass
5. selector 不给 code reviewer 写文件权限 -> pass
6. dynamic DAG 中 Integrator blocked_by 所有专家任务 -> pass
7. output contract 缺少 risks 章节 -> fail
8. code-change workflow 未运行 GitNexus gate -> fail
```

## 15. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 全量导入导致路由噪声 | 高 | 中 | 第一阶段只支持 allowlist |
| Persona 内容不稳定 | 中 | 中 | pinned commit + source lock + human review |
| 专家输出风格过强 | 中 | 中 | 生成 `normalized-profile.md`，并用 output contract 限制格式 |
| 通用运行器过度膨胀 | 中 | 中 | 运行器只负责 prompt 装配、模型调用和契约校验，领域差异留在 profile |
| Profile 配置错误影响专家身份 | 中 | 中 | schema validation + agency check + source lock |
| 过早 Rust 化拖慢验证 | 中 | 中 | 先用 PydanticAI 完成 POC 和 eval，Rust 只作为稳定后的 Phase H |
| Python 子进程启动成本 | 中 | 低 | POC 阶段接受；稳定后用 Rust `ap-expert` 优化 |
| 重新造 nanobot-like runtime | 中 | 高 | Rust 版只做 leaf executor，调度、权限和 QA Gate 继续由 Agent Nexus 控制 |
| 多专家输出冲突 | 高 | 中 | 固定 Integrator 负责冲突解析 |
| Token 成本上升 | 中 | 中 | profile metadata 先行，role body 按需加载 |
| 权限绕过 | 低 | 高 | persona-only 默认无工具权限 |
| GitNexus 门禁被绕开 | 低 | 高 | QA Gate 硬规则 |
| 用户理解成本增加 | 中 | 中 | 将专家库作为内部能力，主流程只暴露任务入口和最终结果 |

## 16. 推荐产品形态

### 16.1 用户视角

用户不需要知道 `agency-agents` 的目录结构，也不需要知道系统有哪些专家。用户只向一个任务型 Composite Agent 发布任务：

```bash
agent-nexus run task-composer \
  --task "设计 agency-agents 与 Agent Nexus 的集成方案"
```

输出：

```yaml
summary: 集成建议采用“专家配置池 + 通用专家 Agent + Expert Planner + Integrator”模式。
recommendation: |
  使用 agency-agents 作为 Markdown 专家配置来源，不把它作为运行时依赖。
  Composite Agent 自动拆分任务、选择专家、汇总结果并通过 QA Gate 验证。
risks:
  - 专家输出冲突需要固定 Integrator 解决。
  - 代码修改任务必须经过 GitNexus 门禁。
validation:
  - output_contract_checked
  - permission_policy_checked
```

如果用户或开发者需要审计，可以显式打开 trace：

```bash
agent-nexus run task-composer \
  --task "设计 agency-agents 与 Agent Nexus 的集成方案" \
  --trace planner
```

trace 可以展示内部专家分工，但它不是主流程：

```yaml
planner_trace:
  selected_specialists:
    - agency.software-architect
    - agency.security-engineer
    - agency.tool-evaluator
  subtask_plan:
    - architecture
    - runtime_risk
    - implementation_plan
```

### 16.2 开发者视角

开发者通过 allowlist 管理导入范围：

```yaml
agents:
  - source_path: engineering/engineering-code-reviewer.md
    id: agency.code-reviewer
    status: experimental
    capabilities: [code_review, maintainability_review]
```

如果某个专家长期效果好，可以提升为正式 Atomic Agent：

```bash
agent-nexus promote agency.code-reviewer \
  --to agents/atomic/code-reviewer-plus
```

## 17. 最终建议

建议按以下顺序推进：

1. 先实现 allowlist importer，不做全量导入。
2. 将导入专家作为 `persona-only` Virtual Atomic Agent，默认无工具权限。
3. 首版 `generic-expert-agent` 使用 PydanticAI，实现动态 profile 注入和结构化 artifact 输出。
4. 实现面向任务的 `task-composer` Composite Agent，把专家选择作为内部 Expert Planner 能力。
5. 固定 Integrator 和 QA Gate，防止多专家输出发散。
6. 默认隐藏专家清单和中间产物，只在 debug、audit 或 eval 模式展示 planner trace。
7. 对代码修改工作流强制接入 GitNexus impact analysis 和 detect_changes。
8. 通过 eval 验证“用户只发布任务”的动态专家编排是否优于现有 static Composite Agent。
9. 当 profile schema、output contract 和 eval 稳定后，再实现 Rust `ap-expert`，但只作为 leaf executor，不开发独立 nanobot-like runtime。

一句话策略：

> 用 `agency-agents` 扩充 Agent Nexus 的专家脑库，用 Agent Nexus 控制专家如何被选择、运行、约束、审计和集成。
