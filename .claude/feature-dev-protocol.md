# Feature Dev Protocol — 新功能开发协议

> **适用场景**: 新增 Agent、新增 API、新增模块、新增 DSL、跨模块功能开发。
> **不适用**: bug 修复 → 用 quality-protocol；结构重构 → 用 refactor-protocol。
> **核心原则**: SKILL.md 先于代码，接口先于实现，测试先于填充。

---

## 协议架构

```
Phase D1: 需求澄清 (Requirements)
  → 输出: SKILL.md + 接口契约 + 影响范围

Phase D2: 接口设计 (Design)
  → 输出: 数据模型 + 公开 API + 文件结构

Phase D3: 测试驱动实现 (Implement)
  → 输出: 测试通过 + 代码完成

Phase D4: 集成交接 (Integrate)
  → 输出: 全量测试绿 + 文档更新 → 交接给 Quality Protocol
```

---

## Phase D1: 需求澄清

### SOP

1. **写 SKILL.md** — 项目约定（见 CLAUDE.md）：所有 Agent 必须有 SKILL.md 才能写代码。内容包括：
   - 功能概述（1 段话）
   - 输入/输出规范
   - 依赖的模块和接口
   - 错误处理策略
   - 性能预期

2. **影响分析** — 使用 `gitnexus_impact` 确认改动范围：
   - 哪些现有模块会被影响？
   - 是否需要新增文件/目录？
   - 是否涉及 Pydantic model 变更？
   - 是否涉及数据库 schema 变更？

3. **依赖检查** — 确认外部依赖：
   - 需要新 pip 包？→ 加到 pyproject.toml
   - 需要新的参考项目？→ 克隆到参考路径
   - 需要新的 TOML 模板？→ 加到 templates/

4. **范围锁定** — 输出一份清单：

```markdown
## Feature Scope: [feature name]

### 新增文件
- [ ] src/agent_nexus/.../new_module.py
- [ ] tests/unit/test_new_module.py

### 修改文件 (影响分析已确认)
- [ ] file_a.py — 改什么
- [ ] file_b.py — 改什么

### 不改的文件 (影响分析已排除)
- file_c.py — 无关

### 外部依赖
- 无 / 或列出
```

### D1 退出条件

- [ ] SKILL.md 已创建（或在 agents/ 下对应目录）
- [ ] gitnexus_impact 已跑，blast radius 已记录
- [ ] 范围清单已输出，文件列表明确

---

## Phase D2: 接口设计

### SOP

1. **定义数据模型** — 先写 Pydantic model / dataclass：
   ```python
   # 先写 model，再写实现
   class NewFeatureInput(BaseModel):
       query: str = Field(min_length=1, max_length=500)
       ...

   class NewFeatureOutput(BaseModel):
       result: str
       ...
   ```

2. **定义公开 API** — 方法签名 + docstring，不写实现：
   ```python
   async def new_feature(input: NewFeatureInput) -> NewFeatureOutput:
       """One-line summary.

       Args:
           input: ...

       Returns:
           ...

       Raises:
           FeatureError: ...
       """
       ...
   ```

3. **定义错误类型** — 新功能的错误层次：
   ```python
   class FeatureError(Exception): ...
   class FeatureValidationError(FeatureError): ...
   class FeatureExecutionError(FeatureError): ...
   ```

4. **接口评审** — 用 `/code-review-expert` 审接口设计（不是审实现）

### D2 退出条件

- [ ] 数据模型已定义（Pydantic model 带约束）
- [ ] 公开 API 签名已定义
- [ ] 错误类型已定义
- [ ] 接口评审通过

---

## Phase D3: 测试驱动实现

### SOP

1. **写测试桩** — 基于接口定义写测试，覆盖：
   - 正常路径（happy path）
   - 边界条件（空输入、极长输入、特殊字符）
   - 错误路径（依赖失败、超时、异常输入）
   - 与现有模块的集成点

2. **实现代码** — 填充实现，让测试通过：
   - 一次实现一个方法/功能点
   - 每实现一个点就跑对应测试确认通过
   - 不在实现过程中"顺手"重构其他代码

3. **实现过程约束**:
   - 新增代码只用标准库 + 项目已有依赖
   - 不引入新的全局状态
   - 每个 `async def` 必须有对应的 `async` 测试
   - 每个 `close()`/`cleanup` 必须有对应的 teardown 测试
   - Mock read/readline 必须返回 `b""`（防无限循环）

4. **实现后检查**:
   ```bash
   # 跑新功能的测试
   uv run pytest tests/unit/test_new_module.py -v
   # 跑全量测试确认无破坏
   uv run pytest tests/ -x -q
   # Pyright 检查
   uv run pyright src/agent_nexus/.../new_module.py
   ```

### D3 退出条件

- [ ] 新功能测试全部通过
- [ ] 全量测试无回归
- [ ] Pyright 零新增 HIGH/CRITICAL
- [ ] 无 `TODO`/`FIXME`/`HACK` 残留

---

## Phase D4: 集成交接

### SOP

1. **集成点验证** — 确认新功能与现有系统的集成：
   - 被调用方：调用新功能的代码是否正确传参？
   - 调用方：新功能调用的老代码是否行为一致？
   - 注册/发现：Agent 是否正确注册到 Gateway/Router？

2. **文档更新**:
   - `docs/09-implementation-plan.md` checkbox 更新
   - `CLAUDE.md` 项目结构是否需要更新
   - SKILL.md 与实现是否一致

3. **变更集验证**:
   ```bash
   gitnexus_detect_changes  # 确认只改了预期范围
   gitnexus_impact          # 确认 d=1 依赖已更新
   ```

4. **交接给 Quality Protocol**:
   - 输出一份 summary：改了什么、加了什么、测了什么
   - 交给 quality-protocol 做 Phase A 穷尽扫描

### D4 退出条件

- [ ] 全量测试绿
- [ ] 集成点已验证
- [ ] 文档已更新
- [ ] gitnexus_detect_changes 确认范围正确
- [ ] 交接 summary 已输出

---

## 反模式（禁止）

| # | 反模式 | 为什么禁止 |
|---|--------|-----------|
| 1 | 不写 SKILL.md 直接写代码 | 项目约定。SKILL.md 是需求对齐的抓手 |
| 2 | 先写实现再补测试 | 测试会无意识地验证实现而非需求 |
| 3 | 实现"顺手"重构其他代码 | Feature 和 Refactor 是不同的协议，混做会模糊变更范围 |
| 4 | 新增全局状态/单例 | 增加测试难度，引入隐式依赖 |
| 5 | 不跑 gitnexus_impact 就改代码 | 不知道 blast radius = 不知道改对了没 |
| 6 | 改了 Pydantic model 不检查构造方 | v1 教训：field 约束变更会 break 调用方 |

---

## 与其他协议的关系

```
Feature Dev Protocol (本文件)
    │
    ├── D1-D4 完成后
    │   └──→ Quality Protocol (quality-protocol.md)
    │         Phase A 扫描新代码 + Phase B 对抗审计
    │
    ├── D2 发现需要重构
    │   └──→ Refactor Protocol (refactor-protocol.md)
    │
    └── D3 实现中发现 bug
        └──→ 直接修（小 bug）/ 提单给 Quality Protocol（大 bug）
```

---

## 测试规则

- 测试文件按模块命名：`test_{module_name}.py`
- Mock read/readline 必须用 `b""`
- IPython InteractiveShell 用 session-scoped fixture
- 单测文件不超过 30 秒，全量测试不超过 180 秒
- Agent 测试优先用 `AsyncMock` 模拟 IPC，不启动真实子进程
