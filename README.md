# tmux-agent

A lightweight orchestration agent for tmux-based AI development environments. The agent watches pane output, interprets structured `### SENTRY {json}` markers, evaluates YAML-defined policies, and performs follow-up actions with human-in-the-loop approvals.

## Features
- Configurable tmux pane discovery and incremental log capture.
- YAML policy engine with stage triggers, retries, approvals, and escalation hooks.
- Persistent state backed by SQLite for restart resilience.
- File-based and webhook-driven approval flow with signed tokens.
- Notification adapters (stdout 默认，可选 Server酱、WeCom webhook、WeCom 应用消息、本地门户总线)。
- Worktree-aware agent CLI：一键创建 Git worktree、启动专用 tmux 会话并注入 Claude/Codex 模板。
- Orchestrator 支持多 pane 调度、排队/依赖管理，并输出 Prometheus 指标、Dry-Run 与 JSONL 回放工具（详见 `docs/orchestrator_monitoring.md`）。

### 通知通道
- `notify: "stdout"`：输出至 STDOUT，适合本地调试。
- `notify: "wecom"`：使用企业微信自建群机器人 Webhook，需设置 `WECOM_WEBHOOK`。
- `notify: "wecom_app"`：推送企业微信应用消息，需设置 `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_APP_SECRET`，可选 `WECOM_TOUSER`（默认 `@all`）、`WECOM_TOPARTY`、`WECOM_TOTAG`。
- `notify: "local_bus"`：写入本地 JSONL 总线，供移动端门户实时读取。
- 可以使用逗号组合多种通道，例如 `notify: "wecom,local_bus"` 同时推送企业微信与本地门户。

## Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dashboard]

# Copy and adjust configuration
cp examples/hosts.example.yaml hosts.yaml
cp examples/policy.example.yaml policy.yaml

.venv/bin/python -m tmux_agent.main --config hosts.yaml --policy policy.yaml
```

Run `.venv/bin/python -m tmux_agent.main --help` for CLI options.

### Dashboard (默认 8702)
```bash
.venv/bin/python -m tmux_agent.dashboard.cli \
  --db ~/.tmux_agent/state.db \
  --approval-dir ~/.tmux_agent/approvals \
  --host 0.0.0.0 \
  --port 8702
```

访问 `http://<nas>:8702/` 查看新的 “Agents” 分组；`/api/overview` / `/api/dashboard` 提供 JSON 数据。处于 `WAITING_APPROVAL` 状态的阶段会在表格中显示 “Approve / Reject” 按钮。

### 移动端控制台 (手机访问)
1. 将通知通道设为 `notify: "wecom,local_bus"` 或 `notify: "local_bus"`，即可同步推送至企业微信群与本地门户。
2. 启动调度器：`source .env && .venv/bin/python -m tmux_agent.main --config agent-config.yaml --policy policy.yaml`。
3. 在终端启动门户服务：
   ```bash
   .venv/bin/python scripts/run_portal.py \
     --config agent-config.yaml \
     --host 0.0.0.0 \
     --port 8787 \
     [--token <可选口令>]
   ```
4. 手机浏览器访问 `http://<nas>:8787/`，顶部实时显示通知，底部输入框可向指定 tmux 会话发送指令。若设置 `--token`，需在请求时携带同名 `X-Auth-Token` 头部。

门户使用 `~/.tmux_agent/bus/` 保存通知与命令 JSONL，支持历史回放与离线分析。

### 企业微信同步
- 若需要将门户中的通知同步至企业微信群，可仅将配置文件中的 `notify` 设为 `local_bus`。
- 启动通知中继：
  ```bash
  .venv/bin/python -m tmux_agent.notify_bridge \
    --config agent-config.yaml \
    --channel wecom \
    --interval 2
  ```
  中继会持续监听本地总线的新通知，并调用 WeCom Webhook/应用消息发送到群聊。
- 也可通过 `--channel wecom,stdout` 等形式一次推送多个通道；使用 `--once` 选项可单次同步历史通知。

### Agent CLI：Worktree + tmux 会话
```bash
# 初始化 `.tmuxagent/fleet.toml` 与模板目录
.venv/bin/tmux-agent init

# 查看模板（默认读取 .tmuxagent/templates/*.md）
.venv/bin/tmux-agent templates

# 启动/复用代理：创建 worktree、拉起 tmux 会话并注入模板/任务描述
.venv/bin/tmux-agent spawn feature/login --template qa-template --task "验证登录流程"

# 查看活跃代理登记（对应 dashboard “Agents”）
.venv/bin/tmux-agent sessions
```

## Testing
```bash
pip install -e .[dev]
pytest
```

详见 `docs/testing.md` 获取更完整的手动验证场景。
