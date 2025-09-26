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

### 阶段 1：Orchestrator 守护与上下文增强
- **服务守护**
  - 使用 systemd/supervisor 或在 Runner 中引入子进程管理，确保 orchestrator crash 后自动重启。
  - 增加心跳（写入 `metadata.orchestrator_heartbeat`），供仪表盘和告警检查。
- **日志归档与摘要**
  - 为 `.tmuxagent/logs/<session>.log` 引入滚动策略；长日志定期压缩。
  - 增设“摘要任务”：周期性调用 Codex Summarize prompt 把长日志汇总写入 `metadata.history_summaries`。
  - Prompt 中增加 `recent_summary`、`recent_actions` 字段，使决策具备记忆。
- **命令执行反馈**
  - 在 Runner 捕获命令执行的返回/错误并写入 metadata；orchestrator 根据反馈决定重试或转人工。

### 阶段 2：决策策略与安全控制
- **阶段状态机**
  - 在 metadata 中维护 phase（PLANNING/EXECUTING/VERIFYING/BLOCKED/DONE）；不同 phase 选用不同 prompt & 行为策略。
  - 引入 `blockers` 字段，记录阻塞原因与重试次数；持续阻塞时强制提醒。
- **命令审核**
  - 扩展 LocalBus 命令 payload：包含工作目录、命令类型、风险等级。
  - 对非白名单操作使用审批模式，写入审批队列等待微信确认。
- **模板体系**
  - 维护多套 prompt（planning/execution/testing/summary）。在 orchestrator 配置中映射 phase→prompt。

### 阶段 3：通知与双向交互
- **统一通知入口**
  - 在 `Notifier`/`notify_bridge` 前增加过滤器：只有 orchestrator 标记 `requires_confirmation` 或设置危急级别的事件推送到微信。
  - Runner/policy 常规通知改入日志或 8787 门户提示。
- **微信反馈回路**
  - 为 WeCom 机器人搭建接收端（HTTP 或 CLI 脚本），解析回复如 `/approve <task>`、`/deny` 等。
  - Orchestrator 在等待确认时订阅该指令，依据回复继续/终止动作。
- **门户升级**
  - 8787 页面增加“等待确认”“阻塞任务”板块；支持从手机提交 `/approve`、自定义指令。

### 阶段 4：多 pane 协作与任务板
- **任务依赖图**
  - metadata 中记录 `depends_on`，orchestrator 在上游未完成时仅汇总状态，不执行命令。
  - 实现 orchestrator 级别的计划：如 orchestrator → CI pane → Deploy pane 的链式动作。
- **仪表盘增强**
  - 在 8702 仪表盘增加 “Orchestrator” 页，展示 phase、summary、阻塞等信息；支持时间线、历史命令追踪。
  - 建立审计日志：记录 orchestrator 每次注入的指令、确认情况。
- **冲突管理**
  - 加入锁/队列，避免多个决策在同一 pane 并发执行。

### 阶段 5：可靠性与可观测性
- **监控指标**
  - 输出 Prometheus/Grafana 指标：命令数量、失败率、冷却次数、待确认数量等。
  - 异常（如 codex 失败、长时间无心跳）触发高优先级告警。
- **回放 / 模拟模式**
  - 提供 dry-run/回放功能，用历史日志测试 prompt 和策略。
- **文档与运维**
  - 编写 orchestrator 配置指南、微信回路配置说明、常见故障排查手册。

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
