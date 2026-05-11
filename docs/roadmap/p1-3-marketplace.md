# P1-3: Agent Marketplace + Quality Gate

> 优先级：P1 🟠 | 预估工期：W5-9 | 依赖 P0-2（auth 机制复用）

## 需求

- **来源**：竞品无原生市场，Homebrew Tap 模型唯一性
- **目标**：Git-based 分发升级为可信 Agent 市场（质量评分 + 签名 + 依赖解析 + 搜索发现）
- **需求强度**：⭐⭐⭐⭐ | **差异化**：⭐⭐⭐⭐⭐ | **实现复杂度**：高

## 当前状态

| 组件 | Python | Rust |
|------|--------|------|
| Git 安装器 (sparse-checkout) | ✅ `local/installer.py` | ✅ `ap-fetcher/installer.rs` |
| Source 管理 (3 种 source) | ✅ `local/sources.py` | ✅ `ap-fetcher/sources.rs` |
| Lockfile (flock + atomic write) | ✅ `local/lockfile.py` | ✅ `ap-fetcher/lockfile.rs` |
| 安全审计 | ❌ | ✅ `security_audit.rs` |
| Manifest 检查 | ⚠️ 基础（YAML） | ✅ `manifest_checker.rs`（TOML） |
| SKILL 检查 | ❌ | ✅ `skill_checker.rs` |
| 签名验证 | ❌ | ❌ |
| 依赖解析 | ❌ | ❌ |
| 搜索 API | ⚠️ 按名称 | ❌ |
| 评分系统 | ❌ | ❌ |

### 关键约束

- Python 用 `agent-manifest.yaml`，Rust 用 `agent.toml` — 双格式待统一
- Rust 已有 security_audit/manifest_checker/skill_checker，但未桥接到 Python
- Lockfile 扁平结构，无依赖图

## 设计方案

### 统一 Manifest TOML

```toml
# agent.toml
[agent]
name = "code-reviewer"
version = "1.2.0"
type = "atomic"
description = "Code review with SOLID/security analysis"
entry = "code_reviewer.mcp:serve"

[agent.model_config]
recommended = "anthropic:claude-sonnet-4-20250514"
fallback = "ollama:qwen2.5-coder:7b"

[agent.capabilities]
tags = ["code-review", "security", "solid"]
domains = ["software-engineering"]

[agent.permissions]
mode = "default"
allowed_tools = ["read_file", "search_files"]
denied_tools = ["execute_shell"]

[agent.dependencies]   # Composite only
atomic_agents = ["doc-filler", "test-suite-generator"]

[agent.quality]
min_coverage = 0.8
required_checks = ["security_audit", "skill_validation"]
```

迁移：双读兼容 3 个月 + 1 个月 deprecation warning → 移除 YAML reader。

### Quality Gate Pipeline

新增 `platform/local/quality_gate.py`：

```python
class QualityGate:
    def __init__(self):
        self._checks = [
            ManifestCheck(),       # agent.toml 格式和必填字段
            SkillFileCheck(),      # SKILL.md 存在性和必需段落
            SecurityAuditCheck(),  # 危险函数/密钥/网络访问
            DependencyCheck(),     # 依赖存在性和版本兼容
            TestCoverageCheck(),   # 测试覆盖（可选）
        ]
    async def evaluate(self, agent_path: Path) -> QualityGateResult
```

评分：Critical fail + Warning -0.1 + 最低线可配置（默认 0.6）。

### Sigstore 签名

```python
class AgentSigner:
    async def sign(self, agent_path: Path, identity_token: str) -> SignatureBundle
    async def verify(self, agent_path: Path, signature: SignatureBundle) -> bool
```

### 依赖解析（仅 direct deps）

```python
class DependencyResolver:
    async def resolve(self, agent: AgentManifest) -> ResolutionResult
    async def check_conflicts(self, agents: list[AgentManifest]) -> ConflictReport
```

## 已确认决策

| # | 决策 | 理由 |
|---|------|------|
| D14 | 3 个月过渡 + 1 个月 warning | 足够迁移，不无限拖延 |
| D15 | Quality Gate 评分可配置，默认 0.6 | 不同领域不同标准 |
| D16 | Sigstore 优先 | 免密钥管理，GitHub OIDC 一键签名 |
| D17 | 仅 direct deps | 无 composite-on-composite 需求 |
| D18 | 搜索按下载量排序 | 最客观，不可伪造 |

## 实施阶段

| Phase | 内容 | 工期 | 验证标准 |
|-------|------|------|----------|
| 1 | 统一 TOML + 双读兼容 | W5-6 | Python/Rust 都能读 TOML + YAML |
| 2 | Quality Gate Pipeline | W7-8 | 5 项检查通过/失败可测 |
| 3 | 依赖解析 + 冲突检测 | W8 | Composite 安装自动拉取依赖 |
| 4 | Sigstore 签名验证 | W9 | 签名/验签端到端 |
| 5 | 搜索增强 + Rust 同步 | W9 | 按能力/领域搜索，按下载量排序 |

## 依赖

- P0-2 auth 机制（签名验证复用认证基础设施）
- Sigstore Python/Rust SDK
- Python `tomllib` (3.12+)

## 风险

| 风险 | 缓解 |
|------|------|
| YAML→TOML 迁移 | 双读 + 自动迁移脚本 |
| QG 误报 | 可配置检查级别 + skip |
| Sigstore OIDC 依赖 | 备选 GPG |
| 依赖解析复杂度 | 先扁平，后续嵌套 |
