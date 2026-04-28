# Agent Nexus 全量代码评审报告

- 评审日期: 2026-04-23
- 评审范围: 当前仓库全量代码，重点覆盖本地未提交 Rust 变更（`ap-cli`、`ap-core`、`ap-evolution`、`ap-fetcher`、`ap-gateway`、`ap-runtime`）
- 评审方式: 仓库结构审阅 + 关键改动 diff 审阅 + 协议/调用链交叉核对 + 独立 reviewer 复核
- 说明: 已补跑本地自动化测试。Python 侧使用 `.venv/bin/pytest -q`，结果为 `2708 passed, 2 warnings in 32.49s`；Rust 侧使用 rustup toolchain 中的 `cargo test`，相关 workspace 测试均通过。以下结论基于静态审阅、协议一致性分析与本地测试结果综合得出。

## 结论摘要

本轮评审发现 4 个需要优先处理的问题，其中 2 个高优先级、2 个中优先级。最高风险集中在:

1. CLI 与 IPC 协议兼容性被破坏，`runtime exec` 多参数场景可能把 JSON 数组字面量发给 agent。
2. `install --type` 的参数校验时机错误，失败时会留下半安装状态。
3. Gateway 延迟激活链路把激活失败缓存成“成功但空工具列表”，会掩盖真实故障并阻断重试。

## 详细发现

### 1. [高] `runtime exec` 多参数发送格式破坏现有 IPC 协议

- 位置:
  - `crates/ap-cli/src/commands/runtime.rs:129-136`
  - `src/agent_nexus/models/ipc.py:42-48`
  - `src/agent_nexus/platform/orchestration/ipc.py` 中现有协议仍将任务内容视为普通字符串
- 问题:
  - 现在 Rust CLI 在参数多于 1 个时，会把参数序列化成 JSON 数组字符串再通过 `send_task()` 发出。
  - 但当前平台/Agent IPC 合约仍然只有平铺的 `content: str`，接收侧没有与之配套的 JSON 数组解析协议。
- 风险:
  - 之前收到的是 `"fix login flow"`，现在会收到 `"[\"fix\",\"login\",\"flow\"]"`。
  - 这会直接改变 agent 的任务输入语义，导致多参数调用行为回归，尤其影响把 CLI 参数直接当 prompt/task text 使用的 agent。
- 建议:
  - 如果目标是兼容旧协议，恢复为空格拼接的纯文本行为。
  - 如果确实需要结构化参数，必须同步扩展 IPC schema，并在接收端显式解析。
  - 增加一个回归测试，覆盖 `runtime exec agent arg1 arg2 arg3` 的端到端任务内容断言。

### 2. [高] `install --type` 在安装完成后才校验，失败时会留下脏安装目录

- 位置: `crates/ap-cli/src/commands/install.rs:33-72`
- 问题:
  - `agent_type_str` 的合法性校验发生在 `installer.install(...)` 之后。
  - 如果用户传入非法类型，命令会在 clone/安装完成后报错退出，但不会回滚已落盘的 `.agents/<agent>` 目录。
- 风险:
  - 磁盘状态和 `lockfile.json` 状态不一致，形成“已安装但未登记”的半安装状态。
  - 后续重试、升级、卸载都会遇到歧义，排障成本高。
- 建议:
  - 在任何 I/O 前先解析并校验 `agent_type_str`。
  - 或者在后续步骤失败时补充清理逻辑，保证 install 是原子式成功/失败。
  - 增加测试覆盖“非法 `--type` 不应留下安装残留”。

### 3. [中] 延迟激活把 `list_tools()` 失败缓存成空列表，导致错误被永久吞掉

- 位置: `crates/ap-gateway/src/deferred_registry.rs:151-169`
- 问题:
  - `activate()` 里对 `client.list_tools().await` 的失败分支不再返回 `RegistryError::ActivationFailed(...)`，而是记录日志后返回空 `Vec`。
  - 由于这个结果被放进 `tokio::sync::OnceCell`，首次失败会把“空工具列表”缓存成稳定状态。
- 风险:
  - 瞬时激活失败会被错误地表现为“激活成功但没有工具”。
  - 后续调用无法自动重试 `list_tools()`，除非显式 `deactivate()`。
  - 故障表象从可诊断的 activation error 退化为静默能力缺失，尤其难排查。
- 建议:
  - 不要把失败折叠成空列表；应保留显式错误返回。
  - 如果 `OnceCell` 不方便承载错误，可缓存 `Result<Arc<Vec<ToolInfo>>, RegistryError>`，或在失败时不写入 cell。
  - 增加测试覆盖 `list_tools()` 首次失败后的重试行为。

### 4. [中] `SourceEntry::validate()` 的 URL 判定规则过松且不完整

- 位置: `crates/ap-core/src/models/distribution.rs:46-70`
- 问题:
  - 现在的校验逻辑会放过很多并非合法仓库地址的字符串，只要它们不含 `://` 且不以 `-` 开头。
  - 同时它又可能误拒一些合法但未列入白名单的 Git URL 形式。
- 风险:
  - 无效 source 会在更晚的 clone/install 阶段才失败，降低错误定位质量。
  - 新增 URL 形式时，验证层和安装层可能继续分叉。
- 建议:
  - 用更明确的 URL/路径解析策略替代字符串启发式判断。
  - 至少补齐测试矩阵:
    - 应接受: `https://...`、`ssh://...`、`git@...`、绝对路径
    - 应拒绝: 空串、明显非法文本、危险前缀、格式损坏输入

## 正向观察

- `ap-evolution/src/engine.rs` 中对 health mutex 的持有范围进行了缩短，方向是对的，能减少锁跨 I/O 的风险。
- `ap-core/src/orchestration/task_graph.rs` 将 `is_empty()` 从吞错布尔值改为显式 `Result<bool, _>`，错误语义更清晰。
- `ap-fetcher/src/uv_bridge.rs` 在找不到 `uv` 时直接返回错误，比“静默回退后再失败”更容易诊断。

## 建议修复顺序

1. 先修复 `runtime exec` 的 IPC 兼容性问题。
2. 再修复 `install --type` 的校验时机，避免继续制造半安装状态。
3. 随后恢复 deferred registry 的激活失败语义，避免 Gateway 故障被吞。
4. 最后收紧 `SourceEntry` 校验并补测试。

## 补充说明

- 这份报告优先聚焦行为回归、状态一致性和协议兼容性风险，没有把纯注释/文档调整列为问题。
- 若需要，我可以下一步直接按这份报告把上述 4 个问题逐项修掉，并补上对应测试。
