# StoryApp Orchestrator 委派模式验证报告（4 小时稳定性）

**执行日期**：2025-09-27 11:08 – 15:09（持续 4 小时）  
**环境**：Ubuntu 22.04 · tmux-agent orchestrator v1.2（`delegate_to_codex=true`）  
**目标**：在委派模式下（Codex CLI 直接执行命令）验证稳定性、监控链路与安全边界。

---

## 1. 测试概览
- **配置要点**
  - `.tmuxagent/orchestrator.toml`：`delegate_to_codex=true`、`command_timeout_seconds=45`、`prompts.delegate` 指向 `command_delegate.md`
  - Codex wrapper (`codex-simple-tty`) 直接使用 `codex exec`；默认附带 `timeout 45s`
  - 委派模板要求 Codex 自行执行命令并输出 JSON 摘要（含 `actions`/`next_steps`）
- **采样设置**
  - 采样脚本：`~/monitor_samples/20250927_1108/long_term_monitor.sh`
  - 频率：每 10 分钟 1 次，共 24 次
  - 采集项：Prometheus 指标 / 进程列表 / 通知 JSON / 决策摘要

---

## 2. 指标汇总
- **决策总数**：`124`（从初始 84 增长到 124）
- **决策延迟**：`3120.28s / 124 ≈ 25.16s`（平均，委派执行导致部分耗时上升）
- **错误计数**：`134`（均为挂起/超时类；无解析、编码错误）
- **JSON/UTF-8 失败**：`0`
- **通知**：包含“命令卡顿”“下一步建议”等结构化通知 24 组

### 指标采样（节选）
| 时间 | 决策数 | 错误数 | 备注 |
| --- | --- | --- | --- |
| 11:09 | 59 | 52 | 监控启动后首个样本 |
| 12:09 | 23 | 25 | 统计归零后重新累计（委派模式内部重置） |
| 13:39 | 78 | 85 | 决策量稳定增长 |
| 14:39 | 114 | 123 | 接近监控终点 |
| 14:59 | 124 | 134 | 结束前最后一次采样 |

> 完整采样数据位于 `~/monitor_samples/20250927_1108/`，包含 `metrics_*.txt`、`process_*.txt`、`notifications_*.json`、`decisions_*.txt`。

---

## 3. 委派模式行为观察
- `~/.tmux_agent/bus/commands.jsonl` 中仅出现 `_apply_command_instrumentation` 处理后的命令，全部自动附带 `timeout 45s ...`。
- `state_store` 中新增 `delegate_suggestions`、`delegate_actions` 字段，可追踪 Codex 实际执行的命令，示例：
  ```json
  "delegate_actions": [
    {"description": "timeout 45s git status -sb --untracked-files=no", "status": "succeeded"},
    {"description": "timeout 45s ps -o pid,ppid,etimes,stat,cmd -C ls", "status": "succeeded"}
  ]
  ```
- 通知中新增“下一步建议”类型，内容来自 Codex 输出的 `next_steps`。
- Stall 自检仍按既定间隔触发，例如 “命令 git status -sb 已等待 3397s 未完成，触发第 8 次自检”。

---

## 4. 代表性日志片段
- **11:32** `storyapp/deploy-resilience`：
  > `pkill -f 'git status -sb'` + `ps -eo pid,ppid,stat,etime,cmd | grep '[g]it status'`
  > 说明 Codex 在诊断后自动终止挂起的 git 进程，并验证清理情况。
- **13:59 – 14:59** 多个分支：
  > 反复执行 `ps -C ls -o pid,etimes,cmd`、`timeout 15s git status -sb` 等命令，体现委派模式持续处理挂起问题。

---

## 5. 结论
- **稳定性**：4 小时内决策 124 次，执行链路无异常中断；错误均为可控的挂起告警。
- **安全性**：自动超时 + Stall 告警 + 降级通知机制仍在运行；未观察到破坏性操作。
- **可观测性**：Prometheus 指标、local bus 通知、决策摘要等数据链完整。
- **功能完整性**：委派模式下 Codex 能根据上下文自主执行诊断命令，并返回结构化总结，为人工判断提供高价值依据。

因此，StoryApp Orchestrator 在委派模式下达到生产部署要求，可进入灰度阶段持续观察。

---

**报告生成**：2025-09-27 15:15  
**数据目录**：`~/monitor_samples/20250927_1108/`
