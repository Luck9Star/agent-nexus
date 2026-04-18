# Agent Nexus

**[English](README_EN.md)** | 中文

> MCP-native Agent Platform — 自建编排 · Git 分发 · 自进化引擎

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 1793](https://img.shields.io/badge/tests-1793_passing-brightgreen.svg)]()

Agent Nexus 是一个 **MCP-native** 的智能体平台，采用四层架构设计：

- **自建编排层** — TaskGraph（SQLite WAL）+ IPC（JSON-lines）+ ProcessManager（asyncio.subprocess）+ OrchestrationDSL（TOML DAG），参考 ClawTeam 实现简化构建
- **Git-based 分发** — Homebrew Tap 模式，官方 monorepo + 私有仓库 + 直连 URL，无需云端基础设施
- **Python Runtime** — IPython 内核执行 + AST 级别安全检查，Agent 内部永远 Python，平台层后续 Rust 重写
- **自进化引擎** — FIX / DERIVED / CAPTURED 三类技能演化 + CompactionGuard 上下文保护 + Agent Promotion 自动晋升

用户在本地运行 Agent，自行配置模型（OpenAI / Anthropic / Ollama / 国产模型均支持）。

---

## 四层架构

```
┌─────────────────────────────────────────────┐
│          MCP Exposure Layer                 │  FastMCP Server per Agent
│          MCP Gateway 聚合路由               │  DeferredAgentRegistry 懒加载
├─────────────────────────────────────────────┤
│          Orchestration Layer                │  TaskGraph (SQLite WAL + blocked_by + 环检测)
│          自建编排层                          │  IPC (stdin/stdout JSON-lines)
│                                             │  ProcessManager (asyncio.subprocess)
│                                             │  OrchestrationDSL (TOML DAG)
├─────────────────────────────────────────────┤
│          Python Runtime Layer               │  IPython InteractiveShell 内核执行
│          CaveAgent-based                    │  SecurityChecker AST 级别代码安全分析
│                                             │  L0-L3 四级上下文渐进加载
├─────────────────────────────────────────────┤
│          Self-Evolution Engine              │  Atomic Skill Evolution (FIX/DERIVED/CAPTURED)
│          OpenSpace-based                    │  Composite Orchestration Evolution
│                                             │  Agent Promotion (skill → standalone agent)
└─────────────────────────────────────────────┘
```

## Agent 体系

### 10 Atomic Agents（单一专业能力）

| Agent | 领域 | 模型层级 | 核心差异点 |
|-------|------|---------|-----------|
| **Doc Filler** | 文档/模板自动化 | Lightweight/Standard | 两阶段管道，样式继承链处理 |
| **Requirements Analyzer** | 软件工程 - 需求分析 | Powerful | 多轮对话追踪提问策略 |
| **Code Reviewer** | 软件工程 - 代码质量 | Premium | 每语言规则数据库，跨文件推理 |
| **API Doc Generator** | 软件工程 - 文档 | Standard | OpenAPI 3.1 标准生成 |
| **Security Scanner** | 质量/安全 - 应用安全 | Standard | OWASP Top 10 模式匹配 |
| **Accessibility Auditor** | 质量/安全 - 无障碍 | Lightweight/Standard | WCAG 2.2 AA 87 条标准 |
| **Localization Specialist** | 文档/内容 - 本地化 | Standard | 术语表管理，语域识别 |
| **Contract Analyzer** | 文档/内容 - 法律分析 | Premium | 条款间依赖理解，多法域合规 |
| **Market Intelligence** | 研究/分析 - 市场研究 | Standard | Porter/SWOT/PESTEL 方法论 |
| **Test Suite Generator** | 软件工程 - 测试 | Standard | AST 解析 + 每范式测试策略 |

### 5 Composite Agents（多 Agent 编排）

| Agent | 编排模式 | 依赖的 Atomic Agent |
|-------|---------|-------------------|
| **Feature Delivery Pipeline** | 顺序 → 并行 | Requirements → [API Doc + Test + Review] |
| **Document Compliance Gateway** | 全并行 | [Legal + Accessibility + Localization] → 冲突检测 |
| **CI/CD Quality Gate** | 全并行 | [Security + Code Review + Test] → 质量决策 |
| **Competitive Intel Briefing** | 顺序链 | Market Intel → Doc Filler → Localization |
| **Product Documentation Suite** | 并行 → 顺序 | [API Doc + Code Review] → Localization |

## 三种运行模式

| 模式 | 说明 | 入口 |
|------|------|------|
| **MCP Standalone** | Agent 直接作为 MCP Server 运行 | `uvx agent-name` |
| **Platform Router** | 通过 Platform Router 编排管理 | `agent-nexus run <name> --mode router` |
| **CLI Standalone** | 直接命令行交互 | `agent-nexus run <name> --mode cli` |

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/anthropics/agent-nexus.git
cd agent-nexus

# 创建虚拟环境并安装
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 配置模型

```bash
# 环境变量方式（优先级最高）
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OLLAMA_BASE_URL="http://localhost:11434"

# 或编辑配置文件
mkdir -p ~/.agent-nexus
cat > ~/.agent-nexus/config.toml << 'EOF'
[models]
default = "gpt-4o"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"

[models.providers.ollama]
base_url = "http://localhost:11434"
EOF
```

### 使用 CLI

```bash
# 安装 Agent
agent-nexus install doc-filler

# 运行 Agent
agent-nexus run doc-filler --mode mcp

# 查看已安装的 Agent
agent-nexus list

# 搜索可用的 Agent
agent-nexus search "security"

# 管理 Agent 源
agent-nexus sources add --name internal --url https://github.com/myorg/agents.git
```

## 项目结构

```
agent-nexus/
├── src/agent_nexus/          # 平台核心
│   ├── models/               # 共享数据模型（10 文件，58 Pydantic 类型）
│   ├── platform/
│   │   ├── router/           # Platform Router（4-Phase Workflow）
│   │   ├── orchestration/    # TaskGraph + IPC + ProcessManager + DSL
│   │   ├── gateway/          # MCP Gateway 聚合 + DeferredRegistry
│   │   ├── config/           # 模型配置 + Provider 注册
│   │   ├── local/            # CLI + Git Installer + Supervisor
│   │   ├── skills/           # Skill Loader
│   │   ├── evolution/        # 自进化引擎（6 模块）
│   │   └── runtime/          # Python Runtime（IPython + SecurityChecker）
├── agents/                   # Agent 包
│   ├── atomic/               # 10 Atomic Agent
│   └── composite/            # 5 Composite Agent
├── tests/                    # 平台测试（unit + integration + e2e）
├── templates/                # OrchestrationDSL TOML 模板
├── docs/                     # 设计文档（POC v5.2，中文）
└── pyproject.toml
```

## 自进化引擎

Agent Nexus 内置三级自进化能力：

1. **Atomic Skill Evolution** — 基于运行时指标的技能级演化
   - `FIX`：修复破损/过时技能（就地，同名同目录）
   - `DERIVED`：创建增强版本（新目录，新名称，支持多技能合并）
   - `CAPTURED`：捕获新颖模式为全新技能

2. **Composite Orchestration Evolution** — 编排级优化
   - 基于 TaskGraph 执行历史优化 DAG 拓扑
   - 自动发现可并行化的瓶颈节点

3. **Agent Promotion** — 技能晋升为独立 Agent
   - 条件：`effective_rate > 0.8` + `total_selections > 50` + 独立工作流
   - 自动生成 `agent.toml` + `agent.py` + `SKILL.md`

### 健康阈值规则

| 触发条件 | 演化类型 | 说明 |
|---------|---------|------|
| `fallback_rate > 0.4` | FIX | 技能频繁被选中但未应用 |
| `applied_rate > 0.4` AND `completion_rate < 0.35` | FIX | 应用率高但完成率低 |
| `effective_rate < 0.55` AND `applied_rate > 0.25` | DERIVED | 中等效能，需要增强 |
| `effective_rate > 0.8` AND `selections > 50` | Promotion | 可晋升为独立 Agent |

## 安全架构（纵深防御）

1. **进程边界** — Agent 作为独立子进程运行
2. **PermissionChecker** — 执行前权限检查（DEFAULT / PLAN / FULL_AUTO 三级）
3. **SecurityChecker** — Runtime AST 级别代码安全分析（import/function/attribute/regex 四类规则）

## 测试

```bash
# 运行全部测试
pytest tests/ agents/ -v

# 仅平台测试
pytest tests/ -v

# 仅某个 Agent 的测试
pytest agents/atomic/doc-filler/tests/ -v

# 覆盖率
pytest tests/ --cov=agent_nexus --cov-report=html
```

当前测试覆盖：**1793 个测试全部通过**，覆盖所有平台模块和 Agent 包。

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 平台核心 | Python 3.11+ | POC 阶段 |
| 数据模型 | Pydantic v2 (frozen) | 全量不可变模型 |
| MCP Server | FastMCP | per-Agent MCP 暴露 |
| CLI | Typer | install/run/list/search |
| 持久化 | SQLite WAL | TaskGraph 并发安全 |
| Runtime | IPython InteractiveShell | 内核执行 |
| 配置 | TOML + YAML | config.toml + sources.yaml |
| 生产重写 | Rust | 仅上层（Gateway/Fetcher/Supervisor/CLI），Agent Runtime 保持 Python |

## 设计文档

所有设计文档位于 `docs/`，POC v5.2 中文文档：

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

## 许可证

[MIT](LICENSE)
