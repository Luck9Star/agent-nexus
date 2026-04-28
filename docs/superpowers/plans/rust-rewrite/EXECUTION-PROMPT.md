# Rust Rewrite Execution Prompt

## 复制以下内容到新会话

```
执行 Rust 重写计划。从 Phase 01 开始，按依赖顺序逐步实施。

## 背景

agent-nexus 项目正在将 Python Platform 层重写为 Rust。12 个子计划文件已经过评审，17 个发现中 15 个已修复（3 个 P0 全部解决），计划可执行。

## 计划文件位置

所有计划在 `docs/superpowers/plans/rust-rewrite/` 下：
- `00-master-plan.md` — 总览、依赖关系、crate 结构
- `01-ap-core-models.md` — 数据模型（Phase 01，所有其他 phase 依赖此）
- `02-ap-core-config.md` — 配置系统
- `03-ap-core-orchestration.md` — TaskGraph + ProcessManager + IPC + DSL
- `04-ap-gateway.md` — MCP Gateway
- `05-ap-core-hooks-skills.md` — Hook 执行器 + Skill 加载器
- `06-ap-runtime.md` — Agent 运行时
- `07-ap-fetcher.md` — Git 安装器 + Source/Lockfile 管理
- `08-ap-evolution.md` — 自进化引擎
- `09-ap-cli.md` — CLI 入口
- `10-ap-cli-commands.md` — CLI 命令实现
- `11-compatibility-tests.md` — Python 兼容性测试
- `REVIEW-findings.md` — 评审结果（15/17 fixed，F-16/F-17 non-blocking）

## 执行规则

1. **严格按计划执行** — 每个计划有编号的 Task 和 Step，按序执行。每个 Step 都是 test-first（先写测试再实现）。
2. **Wire-format 兼容** — Rust 必须能读写 Python 生成的文件（SQLite、JSON、YAML、TOML）。IPC 用 flat struct 不是 tagged enum。
3. **每完成一个 Task 验证一次** — `cargo test -p <crate>` + `cargo clippy -p <crate> -- -D warnings`。
4. **每完成一个 Task commit 一次** — 用计划中的 commit message。
5. **6-crate workspace 结构** — ap-core, ap-runtime, ap-gateway, ap-fetcher, ap-evolution, ap-cli。先创建 workspace 和 crate skeleton（参考 master plan）。

## 执行顺序

```
Phase 01 (ap-core models) → Phase 02 (config) → Phase 03 (orchestration)
→ Phase 05 (hooks/skills) → Phase 06 (runtime) → Phase 04 (gateway)
→ Phase 07 (fetcher) → Phase 08 (evolution) → Phase 09+10 (CLI)
→ Phase 11 (compatibility tests)
```

## 开始

1. 先读 `docs/superpowers/plans/rust-rewrite/00-master-plan.md` 了解全局结构
2. 读 `docs/superpowers/plans/rust-rewrite/01-ap-core-models.md` 开始实施
3. 如果 crates/ 目录不存在，先按 master plan 创建 Cargo workspace
4. 从 Task 1.1 开始，先写测试再写实现，每个 Task 跑 cargo test 验证后 commit
```
