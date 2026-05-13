# 14. Structured Reasoning Protocol

> **Status**: Approved
> **Date**: 2026-05-07
> **Source**: GenericAgent (lsdefine/GenericAgent) 架构分析 + 本地 grill-me 决策

## 1. 背景与动机

当前 Agency Pipeline 的专家执行是**单次 LLM 调用**模式：`LLMExecutor.__call__` 构建 system prompt → `client.call()` → `_parse_sections()`。每个专家只有一次机会输出完整分析，没有结构化推理过程。

GenericAgent 项目（~3K 行核心代码）实现了一套 `<thinking>/<summary>/<tool_use>` 三段式协议，强制模型按"思考→总结→行动"的结构输出。这套协议在多轮任务中显著提升了输出质量。

本方案从 GenericAgent 提取核心协议机制，以最小侵入方式嫁接到 Agency Pipeline。

## 2. 设计决策记录

通过 grill-me 逐项确认的决策树：

| # | 决策点 | 选项 | 最终决定 | 理由 |
|---|--------|------|---------|------|
| 1 | 注入路线 | A) 只改 system prompt / B) 改执行循环 | **A** | 最小侵入，不改 LLMClient |
| 2 | thinking 提取 | A1) 丢弃 / A2) 提取到 metadata | **A2** | 可观测、可调试、下游可消费 |
| 3 | 开关颗粒度 | L1) 全局 CLI / L2) per-expert / L3) 组合 | **L1** | 先做最简版本验证效果 |
| 4 | 命名 | `--thinking-protocol` / `--reasoning-protocol` | **`--reasoning-protocol`** | 避免与模型原生 thinking 混淆 |
| 5 | 协议语言 | 全英 / 全中 / 双语 | **全英文** | 与现有 system prompt 语言一致，LLM 遵循度更高 |
| 6 | 注入位置 | A) body 前 / B) sections 前 / C) 替换 sections 指令 | **C** | 合并为一条输出格式规则，避免冲突 |
| 7 | 提取范围 | 只 thinking / 只 summary / 都提取 | **都提取** | `reasoning` 给人看，`expert_summary` 给机器看 |

## 3. 改动范围

### 3.1 改动的文件

| 文件 | 改动内容 |
|------|---------|
| `src/agent_nexus/platform/agency/executor.py` | `_build_system_prompt` 新增 `reasoning_protocol` 参数；新增 `_extract_reasoning_tags` / `_strip_reasoning_tags`；`__call__` 中提取标签到 metadata |
| `src/agent_nexus/platform/agency/cli.py` | `run_composition` 加 `--reasoning-protocol` flag，透传 |
| `src/agent_nexus/platform/agency/task_composer.py` | `TaskComposerInput` 加 `reasoning_protocol: bool`；`TaskComposer.run` 透传 |

### 3.2 不改的文件

| 文件 | 理由 |
|------|------|
| `llm_client.py` | 仍然是单次调用，不动 |
| `llm_planner.py` | 已有 `response_format="json"`，不受影响 |
| `llm_integrator.py` | 本迭代不消费 `expert_summary`，后续迭代 |
| expert profiles | 协议是 system prompt 层面注入，不改 profile 格式 |

## 4. 协议文本

当 `--reasoning-protocol` 开启时，`_build_system_prompt` 中原有的 sections 指令被替换为：

```
Follow this response protocol strictly:
1. **Think**: Analyze the task inside <thinking> tags. Consider multiple
   perspectives, identify edge cases, and evaluate trade-offs.
2. **Summarize**: Output a one-line (<30 words) physical snapshot in <summary>
   tags capturing your key finding and confidence level.
3. **Structure**: Output your analysis as ## markdown headings using exactly
   these section names: {section_list}. Provide substantive content under each.
```

### 关闭时的行为（默认）

`_build_system_prompt` 输出与当前完全一致：

```
Your response must include these sections as ## markdown headings: {section_list}.
Use exactly these heading names so they can be parsed. Provide substantive content under each heading.
```

## 5. 标签提取逻辑

在 `LLMExecutor.__call__` 中，`_parse_sections` 之前执行：

```python
if self._reasoning_protocol:
    thinking, summary = _extract_reasoning_tags(response.text)
    clean_text = _strip_reasoning_tags(response.text)
else:
    thinking, summary = None, None
    clean_text = response.text

sections = self._parse_sections(clean_text, required_sections)

metadata = {
    "llm": True,
    "model": response.model,
    "provider": response.provider,
}
if thinking is not None:
    metadata["reasoning"] = thinking
if summary is not None:
    metadata["expert_summary"] = summary
```

### 提取函数

```python
import re

_REASONING_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)

def _extract_reasoning_tags(text: str) -> tuple[str | None, str | None]:
    """Extract <thinking> and <summary> content from LLM response."""
    t_match = _REASONING_RE.search(text)
    s_match = _SUMMARY_RE.search(text)
    thinking = t_match.group(1).strip() if t_match else None
    summary = s_match.group(1).strip() if s_match else None
    return thinking, summary

def _strip_reasoning_tags(text: str) -> str:
    """Remove <thinking> and <summary> blocks from text before section parsing."""
    text = _REASONING_RE.sub("", text)
    text = _SUMMARY_RE.sub("", text)
    return text.strip()
```

## 6. CLI 集成

```python
# cli.py run_composition 命令新增参数（使用 typer）
@app.command()
@click.option("--reasoning-protocol", is_flag=True, default=False,
              help="Enable structured reasoning protocol for expert execution")
def run_composition(..., reasoning_protocol: bool):
    ...
    # reasoning_protocol 通过 TaskComposerInput 透传到 LLMExecutor
```

## 7. 测试策略

### 单元测试

| 测试 | 验证点 |
|------|--------|
| `test_extract_reasoning_tags_both` | 输入含 `<thinking>` 和 `<summary>`，正确提取 |
| `test_extract_reasoning_tags_missing` | 输入不含标签，返回 `(None, None)` |
| `test_strip_reasoning_tags` | 剥离后不影响 `## sections` |
| `test_build_system_prompt_with_protocol` | 协议开启时输出包含三段式指令 |
| `test_build_system_prompt_without_protocol` | 协议关闭时输出与当前一致 |
| `test_parse_sections_after_strip` | 标签剥离后 `## heading` 分节正确 |

### 集成测试

开启 `--reasoning-protocol` 端到端跑一个 task，验证：
1. `<thinking>` 不污染 sections 内容
2. metadata 中有 `reasoning` 和 `expert_summary`
3. sections 内容质量不低于未开启时

## 8. 未来迭代

| 迭代 | 内容 | 前置条件 |
|------|------|---------|
| v2 | `LLMIntegrator` 消费 `expert_summary` 替代完整 sections 注入 | v1 数据验证有效 |
| v2 | per-expert `reasoning_protocol` 配置 | profile schema 升级 |
| v3 | 多轮执行模式（2-3 轮循环） | `LLMClient` 支持多轮对话 |
| v3 | `<thinking>` 内容作为质量信号 | QA Gate 可消费 reasoning |
