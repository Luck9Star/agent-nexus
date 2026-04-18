# Agent Nexus — POC v5

> 基于 ClawTeam 参考实现的 MCP-native Agent 平台，专攻需要深度领域知识、多 Agent 协作和质量保证的任务场景。用户本地运行，自配模型，无需平台计费。编排层自建（参考 ClawTeam），不作为外部依赖引入。Agent 通过 Git 仓库分发（Homebrew tap 模式）。

## 文档目录

| # | 章节 | 文件 | 说明 |
|---|------|------|------|
| 1-3 | 产品定位与核心架构 | [docs/01-overview.md](docs/01-overview.md) | §1 产品定位与愿景 + §2 竞争格局与差异化 + §3 核心架构 |
| 4 | 自建编排层 | [docs/02-clawteam-integration.md](docs/02-clawteam-integration.md) | §4 自建编排组件（参考 ClawTeam 实现）、TaskGraph、IPC、ProcessManager、OrchestrationDSL |
| 5 | Python Runtime | [docs/03-python-runtime.md](docs/03-python-runtime.md) | §5 Runtime vs Tool Call、CaveAgent 实测数据、Runtime-First Hybrid |
| 6 | Self-Evolution Engine | [docs/04-self-evolution.md](docs/04-self-evolution.md) | §6 OpenSpace 进化机制、双层自进化、质量指标、SQLite Schema |
| 7 | Agent 体系 | [docs/05-agent-system.md](docs/05-agent-system.md) | §7 Atomic/Composite Agent、三种运行模式、Agent Package 结构 |
| 8 | MCP 暴露与通信 | [docs/06-mcp-communication.md](docs/06-mcp-communication.md) | §8 FastMCP 双模式、MCP Gateway、通信矩阵、SKILL.md 规范 |
| 9 | Agent 分发与质量 | [docs/07-marketplace.md](docs/07-marketplace.md) | §9 Git-based 分发、安装流程、质量验证、发布流程、版本管理、源管理 |
| 10 | 技术约束与设计决策 | [docs/08-constraints-decisions.md](docs/08-constraints-decisions.md) | §10 设计约束、技术约束、质量约束、模型配置、许可证 |
| 11 | 实施计划 | [docs/09-implementation-plan.md](docs/09-implementation-plan.md) | §11 7 Phase 实施计划 + 风险矩阵 + 项目结构 |
| 12 | Git-Based 分发与本地架构 | [docs/10-cloud-local-architecture.md](docs/10-cloud-local-architecture.md) | §12 Git 分发模型、本地架构、安装/发布流程、Python POC、Rust 重构 |
| A-D | 附录 | [docs/appendix.md](docs/appendix.md) | TOML Schema、Agent 类型对比、模型分层、参考项目 |

## 文件结构

```
agent-nexus/
├── POC.md                          # 本文件（索引页）
├── README.md
├── docs/
│   ├── 01-overview.md
│   ├── 02-clawteam-integration.md
│   ├── 03-python-runtime.md
│   ├── 04-self-evolution.md
│   ├── 05-agent-system.md
│   ├── 06-mcp-communication.md
│   ├── 07-marketplace.md
│   ├── 08-constraints-decisions.md
│   ├── 09-implementation-plan.md
│   ├── 10-cloud-local-architecture.md
│   └── appendix.md
```

## 参考项目

| 项目 | 许可证 | 用途 |
|------|--------|------|
| [ClawTeam](https://github.com/hkuds-lab/clawteam) | MIT | 编排层参考实现（TaskStore、MailboxManager、SpawnBackend） |
| [OpenSpace](https://github.com/HKUDS/OpenSpace) | MIT | Self-Evolution Engine 参考 |
| [CaveAgent](https://github.com/acodercat/cave-agent) | MIT | Python Runtime 参考 |
| [deer-flow](https://github.com/bytedance/deer-flow) | Apache-2.0 | Harness/App 分离、Skill loading 参考 |
| [OpenHarness](https://github.com/HKUDS/OpenHarness) | MIT | Permission/Hook/Plugin 架构参考 |
| [nanobot](https://github.com/icemachined/nanobot) | MIT | Token 优化、Deferred Loading 参考 |
