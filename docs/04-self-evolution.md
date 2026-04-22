# Self-Evolution Engine

> Agent Nexus Design Doc — §6 Self-Evolution Engine：OpenSpace 核心机制、双层自进化设计、三触发器 + 防循环机制、质量指标、健康诊断阈值、进化引擎架构、SQLite Schema

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/platform/evolution/` (Engine 250, Store 1392, Analyzer 306, Evolver 443, Compaction 220, Health 310, Promotion 408, ContextDescriber 340, Thresholds 118 — 3,837 lines total)
> **Tests**: `tests/unit/test_evolution_engine.py`, `tests/unit/test_evolution_store.py`, `tests/unit/test_evolution_analyzer.py`, `tests/unit/test_evolution_evolver.py`, `tests/unit/test_evolution_compaction.py`, `tests/unit/test_evolution_health.py`, `tests/unit/test_evolution_promotion.py`, `tests/unit/test_evolution_thresholds.py`, `tests/unit/test_evolution_models.py`, `tests/unit/test_evolution_module.py`

## §6 Self-Evolution Engine

> **参考项目**: [HKUDS/OpenSpace](https://github.com/HKUDS/OpenSpace) — MIT License, Data Intelligence Lab@HKU
>
> **本地源码**: `/Users/yangyitian/Documents/dev/Agents/OpenSpace/`

### 6.1 OpenSpace 核心机制

> **参考模块**: OpenSpace `openspace/skill_engine/types.py` — `EvolutionType`(FIX/DERIVED/CAPTURED), `SkillRecord`, `SkillLineage`; `openspace/skill_engine/analyzer.py` — `ExecutionAnalyzer`; `openspace/skill_engine/evolver.py` — `SkillEvolver`, `EvolutionTrigger`; `openspace/skill_engine/store.py` — `SkillStore`(SQLite); `openspace/skill_engine/registry.py` — `SkillRegistry`(BM25+embedding); `openspace/skill_engine/patch.py` — 多文件 patch; `openspace/tool_layer.py` — `OpenSpace` 主入口

OpenSpace 实现了完整的 Skill 生命周期自管理系统，其进化引擎是 DAG 版本管理系统。

**三种进化模式：**

| 模式 | 触发条件 | 产出 | 父节点 |
|------|---------|------|--------|
| **FIX** | Skill 应用失败 | 同名新版本（in-place 更新） | 恰好 1 个（前版本） |
| **DERIVED** | 成功模式可增强/合并 | 新 Skill（新目录/新名称） | 1+ 个（单→增强，多→合并） |
| **CAPTURED** | 无 Skill 参与但任务成功 | 全新 Skill（无父节点） | 0 个 |

**关键源码模块（`openspace/skill_engine/`）：**

| 模块 | 职责 |
|------|------|
| `types.py` | 数据模型：SkillRecord, SkillLineage, EvolutionType, ExecutionAnalysis |
| `analyzer.py` | ExecutionAnalyzer — 任务后 LLM 分析，产出 EvolutionSuggestion |
| `evolver.py` | SkillEvolver — 执行 FIX/DERIVED/CAPTURED 进化，LLM Agent 循环（≤5轮） |
| `store.py` | SkillStore — SQLite 持久化，原子计数器更新 |
| `registry.py` | SkillRegistry — 发现、BM25+embedding 混合排序、LLM 选择 |
| `patch.py` | 多文件 patch（FULL / DIFF / PATCH 三种 LLM 输出格式） |

**OpenSpace 实测数据（GDPVal Benchmark）：**
- 50 个专业任务中自主进化 **165 个 Skill**
- **4.2×** 收入提升 vs 基线 Agent
- **46%** Token 节省

### 6.2 双层自进化设计（自建）

借鉴 OpenSpace 架构自建进化引擎（非直接依赖），因为我们需要 Agent 级别 + 编排级别的双层进化。

#### Layer 1: Atomic Agent Skill 进化

```
Atomic Agent 执行任务
  → ExecutionAnalyzer 分析:
    ├── Skill 是否被正确应用 (applied_rate)
    ├── 任务是否成功 (completion_rate)
    ├── 哪些步骤失败 → FIX 候选
    └── 哪些成功模式可固化 → CAPTURED 候选
  → 自动触发:
    ├── FIX: 修复 broken 的 SKILL.md 步骤
    └── CAPTURED: 从高频成功模式提取新 Skill
  → 关键指标: effective_rate, fallback_rate
