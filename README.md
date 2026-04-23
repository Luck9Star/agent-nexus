# Agent Nexus

<p align="center">
  <strong>MCP-native Agent 平台 | 自建多 Agent 编排 | Git-based 分发</strong>
</p>

---

Agent Nexus 是一个 MCP-native 的 Agent 平台，提供自建的多 Agent 编排基础设施、Python Runtime 执行层和自进化引擎。Agent 通过 Git 仓库分发（类 Homebrew Tap 模型），本地运行，使用用户自配模型。

## 核心特性

- **自建编排层** — 参考 ClawTeam 验证过的模式（TaskStore、Mailbox、SpawnBackend），按需精简自建。TaskGraph（SQLite + DAG + 环检测）、IPC（JSON-lines）、ProcessManager（async subprocess + 健康检查）
- **MCP-native** — 每个 Agent 自带 FastMCP Server，MCP Gateway 统一路由与发现。MCP 协议边界 = 语言边界
- **Git-based 分发** — 官方 monorepo + 私有仓库 + 直连 URL，无需云端基础设施。类似 Homebrew Tap 模式
- **双语言实现** — Python 平台已完成（Phases 1-6），Rust 平台重写进行中（6 crates, ~18K LOC）
- **自进化引擎** — 基于 OpenSpace 设计，三层递进：Atomic Skill Evolution → Composite Orchestration Evolution → Agent Promotion
- **用户自配模型** — 支持 OpenAI、Anthropic、Ollama 等多后端，免费使用（用户自备 API Key）

## 架构概览

四层架构（自顶向下）：

```
┌─────────────────────────────────────────────────┐
│  Layer 1: MCP Exposure (FastMCP per Agent)       │
│  MCP Gateway → 路由、发现、工具聚合               │
├─────────────────────────────────────────────────┤
│  Layer 2: Orchestration (自建)                    │
│  TaskGraph (SQLite DAG) + IPC (JSON-lines)        │
│  ProcessManager + OrchestrationDSL (TOML)         │
├─────────────────────────────────────────────────┤
│  Layer 3: Python Runtime (CaveAgent-based)        │
│  IPythonRuntime + SecurityChecker (AST)           │
├─────────────────────────────────────────────────┤
│  Layer 4: Self-Evolution Engine (OpenSpace-based) │
│  Skill → Orchestration → Agent Promotion          │
└─────────────────────────────────────────────────┘
```

## Agent 体系

| 类型 | 数量 | 示例 |
|------|------|------|
| **Atomic Agent** | 11 | doc-filler, code-reviewer, security-scanner, test-suite-generator |
| **Composite Agent** | 5 | feature-delivery-pipeline, product-documentation-suite |

三种运行模式：**MCP 独立运行** / **Platform Router 调度** / **CLI 独立运行**

## 快速开始

### 前置要求

- Python 3.11+
- [hatch](https://hatch.pypa.io/) (`pip install hatch`)
- 可选：Rust toolchain（用于 Rust 平台开发）

### 安装与运行

```bash
# 克隆仓库
git clone https://github.com/user/agent-nexus.git
cd agent-nexus

# Python 平台
hatch env create          # 创建开发环境
hatch run test            # 运行测试

# Rust 平台（可选）
cargo build               # 构建所有 crates
cargo test                # 运行 Rust 测试

# CLI 使用
agent-nexus init          # 初始化配置
agent-nexus install <agent>  # 安装 Agent
agent-nexus run <agent>   # 运行 Agent
agent-nexus status        # 查看状态
```

### 环境变量

```bash
# 模型配置（按优先级：env > agent config > defaults）
export AGENT_MODEL=gpt-4o           # 默认 Agent 模型
export DEFAULT_MODEL=gpt-4o         # 全局默认模型
export OPENAI_API_KEY=sk-...        # OpenAI
export ANTHROPIC_API_KEY=sk-ant-... # Anthropic
export OLLAMA_BASE_URL=http://...   # Ollama 本地模型
```

## 项目结构

```
agent-nexus/
├── src/agent_nexus/              # Python 平台核心 (hatch editable install)
│   ├── platform/
│   │   ├── orchestration/        # TaskGraph, ProcessManager, IPC, DSL
│   │   ├── router/               # Platform Router (4-Phase Workflow)
│   │   ├── gateway/              # MCP Gateway
│   │   ├── config/               # 模型配置 + Provider 注册
│   │   ├── local/                # CLI + Git Installer + Supervisor
│   │   ├── skills/               # Skill Loader
│   │   ├── evolution/            # 自进化引擎
│   │   └── runtime/              # Python Runtime
│   └── models/                   # 共享数据模型
├── agents/                       # 官方 Agent 包（每个独立 pyproject.toml）
│   ├── atomic/                   # 11 Atomic Agents
│   └── composite/                # 5 Composite Agents
├── crates/                       # Rust 平台重写（进行中）
│   ├── ap-core/                  # 核心: TaskGraph, StateMachine, IPC, Hooks
│   ├── ap-cli/                   # CLI: clap, 9 命令
│   ├── ap-gateway/               # MCP Gateway
│   ├── ap-fetcher/               # Git-based Agent 分发
│   ├── ap-evolution/             # 自进化引擎 (SQLite)
│   └── ap-runtime/               # Python 子进程桥接
├── tests/                        # 测试
│   ├── unit/                     # 单元测试
│   ├── integration/              # 集成测试
│   └── e2e/                      # 端到端测试
├── templates/                    # OrchestrationDSL TOML 模板
├── docs/                         # 设计文档
├── Cargo.toml                    # Rust workspace
└── pyproject.toml                # Python 包配置
```

## 技术栈

| 层 | 技术 |
|----|------|
| Python 平台 | Python 3.11+, Pydantic, FastMCP, Typer, asyncio |
| Rust 平台 | Rust 2021, Tokio, Axum, Rusqlite, Clap, Git2 |
| 协议 | MCP (stdio/SSE), JSON-lines IPC, TOML DSL |
| 存储 | SQLite (TaskGraph + Evolution), TOML (配置) |
| 分发 | Git (Homebrew Tap 模型) |

## 安全架构（纵深防御）

1. **进程边界** — Agent 以独立子进程运行
2. **PermissionChecker** — 执行前权限检查（DEFAULT / PLAN / FULL_AUTO）
3. **SecurityChecker** — 运行时 AST 级别代码安全分析

## 文档

完整设计文档位于 `docs/` 目录，详见 `docs/README.md`。

| 文档 | 位置 |
|------|------|
| 产品定位与核心架构 | `docs/01-overview.md` |
| 编排层设计 | `docs/02-clawteam-integration.md` |
| Python Runtime | `docs/03-python-runtime.md` |
| 自进化引擎 | `docs/04-self-evolution.md` |
| Agent 体系 | `docs/05-agent-system.md` |
| MCP 通信矩阵 | `docs/06-mcp-communication.md` |
| Git 分发与质量门禁 | `docs/07-marketplace.md` |
| 约束与决策 | `docs/08-constraints-decisions.md` |
| 实施计划 | `docs/09-implementation-plan.md` |

## 许可证

MIT License
