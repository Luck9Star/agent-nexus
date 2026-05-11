# P2-5: Self-Evolution 产品化

> 优先级：P2 🟡 | 预估工期：W9-14 | 依赖 P1-4（更多 agent 为进化提供数据）

## 需求

- **来源**：Self-Evolution 学术爆发（EvoAgentX EMNLP'25、MASLab、SPIRAL），OpenSpace 云端能力可复用
- **目标**：将现有三层进化引擎从研究原型升级为生产可用系统（LLM 驱动 skill 内容修改 + A/B 测试 + 可观测性）
- **需求强度**：⭐⭐⭐ | **差异化**：⭐⭐⭐⭐ | **实现复杂度**：高

## 当前状态

| 组件 | 状态 | 文件 |
|------|------|------|
| SkillEvolver | ⚠️ 半成品 | `platform/evolution/evolver.py` — 只记录元数据，不修改 SKILL.md 内容 |
| ExecutionAnalyzer | ✅ 成熟 | Levenshtein 模糊匹配 + 判断 + 建议 |
| HealthChecker | ✅ 成熟 | 三条阈值规则 |
| AgentPromoter | ✅ 成熟 | 完整 agent 包生成 |
| EvolutionStore | ✅ 成熟 | SQLite WAL，6 张表 |
| CompactionGuard | ✅ 成熟 | 分层上下文管理 |
| LLM 集成 | ❌ 缺失 | 无 LLM 调用生成改进内容 |
| Agency 集成 | ❌ 缺失 | 不在 pipeline 生命周期内 |
| 可观测性 | ❌ 缺失 | 无 dashboard/metrics |
| A/B 测试 | ❌ 缺失 | 进化即替换，无并行验证 |
| 回滚机制 | ❌ 缺失 | 无 rollback API |
| 配置化 | ❌ 缺失 | 阈值全部硬编码 |

### 关键发现

**最核心的差距**：SkillEvolver 不修改 skill 内容。OpenSpace 有 `patch.py` (33KB) 做 LLM 驱动的 skill 内容修改，agent-nexus 只做元数据记录。这不是"进化"，是"记账"。

### 关键约束

- EvolutionStore 6 张表的 schema 已固定，新字段需 additive
- HealthChecker 的三条阈值规则是硬编码的
- Agency Pipeline 有 4 个明确的阶段点（Planner → Executor → Integrator → QAGate）

## 设计方案

### SkillPatcher — LLM 驱动的 Skill 内容进化

新增 `platform/evolution/skill_patch.py`：

```python
class SkillPatcher:
    """LLM 驱动的 Skill 内容修改"""

    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def generate_fix(self, skill: SkillRecord, diagnosis: HealthDiagnosis) -> PatchResult
    async def generate_derived(self, skill: SkillRecord, insights: list[str]) -> PatchResult
    async def validate_patch(self, original: str, patched: str) -> ValidationResult

class PatchResult(BaseModel):
    original_content: str
    patched_content: str
    diff: str                  # unified diff
    patch_type: EvolutionType
    confidence: float          # LLM 置信度 0-1
    validation: ValidationResult

class ValidationResult(BaseModel):
    syntax_valid: bool         # Markdown/代码语法检查
    security_pass: bool        # SecurityChecker 通过
    test_pass: bool | None     # 测试通过（如有测试）
    regression_risk: float     # 回归风险 0-1
```

**LLM Patch 循环**：
```
HealthChecker 发现问题 → SkillEvolver 生成进化策略 → LLM 生成改进内容
→ 验证改进内容（语法/安全/测试）→ 写入新版本 SKILL.md → 记录 lineage
```

### Agency Pipeline 集成

在 `LLMQualityGate` 之后插入 evolution hook：

```python
# agency/pipeline.py 中的集成点
class AgencyPipeline:
    async def run(self, task: str, ...):
        # ... 现有 4 阶段 ...

        # 新增：任务完成后触发进化分析
        if self.evolution_engine:
            await self.evolution_engine.post_analysis(EvolutionContext(
                agent_id=self.agent_id,
                task_id=task_id,
                task_description=task,
                task_completed=result.success,
                skill_ids_used=result.skills_used,
                skills_applied=result.skills_applied,
                skills_fell_back=result.skills_fell_back,
            ))
```

### A/B 测试与回滚

