# 开发协议指南

本项目使用三套开发协议，覆盖完整的开发生命周期。每套协议有明确的适用场景、流程阶段和退出条件。

## 快速选择

| 场景 | 使用哪个协议 | 文件 |
|------|------------|------|
| 新增 Agent / API / 模块 / DSL | Feature Dev Protocol | `feature-dev-protocol.md` |
| 模块拆分 / API 变更 / Rust 重写 / 抽象提取 | Refactor Protocol | `refactor-protocol.md` |
| Bug 搜索 / 安全审计 / 质量迭代 | Quality Protocol | `quality-protocol.md` |

## 协议间的流转

```
新功能开发 → Feature Dev (D1→D2→D3→D4) → 完成后交 Quality
结构重构   → Refactor   (R1→R2→R3→R4) → 完成后交 Quality
Bug 修复   → Quality    (A→B→C)        → 直接使用

开发中发现 bug     → 记录，当前协议完成后再切 Quality
重构中发现需新功能  → 记录，当前协议完成后再切 Feature Dev
```

## 协议核心差异

| 维度 | Feature Dev | Refactor | Quality |
|------|------------|----------|---------|
| 核心原则 | SKILL.md 先于代码，接口先于实现 | 行为不变 = 测试不动，小步可回退 | 穷尽验证 + 收敛仪表盘，数据驱动停止 |
| 测试策略 | 先写测试再填实现 | 原有测试不动，新实现必须通过 | 每种模式修复后二次扫描确认零残留 |
| 退出关注 | 集成点验证 + 文档对齐 | 不变量逐项验证 + d=1 调用方回归 | 收敛仪表盘：连续 2 轮 P0/P1=0 → 停止 |

## 使用方式

在 Claude Code 中，直接引用协议文件路径即可：

```
按照 .claude/feature-dev-protocol.md 的 SOP 执行
按照 .claude/refactor-protocol.md 的 SOP 执行
按照 .claude/quality-protocol.md 的 SOP 执行
```

## 关键约束（三条红线）

1. **不跳阶段** — 每个 Phase 的退出条件必须全部满足才能进入下一个 Phase
2. **不混协议** — Feature 时不"顺手"重构，Refactor 时不"顺手"加功能
3. **不空口完成** — 每个退出条件必须有验证命令的输出证据

## 测试通用规则

- 测试文件按模块命名：`test_{module_name}.py`
- Mock read/readline 必须用 `b""`（防无限循环）
- IPython InteractiveShell 用 session-scoped fixture
- 单测文件 ≤ 30s，全量测试 ≤ 180s
- Agent 测试优先用 `AsyncMock` 模拟 IPC，不启动真实子进程
