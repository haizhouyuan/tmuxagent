# Orchestrator 下一阶段方案

## 目标概述

构建一个常驻、自动化的 Orchestrator Agent，实现：
- 持续监控所有 pane 的日志与状态，掌握完整上下文。
- 自动规划并注入 Codex 指令，落地多 pane 协作。
- 仅在需要人类决策/审批时通过微信机器人通知，支持双向确认。
- 提供可视化的任务状态、阻塞项与执行历史，便于审计与回溯。

## 现状回顾

- Orchestrator 通过 CLI 手动启动，缺少守护与自动恢复。
- 只截取日志尾部，未做归档、摘要或多轮上下文管理。
- 决策 prompt 单一，缺乏任务阶段、依赖、风险等结构化输入。
- 自动注入命令缺少安全/审批兜底，也无失败重试逻辑。
- 通知仍由多条管线直接推送，无法统一筛选确认类消息。
- 微信机器人为单向通道，无法收集确认信息并反馈给 orchestrator。
- Pane metadata 粗糙，仪表盘无法呈现阶段性进展与阻塞依赖。

## 分阶段方案

### 阶段 1：Orchestrator 守护与上下文增强 ✔️ 完成
- **服务守护**
  - 使用 systemd/supervisor 或在 Runner 中引入子进程管理，确保 orchestrator crash 后自动重启。
  - 增加心跳（写入 `metadata.orchestrator_heartbeat`），供仪表盘和告警检查。
- **日志归档与摘要**
  - 为 `.tmuxagent/logs/<session>.log` 引入滚动策略；长日志定期压缩。
  - 增设“摘要任务”：周期性调用 Codex Summarize prompt 把长日志汇总写入 `metadata.history_summaries`。
  - Prompt 中增加 `recent_summary`、`recent_actions` 字段，使决策具备记忆。
- **命令执行反馈**
  - 在 Runner 捕获命令执行的返回/错误并写入 metadata；orchestrator 根据反馈决定重试或转人工。

### 阶段 2：决策策略与安全控制 ✔️ 初版落地
- **阶段状态机**
  - 在 metadata 中维护 phase（PLANNING/EXECUTING/VERIFYING/BLOCKED/DONE）；不同 phase 选用不同 prompt & 行为策略。
  - 引入 `blockers` 字段，记录阻塞原因与重试次数；持续阻塞时强制提醒。
- **命令审核**
  - 扩展 LocalBus 命令 payload：包含工作目录、命令类型、风险等级。
  - 对非白名单操作使用审批模式，写入审批队列等待微信确认。
- **模板体系**
  - 维护多套 prompt（planning/execution/testing/summary）。在 orchestrator 配置中映射 phase→prompt。

### 阶段 3：通知与双向交互 ✔️ 初版落地
- **统一通知入口**
  - 在 `Notifier`/`notify_bridge` 前增加过滤器：只有 orchestrator 标记 `requires_confirmation` 或设置危急级别的事件推送到微信。
  - Runner/policy 常规通知改入日志或 8787 门户提示。
- **微信反馈回路**
  - 为 WeCom 机器人搭建接收端（HTTP 或 CLI 脚本），解析回复如 `/approve <task>`、`/deny` 等。
  - Orchestrator 在等待确认时订阅该指令，依据回复继续/终止动作。
- **门户升级**
  - 8787 页面增加“等待确认”“阻塞任务”板块；支持从手机提交 `/approve`、自定义指令。

### 阶段 4：多 pane 协作与任务板 ✔️ 完成
- **新交付**：
  - 8702 仪表盘新增 “Orchestrator” 专栏，与移动门户的概览保持同步，展示阶段、任务摘要、依赖、排队/待确认命令与心跳。
  - Orchestrator 配置引入 `tasks` 描述，自动补齐链式阶段、责任人、标签与依赖，并在 metadata 中透出。
  - 同一 session/pane 的调度加入冷却 + 队列机制，审计日志会记录 `queued`/`command` 事件，`queued_commands` 元数据实时反映排队状态。
- **审计增强**：所有注入、排队、确认事件写入 `.tmuxagent/logs/orchestrator-actions.jsonl`，供回放工具与治理使用。

### 阶段 5：可靠性与可观测性 ✔️ 完成
- **监控指标**
  - Prometheus `/metrics` 与 orchestrator exporter（可配置 `metrics_port`）输出命令状态、排队深度、待确认数量、决策耗时、错误次数，可直接接入 Grafana/Alertmanager。
  - `_record_error` 统一触发高优先级通知（severity=critical），并配合指标做自动告警。
- **回放 / 模拟模式**
  - 新增 `tmux-agent-orchestrator --dry-run` 保护模式，演练 prompt 而不落地命令。
  - 提供 `tmux-agent-orchestrator-replay --log …` 工具复盘 JSONL 审计日志，输出事件统计、最近命令与最大队列深度。
- **文档与运维**
  - 新增《docs/orchestrator_monitoring.md》，包含指标接入、dry-run、回放与运维巡检建议。

## 下一步重点
- 改善指标告警阈值与 Grafana Dashboard（参考 monitoring 文档），并纳入运维流程。
- 在生产环境执行全流程业务回归测试，验证 orchestrator 闭环能力。

## 依赖与风险提示
- Codex CLI 依赖稳定网络与代理；需集中管理配置与重试策略。
- Pane 日志需要统一权限与路径，确保 `pipe-pane` 全局打开。
- 审批/安全机制必须完善，防止自动命令误触生产。

## 推荐实施顺序
1. 阶段 1：保障 orchestrator 稳定运行与上下文足够。
2. 阶段 2：建立阶段/安全/审批机制。
3. 阶段 3：实现通知过滤 + 微信闭环。
4. 阶段 4：加强多 pane 协作及可视化呈现。
5. 阶段 5：完善监控、回放与运维手册。

依次推进后，即可逼近“后台智能 Agent 自动调度、仅关键节点通知人工”的目标。