```python
class EvolutionExperimenter:
    """Skill 进化 A/B 测试"""

    async def create_experiment(self, parent: SkillRecord, evolved: SkillRecord) -> Experiment
    async def assign(self, experiment: Experiment) -> SkillRecord  # 随机分配版本
    async def record_outcome(self, experiment_id: str, skill_id: str, success: bool) -> None
    async def evaluate(self, experiment_id: str) -> ExperimentResult

class ExperimentResult(BaseModel):
    parent_performance: float
    evolved_performance: float
    confidence: float
    recommendation: Literal["promote", "revert", "continue"]
```

### 可观测性

```python
class EvolutionMetrics:
    evolution_total: Counter      # 进化总次数（按类型分）
    evolution_success: Counter    # 成功次数
    skill_active_count: Gauge     # 活跃 skill 数量
    promotion_total: Counter      # 提升总次数
    experiment_running: Gauge     # 进行中的 A/B 测试

class EvolutionDashboard:
    async def get_summary(self) -> EvolutionSummary
    async def get_skill_lineage(self, skill_id: str) -> LineageTree
    async def get_health_report(self) -> HealthReport
```

### 配置化

新增 `config/evolution.toml`：

```toml
[evolution]
enabled = true
auto_promote = false           # 自动提升（生产建议关闭）
max_evolution_per_day = 10

[evolution.thresholds]
fix_fallback_rate = 0.4
fix_applied_rate = 0.4
fix_completion_rate = 0.35
derived_effective_rate = 0.55
derived_applied_rate = 0.25
promotion_effective_rate = 0.8
promotion_min_selections = 50

[evolution.llm]
model = "anthropic:claude-sonnet-4-20250514"
temperature = 0.3
max_tokens = 4096

[evolution.experiment]
min_samples = 30
confidence_level = 0.95
max_duration_days = 7
```

## 已确认决策

| # | 决策 | 理由 |
|---|------|------|
| D22 | 进化 LLM model：可配置，默认 sonnet | 需要足够 reasoning，haiku 可能质量不足 |
| D23 | 进化生效方式：可配置，默认 A/B 测试 | 生产强制 A/B，开发阶段可跳过 |
| D24 | 回滚粒度：仅上一版本 | 任意版本需维护所有快照，成本高 |
| D25 | 进化质量判定：综合加权（0.5 effective + 0.3 fallback + 0.2 usage） | 防止单一指标被 gaming |

## 实施阶段

| Phase | 内容 | 工期 | 验证标准 |
|-------|------|------|----------|
| 1 | SkillPatcher LLM 集成 | W9-11 | 能自动修复一个已知问题的 skill |
| 2 | Agency Pipeline 集成 hook | W11 | 任务完成后自动触发进化分析 |
| 3 | 配置化 + 可观测性 | W12-13 | evolution.toml 可配置，CLI 可查看状态 |
| 4 | A/B 测试 + 回滚 | W13 | 进化版本与父版本并行测试，可回滚 |
| 5 | Rust 同步 | W13-14 | Python 全部完成后一次性同步到 Rust |

## 依赖

- LLMClient（已有，需确保进化用 model 可配置）
- Agency Pipeline 的 execution context
- OpenSpace `patch.py` 设计参考（不直接引入代码）
- P1-4 更多 agent 为进化提供更丰富的数据

## 风险

| 风险 | 缓解 |
|------|------|
| LLM 生成低质量修改 | validation pipeline + confidence 阈值 + 人工审核 |
| 进化回回归（越改越差） | A/B 测试 + 自动回滚 + 每日进化上限 |
| LLM 调用成本 | 限制进化频率 + 使用低成本 model |
| 与 Rust 进化引擎不一致 | Python 先行，Rust 后续同步 |

## OpenSpace 参考

| 特性 | OpenSpace | 本设计 |
|------|-----------|--------|
| Skill 内容修改 | `patch.py` (33KB) LLM 驱动 | SkillPatcher（新增） |
| Skill 排名 | `skill_ranker.py` (14KB) | 缺失（Phase 3 可加） |
| 对话格式化 | `conversation_formatter.py` (13KB) | 简化版 context_describer 已有 |
| Fuzzy 匹配 | `fuzzy_match.py` (10KB) 独立 | 内联在 analyzer.py |
