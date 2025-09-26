# Orchestrator 监控与演练指南

## 指标导出

Orchestrator 服务内置 Prometheus 指标，覆盖命令注入、排队、待确认、决策耗时与错误。

- 在配置文件 `.tmuxagent/orchestrator.toml` 中指定：

```toml
metrics_port = 9108
metrics_host = "0.0.0.0"  # 可选，默认 0.0.0.0
```

- 或通过 CLI 参数覆盖：

```bash
.tmuxagent/venv/bin/tmux-agent-orchestrator \
  --config agent-config.yaml \
  --orchestrator-config .tmuxagent/orchestrator.toml \
  --metrics-port 9108
```

- 指标样例：
  - `orchestrator_commands_total{result="dispatched|queued|dry_run"}`
  - `orchestrator_queue_size{branch="storyapp/feature-x"}`
  - `orchestrator_pending_confirmation_total{branch="storyapp/feature-x"}`
  - `orchestrator_decision_latency_seconds_bucket`
  - `orchestrator_decision_errors_total{branch="..."}`

- 访问 `http://<host>:9108/` 查看 Prometheus 原始指标，或在 Dashboard 服务 `http://<host>:8702/metrics` 汇总部署侧数据。

### 告警建议

- **队列堆积**：`orchestrator_queue_size > 3` 持续 5 分钟。
- **待确认过多**：`orchestrator_pending_confirmation_total > 0` 持续 10 分钟。
- **无心跳**：依赖 metadata `orchestrator_heartbeat`，可在 Grafana 录入 `max(time() - heartbeat)` 阈值。
- **决策错误**：`increase(orchestrator_decision_errors_total[5m]) > 0`。

将上述指标接入中心化 Prometheus，再由 Alertmanager/钉钉/企业微信等下发。

## Dry-Run 模式

在生产前可使用 Dry-Run 模式模拟 orchestrator 决策：

```bash
.tmuxagent/venv/bin/tmux-agent-orchestrator \
  --config agent-config.yaml \
  --orchestrator-config .tmuxagent/orchestrator.toml \
  --dry-run
```

Dry-Run 下所有命令仅记录于审计日志，不会写入 LocalBus，也不会影响 tmux pane。

## 审计日志回放

所有 orchestrator 决策写入 `.tmuxagent/logs/orchestrator-actions.jsonl`。

使用内置回放工具快速统计：

```bash
.tmuxagent/venv/bin/tmux-agent-orchestrator-replay --log .tmuxagent/logs/orchestrator-actions.jsonl
.tmuxagent/venv/bin/tmux-agent-orchestrator-replay --branch storyapp/feature-x --limit 200
```

工具会输出事件计数、时间跨度、最后一次命令与最大队列深度，可在故障后快速复盘。

## Dashboard Orchestrator 专栏

- 8702 面板新增 “Orchestrator” 专栏，展示阶段计划、阻塞、待确认/排队命令、责任人、心跳。
- 移动门户 8787 仍保持同步视图，适合手机端审批。

## 运维建议

1. **指标采集**：在 Prometheus 中配置 `scrape_config`，抓取 orchestrator `metrics_port` 与 dashboard `/metrics`。
2. **日志归档**：`.tmuxagent/logs/orchestrator-actions.jsonl` 建议接入 Loki/S3，保留不少于 30 天。
3. **日常巡检**：每日检查 Grafana 队列深度、Pending Confirmation，心跳需在 60 秒内更新。
4. **演练流程**：
   - 先以 `--dry-run` 重放生产策略，确认排队/审批逻辑。
   - 使用 `tmux-agent-orchestrator-replay --limit` 对比 Dry-Run 与生产行为。
   - 更新 `tasks` 配置后执行 `pytest tests/test_orchestrator.py::test_orchestrator_applies_task_plan` 做单测回归。

按上述流程即可完成 Stage 5 的监控告警与回放演练要求。