```

#### Layer 2: Composite Agent 编排进化

```
Composite Agent 协调多个 Atomic Agent
  → 分析:
    ├── 各 Atomic Agent 调用顺序是否最优
    ├── 是否有 Agent 被不必要地调用
    ├── 是否缺少某个步骤
    └── 并行机会是否充分利用
  → 自动触发:
    ├── DERIVED: 优化 TOML 编排模板（调整 agent 顺序、并行策略）
    └── CAPTURED: 从成功的编排模式创建新 Composite Agent
  → 关键指标: 端到端任务成功率、平均步骤数、平均耗时
```

#### Layer 3: Agent Promotion（Skill → Agent 提升）

```
条件: 当某个 CAPTURED Skill 满足:
  - effective_rate > 阈值（如 0.8）
  - total_selections > 最小值（如 50）
  - 涵盖完整的独立工作流（非片段）

动作:
  1. 将 Skill "提升"为独立 Atomic Agent
  2. 生成 Agent Package (SKILL.md + config + entry_point + composition.toml)
  3. 注册到 MCP Server
  4. 发布到 Git 源（提交 PR 到 Official 或 push 到 Private repo）
```

### 6.3 三触发器 + 防循环机制

> **参考模块**: OpenSpace `openspace/skill_engine/evolver.py` — `process_analysis()`, `process_tool_degradation()`, `process_metric_check()`; `openspace/grounding/core/quality/manager.py` — `ToolQualityManager.get_problematic_tools()` 驱动 TOOL_DEGRADATION 触发器

| 触发器 | 方法 | 防循环 |
|--------|------|--------|
| Post-Analysis（每次任务后） | `process_analysis()` | 内置于执行流 |
| Tool Degradation（工具退化） | `process_tool_degradation()` | `_addressed_degradations` 集合 |
| Periodic Metric（每 N 次调用） | `process_metric_check(min_selections=5)` | 新进化 Skill 总 selections=0，需积累 5 次 |

### 6.4 质量指标

```python
total_selections: int    # 被选中次数
total_applied: int       # 实际应用次数
total_completions: int   # 应用后任务成功次数
total_fallbacks: int     # 选中但未应用 + 任务失败次数

applied_rate = total_applied / total_selections
completion_rate = total_completions / total_applied
effective_rate = total_completions / total_selections  # 端到端有效性
fallback_rate = total_fallbacks / total_selections
```

### 6.5 健康诊断阈值

规则引擎预过滤，LLM 做最终判断：

| 条件 | 阈值 | 进化类型 |
|------|------|---------|
| 高回退率 | `fallback_rate > 0.4` | FIX |
| 应用但低完成 | `applied_rate > 0.4` 且 `completion_rate < 0.35` | FIX |
| 中等效果 | `effective_rate < 0.55` 且 `applied_rate > 0.25` | DERIVED |

### 6.6 进化引擎架构

> **实现模块**: `src/agent_nexus/platform/evolution/engine.py` — `EvolutionEngine` (统一门面), `src/agent_nexus/platform/evolution/evolver.py` — `SkillEvolver` (FIX/DERIVED/CAPTURED), `src/agent_nexus/platform/evolution/analyzer.py` — `ExecutionAnalyzer` (任务后分析), `src/agent_nexus/platform/evolution/health.py` — `HealthChecker` (阈值诊断), `src/agent_nexus/platform/evolution/compaction.py` — `CompactionGuard` (防死循环), `src/agent_nexus/platform/evolution/promotion.py` — `AgentPromoter` (Skill→Agent 提升), `src/agent_nexus/platform/evolution/store.py` — `EvolutionStore` (SQLite 持久化)

```python
class EvolutionEngine:
    """
    自建进化引擎，借鉴 OpenSpace 架构但扩展到 Agent 级别。

    核心组件:
    - ExecutionAnalyzer: 任务后 LLM 分析
    - SkillEvolver: FIX / DERIVED / CAPTURED 三模式进化
    - OrchestrationEvolver: 编排级别进化（TOML 优化）
    - AgentPromoter: Skill → Agent 提升
    - EvolutionStore: SQLite 持久化（DAG 版本管理）
    """

    triggers = [
        PostAnalysisTrigger(),
        ToolDegradationTrigger(),
        MetricMonitorTrigger(min_selections=5),
    ]

    async def evolve(self, ctx: EvolutionContext) -> Optional[SkillRecord]:
        # 1. LLM Agent 循环（≤5 轮）
        # 2. Apply-Retry（≤3 次）
        # 3. 验证 + 持久化
        ...

    async def evolve_orchestration(
        self, composite_id: str, analysis: ExecutionAnalysis
    ) -> Optional[str]:
        # 分析 TOML 模板效率，优化 agent 调度
        ...
