# Archived Documents

本文档夹包含已完成、过时或被替代的文档。

## 目录结构

```
archive/
├── superpowers/
│   ├── completed-specs/      # 已完成的实现计划和设计规范 (19 个文件)
│   ├── deprecated/           # 废弃的文档 (1 个文件)
│   └── superseded-designs/   # 被替代的设计文档 (1 个文件)
└── README.md (本文件)
```

## completed-specs/

已完成的 Rust Rewrite 实现计划和设计规范：

| 文件 | 描述 | 对应实现 |
|------|------|---------|
| 00-workspace-setup.md | 工作区 + Cargo.toml 设置 | ✅ |
| 01-ap-core-models.md | 核心类型模型 (11 个模块) | ✅ |
| 02-ap-core-config.md | 配置加载器 | ✅ |
| 03-ap-core-orchestration.md | 编排层 (TaskGraph, ProcessManager) | ✅ |
| 04-ap-core-router.md | Platform Router | ✅ |
| 05-ap-core-hooks-skills.md | Hooks + Skills 系统 | ✅ |
| 06-ap-runtime.md | Runtime 层 | ✅ |
| 07-ap-fetcher.md | Git Fetcher | ✅ |
| 08-ap-evolution.md | 进化引擎 | ✅ |
| 09-ap-gateway.md | MCP Gateway | ✅ |
| 10-ap-cli.md | CLI 命令 | ✅ |
| 11-integration-tests.md | Integration Tests | ⚠️ 部分 |
| 2026-04-20-cli-command-system-design.md | CLI 命令系统设计 | ✅ |
| 2026-04-22-rust-rewrite-design.md | Rust 重写设计 | ✅ |
| 2026-04-27-config-consolidation-design.md | 配置整合设计 | ✅ |
| EXECUTION-PROMPT.md | 执行提示 | - |
| REVIEW-findings.md | Review 发现记录 | - |

## deprecated/

废弃的文档：

| 文件 | 原因 |
|------|------|
| 11-agency-agents-integration.md | 被 `2026-04-27-agency-llm-integration.md` 替代 |

## superseded-designs/

被替代的设计文档：

| 文件 | 原因 |
|------|------|
| 13-mcp-ecosystem-liteLLM-plan.md | LiteLLM 集成计划从未实现 |
