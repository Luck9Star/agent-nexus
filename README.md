# Agent Nexus

**[English](README_EN.md)** | 中文

> MCP-native Agent Platform -- 自建编排 · Git 分发 · 自进化引擎

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 3554](https://img.shields.io/badge/tests-3554_passing-brightgreen.svg)]()

Agent Nexus 是一个 **MCP-native** 的智能体平台，采用四层架构设计。用户在本地运行 Agent，自行配置模型（OpenAI / Anthropic / Ollama / 国产模型均支持）。

**核心亮点：**

- **自建编排层** -- TaskGraph（SQLite WAL）+ IPC（JSON-lines）+ ProcessManager（asyncio.subprocess）+ OrchestrationDSL（TOML DAG），参考 ClawTeam 实现简化构建
- **Git-based 分发** -- Homebrew Tap 模式：官方 monorepo + 私有仓库 + 直连 URL，无需云端基础设施
- **Python Runtime** -- IPython 内核执行 + AST 级别安全检查，Agent 内部永远 Python，平台层后续 Rust 重写
- **自进化引擎** -- FIX / DERIVED / CAPTURED 三类技能演化 + CompactionGuard 上下文保护 + Agent Promotion 自动晋升

---

## 目录

- [架构概览](#架构概览)
- [安装](#安装)
- [快速开始](#快速开始)
- [配置指南](#配置指南)
- [CLI 命令](#cli-命令)
- [Agent 目录](#agent-目录)
- [Agent 开发指南](#agent-开发指南)
- [自进化引擎](#自进化引擎)
- [安全架构](#安全架构)
- [测试](#测试)
- [技术栈](#技术栈)
- [设计文档](#设计文档)
- [许可证](#许可证)

---

## 架构概览

Agent Nexus 采用四层架构，从外部通信到进化引擎自上而下：

```
+---------------------------------------------+
|          MCP 暴露层                          |  FastMCP Server per Agent
|          MCP Gateway 聚合路由               |  DeferredAgentRegistry 懒加载
+---------------------------------------------+
|          编排层                              |  TaskGraph (SQLite WAL + blocked_by + 环检测)
|          自建编排层                          |  IPC (stdin/stdout JSON-lines)
|                                             |  ProcessManager (asyncio.subprocess)
|                                             |  OrchestrationDSL (TOML DAG)
+---------------------------------------------+
|          Python Runtime 层                   |  IPython InteractiveShell 内核执行
|          CaveAgent-based                    |  SecurityChecker AST 级别代码安全分析
|                                             |  L0-L3 四级上下文渐进加载
+---------------------------------------------+
|          自进化引擎                          |  Atomic Skill Evolution (FIX/DERIVED/CAPTURED)
|          OpenSpace-based                    |  Composite Orchestration Evolution
|                                             |  Agent Promotion (skill -> standalone agent)
+---------------------------------------------+
```

**各层职责：**

| 层 | 角色 | 关键组件 |
|----|------|---------|
| **MCP 暴露层** | 外部通信 | FastMCP Server per Agent、MCP Gateway 路由/发现、DeferredAgentRegistry |
| **编排层** | 多 Agent 协调 | TaskGraph（DAG + 状态机 + 环检测）、IPC（JSON-lines）、ProcessManager、OrchestrationDSL（TOML） |
| **Python Runtime 层** | 进程内代码执行 | IPython InteractiveShell、SecurityChecker（AST）、Variables/Functions/Types 持久化 |
| **自进化引擎** | 技能与编排优化 | ExecutionAnalyzer、SkillEvolver、SkillStore、Agent Promotion 管线 |

**通信矩阵：**

| 场景 | 协议 |
|------|------|
| Agent 内部代码执行 | Python Runtime（IPython） |
| Agent 间通信 | IPC（stdin/stdout JSON-lines），Platform Router 中转 |
| Agent 与外部框架 | MCP Server（stdio / SSE） |
| Agent 与外部 API | MCP Tool Call |
| Platform Router 到 Agent | stdin/stdout JSON-lines |
| 远程 Agent（远期） | MCP SSE |

---

## 安装

### 前置条件

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- Git

### 从源码安装（开发模式）

```bash
# 克隆仓库
git clone https://github.com/anthropics/agent-nexus.git
cd agent-nexus

# 创建虚拟环境并安装开发依赖
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 通过 pip 安装

```bash
pip install -e ".[dev]"
```

### 验证安装

```bash
agent-nexus --help
agent-nexus list
```

---

## 快速开始

### 1. 配置模型提供商

至少设置一个 API 密钥作为环境变量：

```bash
export OPENAI_API_KEY="sk-..."
# 或
export ANTHROPIC_API_KEY="sk-ant-..."
# 或使用本地模型
export OLLAMA_BASE_URL="http://localhost:11434"
```

### 2. 安装 Agent

```bash
agent-nexus install doc-filler
```

### 3. 运行 Agent

```bash
# MCP 独立模式（默认）
agent-nexus run doc-filler --mode mcp

# CLI 交互模式（用于测试）
agent-nexus run doc-filler --mode cli

# Router 编排模式
agent-nexus run doc-filler --mode router
```

---

## 配置指南

### 配置优先级

设置项按以下顺序解析（优先级从高到低）：

1. **环境变量** -- `AGENT_MODEL`、`DEFAULT_MODEL` 等
2. **config.toml** -- `~/.agent-nexus/config.toml`
3. **内置默认值** -- 详见 `src/agent_nexus/platform/config/defaults.py`

### 环境变量

| 变量 | 用途 | 示例 |
|------|------|------|
| `OPENAI_API_KEY` | OpenAI API 密钥 | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic API 密钥 | `sk-ant-...` |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | `sk-...` |
| `DASHSCOPE_API_KEY` | 阿里云通义千问 API 密钥 | `sk-...` |
| `MINIMAX_API_KEY` | MiniMax API 密钥 | `...` |
| `OLLAMA_BASE_URL` | Ollama 本地服务地址 | `http://localhost:11434` |
| `AGENT_MODEL` | 覆盖默认模型（最高优先级） | `anthropic:claude-sonnet-4-20250514` |
| `DEFAULT_MODEL` | 默认模型（次高优先级） | `openai:gpt-4o` |
| `AGENT_NEXUS_HOME` | 平台配置目录 | `~/.agent-nexus` |
| `AGENT_MCP_MODE` | MCP 传输模式 | `sse`（默认：stdio） |

### config.toml

主配置文件位于 `~/.agent-nexus/config.toml`：

```toml
[models]
default = "openai:gpt-4o"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"

[models.providers.ollama]
base_url = "http://localhost:11434/v1"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"

[models.providers.qwen]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key_env = "DASHSCOPE_API_KEY"

[runtime]
python_path = "python3"
uv_path = "uv"
```

**内置提供商预设**（可在 config.toml 中覆盖）：

| 提供商 | API 类型 | 密钥环境变量 | 默认 Base URL |
|--------|---------|-------------|--------------|
| openai | openai-compatible | `OPENAI_API_KEY` | （SDK 默认） |
| anthropic | anthropic-messages | `ANTHROPIC_API_KEY` | （SDK 默认） |
| deepseek | openai-compatible | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` |
| minimax | anthropic-messages | `MINIMAX_API_KEY` | `https://api.minimax.chat/v1` |
| qwen | openai-compatible | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| ollama | openai-compatible | （无需密钥） | `http://localhost:11434/v1` |

### 模型层级

Agent 声明推荐的模型层级，平台将层级映射到具体的模型字符串：

| 层级 | 默认模型 | 用途 | 示例 Agent |
|------|---------|------|-----------|
| **lightweight** | `openai:gpt-4o-mini` | 快速任务、提取 | doc-filler（填充阶段） |
| **standard** | `openai:gpt-4o` | 通用任务 | api-doc-generator、security-scanner |
| **powerful** | `anthropic:claude-sonnet-4-20250514` | 复杂推理 | requirements-analyzer |
| **premium** | `anthropic:claude-opus-4-20250116` | 深度分析、代码审查 | code-reviewer、contract-analyzer |

在 config.toml 中覆盖层级到模型的映射：

```toml
[models.tiers]
lightweight = "ollama:llama3"
standard = "openai:gpt-4o"
powerful = "anthropic:claude-sonnet-4-20250514"
premium = "anthropic:claude-opus-4-20250116"
```

### sources.yaml

Agent 包源配置位于 `~/.agent-nexus/sources.yaml`：

```yaml
sources:
  - name: official
    type: git
    url: https://github.com/agent-nexus/official-packages
    branch: main

  - name: team-tap
    type: git
    url: git@github.com:my-team/agent-tap.git
    branch: main

  - name: experimental
    type: git
    url: https://github.com/agent-nexus/experimental
    branch: dev
```

### 配置目录结构

`~/.agent-nexus/`（或 `$AGENT_NEXUS_HOME`）：

```
~/.agent-nexus/
  config.toml         # 平台配置
  sources.yaml        # 包源注册
  lockfile.json       # 已安装 Agent 跟踪（commit SHA、版本）
  agents/             # 已安装的 Agent 包
  venvs/              # 每个 Agent 的虚拟环境
  cache/repos/        # 缓存的 Git 仓库
  runtimes/           # 运行时状态
  logs/               # 平台和 Agent 日志
```

---

## CLI 命令

### 初始化与诊断

```bash
# 首次使用：初始化配置目录
agent-nexus init

# 交互式向导（选择 provider + 配置 API key）
agent-nexus init --wizard

# 诊断检查（配置文件、API key、git/uv、Python 版本、Evolution DB）
agent-nexus doctor

# 查看版本
agent-nexus version
agent-nexus -v              # 等价写法

# 查看环境快照（config 路径、Python 版本、provider 状态）
agent-nexus env
```

### 安装与管理

```bash
# 从官方源安装
agent-nexus install doc-filler

# 安装指定版本
agent-nexus install doc-filler --version 1.2.0

# 从 Git URL 直接安装
agent-nexus install my-agent --source https://github.com/org/agent-repo.git

# 从本地项目目录安装（开发模式）
agent-nexus install doc-filler --local

# 卸载 Agent
agent-nexus uninstall doc-filler

# 更新单个 Agent 到最新版本
agent-nexus update doc-filler

# 更新所有已安装的 Agent
agent-nexus update --all
```

### 发现与查询

```bash
# 列出已安装的 Agent
agent-nexus list

# 搜索所有源中的 Agent
agent-nexus search "security"
agent-nexus search "document"

# 查看 Agent 详细信息
agent-nexus info doc-filler
```

### 创建 Agent

```bash
# 非交互式创建（simple 模式：单个 run 工具）
agent-nexus create agent my-agent -d "My agent description"

# Pipeline 模式（analyze / execute / report 三个工具）
agent-nexus create agent my-agent -d "description" --tools pipeline

# 交互式向导（逐步选择工具模式、模型层级等）
agent-nexus create agent my-agent --wizard

# 指定输出目录
agent-nexus create agent my-agent -d "description" --output ./my-agents
```

工具模式：

| 模式 | `--tools` | 生成的 MCP 工具 |
|------|-----------|----------------|
| 简单 | `simple`（默认） | `run` |
| 流水线 | `pipeline` | `analyze`、`execute`、`report` |

生成的文件：`agent-manifest.yaml`、`agent.py`、`SKILL.md`、`pyproject.toml`、`<pkg>/__init__.py`、`<pkg>/agent.py`、`<pkg>/mcp_adapter.py`

### 包源管理

```bash
# 列出已配置的源
agent-nexus sources list

# 添加私有源
agent-nexus sources add --name internal --url https://github.com/myorg/agents.git

# 移除源
agent-nexus sources remove internal
```

### 运行时管理

```bash
# 启动单个 Agent
agent-nexus start doc-filler

# 启动所有已安装的 Agent
agent-nexus start --all

# 停止 Agent
agent-nexus stop doc-filler
agent-nexus stop --all

# 重启 Agent
agent-nexus restart doc-filler

# 查看运行状态（Installed / Running / PID）
agent-nexus status

# 查看别名
agent-nexus ps

# 查看 Agent 日志（默认最近 50 行）
agent-nexus logs doc-filler
agent-nexus logs doc-filler --lines 200
```

### 配置管理

```bash
# 查看合并后的配置
agent-nexus config show

# JSON 格式输出
agent-nexus config show --json

# 按 dot-path 获取配置值
agent-nexus config get models.default

# 用 $EDITOR 编辑 config.toml
agent-nexus config edit

# 验证配置文件合法性
agent-nexus config validate

# 列出所有 provider 及 API key 状态
agent-nexus config providers

# 输出配置目录路径
agent-nexus config path
```

### 自进化引擎

```bash
# 查看进化状态总览（技能数、健康/不健康计数）
agent-nexus evolution status

# 健康诊断（全部技能或指定技能）
agent-nexus evolution health
agent-nexus evolution health doc-filler --verbose

# 列出技能
agent-nexus evolution list
agent-nexus evolution list --all

# 查看技能版本血统
agent-nexus evolution history doc-filler

# 查看进化质量指标
agent-nexus evolution metrics
agent-nexus evolution metrics --agent doc-filler

# 触发 FIX 演化
agent-nexus evolution fix <skill-id>

# 晋升技能为独立 Agent
agent-nexus evolution promote <skill-id>
```

### 运行 Agent

```bash
# MCP 独立模式（作为 MCP Server 运行，stdio 传输）
agent-nexus run doc-filler --mode mcp

# MCP 独立模式，SSE 传输
agent-nexus run doc-filler --mode mcp --transport sse

# CLI 独立模式（直接交互，用于开发）
agent-nexus run doc-filler --mode cli

# Router 模式（通过 Platform Router + MCP Gateway）
agent-nexus run doc-filler --mode router
```

| 模式 | `--mode` | `--transport` | 说明 |
|------|----------|--------------|------|
| MCP 独立 | `mcp`（默认） | `stdio`（默认）或 `sse` | Agent 作为独立 MCP Server 运行 |
| Platform Router | `router` | `stdio` 或 `sse` | 由 Platform Router 编排，通过 MCP Gateway 暴露 |
| CLI 独立 | `cli` | 不适用 | 直接命令行交互，用于开发/测试 |

### 质量检查（Agent 开发者用）

```bash
# 发布前验证 Agent 包
agent-nexus check ./my-agent
```

---

## Agent 目录

### 11 个 Atomic Agent

每个 Atomic Agent 都是单一专业能力的深度优化专家：

| Agent | 领域 | 模型层级 | 核心差异点 |
|-------|------|---------|-----------|
| **doc-filler** | 文档/模板自动化 | Lightweight/Standard | 两阶段管道（分析+填充），样式继承链处理 |
| **requirements-analyzer** | 软件工程 - 需求分析 | Powerful | 多轮对话追踪提问策略 |
| **code-reviewer** | 软件工程 - 代码质量 | Premium | 每语言规则数据库，跨文件推理 |
| **api-doc-generator** | 软件工程 - 文档 | Standard | OpenAPI 3.1 标准生成 |
| **security-scanner** | 质量/安全 - 应用安全 | Standard | OWASP Top 10 模式匹配 |
| **accessibility-auditor** | 质量/安全 - 无障碍 | Lightweight/Standard | WCAG 2.2 AA 87 条标准 |
| **localization-specialist** | 文档/内容 - 本地化 | Standard | 术语表管理，语域识别 |
| **contract-analyzer** | 文档/内容 - 法律分析 | Premium | 条款间依赖理解，多法域合规 |
| **market-intelligence-analyst** | 研究/分析 - 市场研究 | Standard | Porter/SWOT/PESTEL 方法论 |
| **test-suite-generator** | 软件工程 - 测试 | Standard | AST 解析 + 每范式测试策略 |
| **good-skill** | 通用（自进化晋升） | Standard | 由自进化引擎从 skill sk-1 自动晋升（effective_rate=0.9） |

### 5 个 Composite Agent

Composite Agent 通过 TOML DAG 编排多个 Atomic Agent：

| Agent | 编排模式 | 依赖的 Atomic Agent |
|-------|---------|-------------------|
| **Feature Delivery Pipeline** | 顺序 -> 并行 | requirements-analyzer -> [api-doc-generator + test-suite-generator + code-reviewer] |
| **Document Compliance Gateway** | 全并行 | [contract-analyzer + accessibility-auditor + localization-specialist] -> 冲突检测 |
| **CI/CD Quality Gate** | 全并行 | [security-scanner + code-reviewer + test-suite-generator] -> 质量决策 |
| **Competitive Intel Briefing** | 顺序链 | market-intelligence-analyst -> doc-filler -> localization-specialist |
| **Product Documentation Suite** | 并行 -> 顺序 | [api-doc-generator + code-reviewer] -> localization-specialist |

---

## Agent 开发指南

### Agent 包结构

**Atomic Agent：**

```
my-agent/
  agent-manifest.yaml    # 元数据、权限、模型配置
  SKILL.md               # 三层渐进式行为定义
  agent.py               # PydanticAI 核心逻辑
  tools/                 # 领域专用工具
  hooks/                 # 生命周期钩子（hooks.yaml）
  mcp_servers/           # 外部 MCP Server 依赖
  mcp_adapter.py         # MCP Server 适配器
  local_adapter.py       # Local mode 适配器（stdin/stdout JSON-lines）
  main.py                # 入口（自动检测运行模式）
  models.py              # Pydantic 数据模型
  pyproject.toml         # 包配置
  tests/
    test_agent.py
```

**Composite Agent：**

```
my-composite/
  agent-manifest.yaml    # 元数据 + 依赖声明
  SKILL.md               # 包含编排描述
  composition.toml       # 编排 DAG 定义
  hooks/
    hooks.yaml
  mcp_adapter.py
  main.py
  pyproject.toml
  tests/
    test_composition.py
```

### 快速创建（脚手架）

使用 `agent-nexus create agent` 一键生成完整的 Agent 包骨架：

```bash
# 最快方式
agent-nexus create agent my-agent -d "My agent description"

# 交互向导
agent-nexus create agent my-agent --wizard
```

脚手架会自动生成上述所有必需文件，包括 `agent-manifest.yaml`、`SKILL.md`、`pyproject.toml`、`mcp_adapter.py` 等。详见 [CLI 命令 - 创建 Agent](#创建-agent)。

### agent-manifest.yaml

清单文件声明 Agent 的身份、权限、模型偏好和依赖：

```yaml
name: doc-filler
version: 1.0.0
type: atomic              # atomic | composite
description: Word 文档模板填充专家

model_config:
  recommended: "standard" # lightweight/standard/powerful/premium
  fallback: "lightweight"

permissions:
  mode: default           # default | plan | full_auto
  allowed_tools: [file_read, file_write]
  denied_tools: [bash]
  path_rules:
    - pattern: "*.docx"
      access: read-write

# Composite Agent 必须声明 atomic_agents 依赖
# dependencies:
#   atomic_agents:
#     - requirements-analyzer
#     - api-doc-generator

# 外部 MCP Server 依赖
mcp_servers:
  filesystem:
    transport: stdio
    command: "uvx"
    args: ["mcp-server-filesystem"]

# 生命周期钩子
hooks:
  pre_execution:
    - type: prompt
      prompt: "验证输入文件存在且格式为 .docx"
      block_on_failure: true
  post_execution:
    - type: command
      command: "notify-send '文档填充完成'"
      block_on_failure: false
```

### SKILL.md（三层渐进式行为定义）

SKILL.md 遵循 deer-flow 的渐进式加载理念：

| 层级 | 内容 | 加载时机 |
|------|------|----------|
| **Metadata** | name, agent_type, triggers, capabilities | 即时加载（YAML frontmatter） |
| **Body** | role, workflow, constraints | 首轮交互前加载 |
| **Resources** | examples, templates, references | 按需加载 |

示例：

```markdown
---
name: requirements-analyzer
agent_type: atomic
description: 多轮对话分析模糊需求，输出结构化需求说明书
triggers:
  - 需求分析
  - 提取需求
capabilities: [requirements-analysis, structured-output, web-search]
model_config:
  recommended: "powerful"
  fallback: "default"
---

# Requirements Analyzer Agent

## 角色
你是一个专业的需求分析师...

## 工作流程
1. 接收用户初始需求
2. 多轮提问澄清（每次只问一个带选项的问题）
3. 可选：搜索行业背景信息
4. 生成结构化需求说明书

## 约束
- 每次只问一个问题
- 最多问 12 个问题，到阈值自动总结
- 不编造：无法确认的内容标记为「待确认」
```

### composition.toml（编排 DSL）

Composite Agent 通过 TOML 定义 DAG。`blocked_by` 为空的任务立即执行；有依赖的任务在依赖完成后并行执行：

```toml
[composition]
name = "feature-delivery-pipeline"
description = "需求驱动并行生成 API 文档、测试套件和代码审查"

[tasks.task1]
name = "requirements-analysis"
agent = "requirements-analyzer"
blocked_by = []

[tasks.task2]
name = "api-doc-generation"
agent = "api-doc-generator"
blocked_by = ["task1"]

[tasks.task3]
name = "test-suite-generation"
agent = "test-suite-generator"
blocked_by = ["task1"]

[tasks.task4]
name = "code-review"
agent = "code-reviewer"
blocked_by = ["task1"]
```

完整 TOML Schema 参考：

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `[composition]` | section | 是 | 编排元数据 |
| `composition.name` | string | 是 | Composite Agent 名称 |
| `composition.description` | string | 是 | 描述 |
| `[[tasks]]` | array | 是 | 任务列表 |
| `tasks[].id` | string | 是 | 任务 ID（TOML 键名） |
| `tasks[].name` | string | 是 | 任务显示名称 |
| `tasks[].agent` | string | 是 | 分配的 Agent |
| `tasks[].blocked_by` | array | 否 | 阻塞任务 ID 列表 |
| `tasks[].vars` | object | 否 | 任务变量 |
| `[tool_loading]` | section | 否 | 全局工具加载策略 |
| `tool_loading.strategy` | string | 否 | eager / lazy / manifest_only |
| `tool_loading.preload_agents` | array | 否 | eager 策略下预加载的 Agent |

### 运行模式入口

每个 Agent 的 `main.py` 自动检测运行模式：

```python
import os

def main():
    mode = os.getenv("AGENT_MODE", "mcp")

    if mode == "local":
        # Platform Router 模式：stdin/stdout JSON-lines
        asyncio.run(serve(my_agent))
    elif mode == "cli":
        # CLI 独立模式
        asyncio.run(run_cli(my_agent))
    else:
        # MCP 独立模式（默认）
        mcp_serve(my_agent)
```

### Agent 角色（可选）

Agent 可以声明角色以获得预设的工具集约束：

| 角色 | 工具集 | 推荐模型 |
|------|--------|---------|
| **explore** | glob, grep, file_read, web_fetch/search | lightweight |
| **plan** | file_read, glob, grep（只读） | standard |
| **worker** | 全部工具 | 继承 |
| **verification** | file_read, file_write（仅临时文件）, glob, grep | standard |

### 生命周期钩子

钩子在关键执行节点注入自定义逻辑。支持四种钩子类型：

| 类型 | 执行方式 | 延迟 | 适用场景 |
|------|---------|------|---------|
| **command** | Shell 子进程 | 低 | 文件存在性验证 |
| **http** | HTTP POST | 中 | CI/CD 触发、通知 |
| **prompt** | LLM 短调用（小模型） | 中 | 快速校验、格式检查 |
| **agent** | LLM 深度调用（大模型） | 高 | 复杂推理、质量评审 |

支持的事件：`pre_execution`、`post_execution`、`pre_tool_use`、`post_tool_use`、`on_error`、`on_evolution`。

### MCP 工具命名规范

通过 MCP Gateway 暴露时，工具名遵循：`{agent-name}__{tool-name}`

| Agent | 工具 | MCP 完整名称 |
|-------|------|-------------|
| doc-filler | analyze_template | `doc-filler__analyze_template` |
| doc-filler | fill_template | `doc-filler__fill_template` |
| code-reviewer | review_diff | `code-reviewer__review_diff` |

外部 MCP 工具桥接为：`mcp__{server_name}__{tool_name}`

### 测试约定

```bash
# 运行指定 Agent 的测试
pytest agents/atomic/my-agent/tests/ -v

# 运行全部测试
pytest tests/ agents/ -v

# 发布前质量检查
agent-nexus check ./my-agent
```

---

## 自进化引擎

Agent Nexus 内置三级自进化能力，借鉴 OpenSpace 架构：

### 第一级：Atomic Skill 进化

基于运行时指标的技能级演化，在 Atomic Agent 内部进行：

| 模式 | 触发条件 | 产出 |
|------|---------|------|
| **FIX** | Skill 被选中但执行失败 | 就地更新 SKILL.md（同名同目录） |
| **DERIVED** | 成功模式可以增强/合并 | 新 Skill（新目录、新名称、支持多技能合并） |
| **CAPTURED** | 任务成功但无 Skill 参与 | 全新 Skill（从成功交互中提取） |

跟踪指标：`applied_rate`、`completion_rate`、`fallback_rate`、`effective_rate`。

### 第二级：Composite 编排进化

基于执行历史优化 Composite Agent 的 DAG 拓扑：

- 分析调用链效率、并行化机会、缺失步骤
- `DERIVED`：优化 TOML 模板（调整 Agent 顺序、并行策略）
- `CAPTURED`：从成功的编排模式创建新 Composite Agent

### 第三级：Agent Promotion

将高性能 Skill 晋升为独立 Agent：

- 条件：`effective_rate > 0.8` + `total_selections > 50` + 独立工作流
- 自动生成：`SKILL.md` + `agent.py` + `agent-manifest.yaml`
- 注册为 MCP Server，发布到 Git 源

### 健康阈值规则

| 触发条件 | 演化类型 | 说明 |
|---------|---------|------|
| `fallback_rate > 0.4` | FIX | 技能频繁被选中但未应用 |
| `applied_rate > 0.4` AND `completion_rate < 0.35` | FIX | 应用率高但完成率低 |
| `effective_rate < 0.55` AND `applied_rate > 0.25` | DERIVED | 中等效能，需要增强 |
| `effective_rate > 0.8` AND `selections > 50` | Promotion | 可晋升为独立 Agent |

### 防循环机制

- 三个进化触发器内置防循环：Post-Analysis、Tool Degradation、Periodic Metric Check
- Apply-Retry 限制：每轮进化最多 5 次重试
- CompactionGuard：`min_turns_between_compactions=5`，防止正反馈死循环

---

## 安全架构

纵深防御，三层独立安全机制：

### 1. 进程边界

Agent 作为独立子进程运行，由 ProcessManager（asyncio.subprocess）管理。每个 Agent 拥有独立的虚拟环境，Agent 间无共享内存。

### 2. PermissionChecker（执行前权限检查）

三级权限模式控制 Agent 的行为：

| 模式 | 行为 |
|------|------|
| **default** | 敏感操作需用户确认 |
| **plan** | 先规划、展示方案，用户确认后执行 |
| **full_auto** | 无需确认直接执行（谨慎使用） |

通过 `agent-manifest.yaml` 的 `permissions.mode`、`permissions.allowed_tools` / `denied_tools` 配置。

### 3. SecurityChecker（运行时 AST 级安全检查）

在执行前对生成的 Python 代码进行 AST 级别分析：

| 规则类型 | 检查内容 | 示例 |
|---------|---------|------|
| `ImportRule` | 禁止模块导入 | `os`、`subprocess`、`sys`、`socket` |
| `FunctionRule` | 禁止函数调用 | `eval()`、`exec()`、`compile()` |
| `AttributeRule` | 禁止属性访问 | `__import__`、`__builtins__` |
| `RegexRule` | 基于正则的阻断 | Shell 注入模式 |

---

## 测试

```bash
# 运行全部测试（平台 + Agent）
pytest tests/ agents/ -v

# 仅平台测试
pytest tests/ -v

# 单个 Agent 测试
pytest agents/atomic/doc-filler/tests/ -v

# 覆盖率报告
pytest tests/ --cov=agent_nexus --cov-report=html

# 按类别运行
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/e2e/ -v
```

当前测试覆盖：**3554 个测试全部通过**（平台 2698 + Agent 856），覆盖所有平台模块和 Agent 包。

---

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 平台核心 | Python 3.11+ | 生产就绪（Phase 1-6 完成） |
| 数据模型 | Pydantic v2 (frozen) | 全量不可变模型 |
| Agent 框架 | PydanticAI | Agent 逻辑和工具定义 |
| MCP Server | FastMCP | per-Agent MCP 暴露 |
| CLI | Typer | init/doctor/version/env, install/run/list/search, create agent, runtime start/stop/status, config, evolution |
| 持久化 | SQLite WAL | TaskGraph 并发安全 |
| Runtime | IPython InteractiveShell | 内核执行 |
| 配置 | TOML + YAML | config.toml + sources.yaml |
| 生产重写 | Rust | 仅上层（Gateway/Fetcher/Supervisor/CLI），Agent Runtime 保持 Python |

---

## 设计文档

所有设计文档位于 `docs/`，中文设计文档（已根据实现验证）：

| 主题 | 文件 |
|------|------|
| 产品定位与核心架构 | `docs/01-overview.md` |
| 自建编排层 | `docs/02-clawteam-integration.md` |
| Python Runtime | `docs/03-python-runtime.md` |
| 自进化引擎 | `docs/04-self-evolution.md` |
| Agent 体系 | `docs/05-agent-system.md` |
| MCP 暴露与通信 | `docs/06-mcp-communication.md` |
| Agent 分发与质量关卡 | `docs/07-marketplace.md` |
| 约束与决策 | `docs/08-constraints-decisions.md` |
| 7 阶段实施计划 | `docs/09-implementation-plan.md` |
| Git 分发与本地架构 | `docs/10-cloud-local-architecture.md` |
| TOML Schema 与参考 | `docs/appendix.md` |

---

## 许可证

[MIT](LICENSE)