```

### 6.7 SQLite Schema

> **实现模块**: `src/agent_nexus/platform/evolution/store.py` — EvolutionStore，WAL 模式，connection-per-operation，包含 `skill_records`, `skill_lineage_parents`, `execution_analyses`, `skill_judgments`, `context_budget_log`, `agent_records` 六张表

```sql
-- Skill 记录（含进化 DAG + 质量计数器）
CREATE TABLE skill_records (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0.0',
    lineage_origin TEXT NOT NULL DEFAULT 'imported',
    lineage_generation INTEGER NOT NULL DEFAULT 0,
    lineage_content_diff TEXT,
    lineage_content_snapshot TEXT,
    directory TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    total_selections INTEGER NOT NULL DEFAULT 0,
    total_applied INTEGER NOT NULL DEFAULT 0,
    total_completions INTEGER NOT NULL DEFAULT 0,
    total_fallbacks INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_sr_active ON skill_records(is_active);
CREATE INDEX idx_sr_name ON skill_records(name);
CREATE INDEX idx_sr_updated ON skill_records(updated_at);

-- DAG 边（多对多）
CREATE TABLE skill_lineage_parents (
    skill_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    PRIMARY KEY (skill_id, parent_id),
    FOREIGN KEY (skill_id) REFERENCES skill_records(id),
    FOREIGN KEY (parent_id) REFERENCES skill_records(id)
);
CREATE INDEX idx_lp_parent ON skill_lineage_parents(parent_id);

-- 任务分析（每任务每 Agent 一条，独立 UUID 主键）
CREATE TABLE execution_analyses (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    analysis TEXT NOT NULL,
    evolution_suggestions TEXT,  -- JSON array
    created_at TEXT NOT NULL
);
CREATE INDEX idx_ea_task ON execution_analyses(task_id);

-- Skill 评估（每分析每 Skill 一条，独立 UUID 主键）
CREATE TABLE skill_judgments (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    fell_back INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (analysis_id) REFERENCES execution_analyses(id)
);
CREATE INDEX idx_sj_skill ON skill_judgments(skill_id);
CREATE INDEX idx_sj_analysis ON skill_judgments(analysis_id);

-- Context Budget 日志（Token 优化追踪）
CREATE TABLE context_budget_log (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tokens_before INTEGER,
    tokens_after INTEGER,
    details TEXT,  -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX idx_cbl_agent ON context_budget_log(agent_name);

-- Agent 级别记录（Composite Agent 进化追踪，Layer 2）
CREATE TABLE agent_records (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'atomic',  -- atomic | composite
    skill_ids TEXT DEFAULT '[]',  -- JSON array
    orchestration_toml TEXT,
    effective_rate REAL DEFAULT 0.0,
    avg_steps REAL,
    avg_duration_ms REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_ar_active ON agent_records(is_active);
CREATE INDEX idx_ar_name ON agent_records(name);
```

### 6.8 进化数据分层注入

> **实现模块**: `src/agent_nexus/platform/evolution/context_describer.py` — `EvolutionContextDescriber`

> **参考来源**: nanobot Token 优化方案 — Evolution Engine 数据是 Agent Context 的重要组成部分

进化引擎产出的数据需要分层注入到 Agent Context 中，避免全量注入导致 Token 膨胀。

| 层级 | 注入内容 | Token 估算 | 频率 |
|------|---------|-----------|------|
| L0（每轮） | Metrics 摘要（effective_rate, applied_rate 各 1 行） | ~30 | 每轮 |
| L1（首轮） | 当前任务相关的进化建议（EvolutionSuggestion 摘要） | ~200-500 | 首轮 |
| L2（按需） | 历史建议详情、FIX/DERIVED/CAPTURED 记录 | ~500-2000 | 按需 |

```python
class EvolutionContextDescriber:
    """进化引擎数据的分层描述"""

    def l0_context(self) -> str:
        """L0 Metrics 摘要（每轮注入，~30 tokens）"""
        agent = self.store.get_agent(self.agent_id)
        return (
            f"Evolution Metrics: effective={agent.effective_rate:.2f}, "
            f"applied={agent.applied_rate:.2f}, "
            f"completions={agent.total_completions}/{agent.total_selections}"
        )

    def l1_context(self, skill_ids: list[str] | None = None) -> str:
        """L1 当前任务相关的进化建议（首轮注入）"""
        suggestions = self.store.get_active_suggestions(
            agent_id=self.agent_id,
            skill_ids=skill_ids,
            limit=3
        )
        return "\n".join(
            f"- [{s.type}] {s.summary} (confidence: {s.confidence:.2f})"
            for s in suggestions
        )

    def l2_context(self, skill_ids: list[str] | None = None) -> str:
        """L2 按需获取完整进化历史"""
        lineage = self.store.get_lineage(skill_id)
        return lineage.describe_full_chain()
```

### 6.9 Compaction 防死循环设计

> **实现模块**: `src/agent_nexus/platform/evolution/compaction.py` — `CompactionGuard`

> **教训来源**: OpenClaw #68032 — Context 溢出时 Compaction 触发正反馈死循环

#### 6.9.1 问题描述

当 Agent Context 接近窗口上限时，系统触发 Compaction 压缩历史。如果 Compaction 后重注入的上下文仍然过大，会立即再次触发 Compaction，形成死循环：

```
Context 溢出 → Compaction → 重注入过多内容 → 再次溢出 → 再次 Compaction → ...
```

#### 6.9.2 防护机制

| 机制 | 参数 | 说明 |
|------|------|------|
| 最小间隔 | `min_turns_between_compactions=5` | 两次 Compaction 之间至少 5 轮对话 |
| 重注入限制 | 只重注入 L0 + L1 摘要 | Compaction 后不加载 L2/L3，控制在 L1 budget 内 |
| 硬上限检测 | `total_tokens > context_window * 0.9` | 超过 90% 时强制截断最早的历史消息 |
| 日志告警 | 写入 `context_budget_log` | 连续 3 次 Compaction 触发告警，需人工介入 |

```python
class CompactionGuard:
    """Compaction 防死循环守卫"""

    min_turns_between_compactions: int = 5
    forced_truncation_threshold: float = 0.9  # 90% 强制截断

    def should_compact(self, ctx: AgentContext) -> bool:
        if ctx.turn_number - ctx.last_compaction_turn < self.min_turns_between_compactions:
            return False
        return ctx.token_usage > ctx.context_window * ctx.compaction_trigger_threshold

    def reinject_after_compaction(self, ctx: AgentContext) -> str:
        """Compaction 后只重注入 L0 + L1 摘要"""
        l0 = self.tiered_describer.l0_context()
        l1_summary = self.tiered_describer.l1_context()
        return f"{l0}\n{l1_summary}"

    async def check_and_log(self, ctx: AgentContext) -> None:
        budget = ctx.token_usage / ctx.context_window
        await self.store.log_budget(
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            turn_number=ctx.turn_number,
            total_tokens=ctx.token_usage,
            compaction_triggered=1 if ctx.needs_compaction else 0,
        )
```

---
