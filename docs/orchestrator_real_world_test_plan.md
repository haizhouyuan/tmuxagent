# Orchestrator Real-World Validation Playbook

## Scope
验证升级后的 Orchestrator（阶段 1-6）在真实多代理、多仓库环境中的表现；覆盖命令调度反馈、卡顿自愈、决策辅助、需求分解与指标告警全链路。

## 环境基线
- 主机：Ubuntu 22.04 / macOS 13，8C/16G 以上。
- 代码仓：`~/projects/tmuxagent-worktrees/fleetai`（主服务），`~/projects/testprojectfortmuxagent`（天气机器人验证仓）。
- Python 3.11；虚拟环境：`.venv`。
- Prometheus/Grafana 可选（本地主机即可）。
- 企业微信或 Slack 测试机器人，用于通知闭环。

## 环境准备（务必先执行）
1. **统一配置 `.env`**（建议放在 `~/.env`）：
   ```bash
   HTTP_PROXY=http://127.0.0.1:7890
   HTTPS_PROXY=http://127.0.0.1:7890
   ALL_PROXY=socks5://127.0.0.1:7890  # 如使用 SOCKS5 代理
   NO_PROXY=localhost,127.0.0.1,::1,.local,.lan

   OPENAI_API_KEY=sk-xxx_your_key
   CODEX_MODEL=gpt-5-codex
   CODEX_TIMEOUT_SECONDS=120

   WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=替换为真实Key
   # 如需企业微信应用消息，请设置：
   WECOM_CORP_ID=wwxxxx
   WECOM_AGENT_ID=1000002
   WECOM_APP_SECRET=xxxx
   ```

2. **加载环境并刷新 tmux server**：
   ```bash
   set -a && source ~/.env && set +a
   tmux kill-server 2>/dev/null || true
   ```

3. **确保 `codex` 可执行**（真实或 Fake 版本）：
   ```bash
   mkdir -p ~/.local/bin
   cp /path/to/codex ~/.local/bin/codex && chmod +x ~/.local/bin/codex
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   which codex && codex --version
   ```
   > 若暂时没有真实 CLI，可在 `.tmuxagent/bin/` 放置 FakeCodex（只需输出 `__TMUXAGENT_RESULT ... 0`），用于打通链路。

4. **Webhook 自检**：
   ```bash
   curl -sS -X POST "$WECOM_WEBHOOK" \
     -H 'Content-Type: application/json' \
     -d '{"msgtype":"text","text":{"content":"[Orchestrator] webhook ready"}}'
   ```

5. **运行诊断脚本**（建议保存为 `scripts/diagnose_orchestrator.sh`）：
   ```bash
   ./scripts/diagnose_orchestrator.sh
   ```
   该脚本会校验代理、API Key、Webhook、codex 与 `/metrics` 端口；全部通过后再进入正式测试。

## 预备步骤
1. **同步代码**
   ```bash
   cd ~/projects/tmuxagent-worktrees/fleetai
   git pull
   .venv/bin/python -m pip install -e .[dev]
   ```
2. **生成配置**
   - `tmux-agent init`（如未执行）。
   - 检查 `agent-config.yaml`、`.tmuxagent/orchestrator.toml`，新增：
     ```toml
     stall_timeout_seconds = 120
     stall_retries_before_notify = 2
     failure_alert_threshold = 3
     ```
   - 在目标 session 元数据中设置 `requirements_doc="docs/weather_bot_end_to_end_plan.md"`。
3. **启动基础服务**
   ```bash
   .venv/bin/tmux-agent run &
   .venv/bin/tmux-agent-orchestrator --config agent-config.yaml --metrics-port 9108 &
   .venv/bin/python -m tmux_agent.dashboard.cli --db ~/.tmux_agent/state.db &
   ```
4. **Prometheus 采集**（可选）
   - `prometheus.yml` 中加入：
     ```yaml
     scrape_configs:
       - job_name: tmux_orchestrator
         static_configs:
           - targets: ['localhost:9108']
       - job_name: tmux_dashboard
         static_configs:
           - targets: ['localhost:8702']
     ```
   - 启动 `prometheus --config.file ./prometheus.yml`。

## 测试任务流
### 场景 A：命令执行反馈
1. 打开气象仓 tmux pane（`weather/bot`）。
2. 从 orchestrator 触发“创建天气 API”命令：观察 LocalBus 中命令被注入并附带 `__TMUXAGENT_RESULT ...` 结尾。
3. 刻意制造失败（例如 `python scripts/weather_api_stub.py --bad-flag`），确认：
   - `metadata.last_command_result.exit_code` 记录失败。
   - `metrics` 暴露 `orchestrator_command_failures_total{branch="weather/bot"}` > 0。
   - Dashboard Orchestrator 专栏出现失败提示。

### 场景 B：卡顿检测 & 自愈
1. 在气象 pane 执行长时间阻塞命令（`sleep 600`）。
2. 通过 orchestrator 注入命令后不终止。
3. 等待 `stall_timeout_seconds`，验证：
   - 元数据 `orchestrator_stall_count >= 1`，`blockers` 提示卡顿命令。
   - 企业微信收到“命令卡顿”通知。
   - Orchestrator 自动解除 session 冷却并重新排队命令。

### 场景 C：决策辅助推荐
1. 造成单个 blocker（例如缺少 API key）。
2. 等待 orchestrator 循环一次，检查：
   - `metadata.next_actions` 含有 “解除 blocker” 建议。
   - Dashboard “下一步建议” 卡片更新。
   - 通知渠道收到“下一步建议”提示（优先级 high）。

### 场景 D：需求文档分解
1. 在 metadata 设置 `requirements_doc` 后，观察 `task_decomposition` 是否生成步骤。
2. 核对步骤是否包含：
   - 创建 stub 脚本。
   - `uvicorn` 启动命令。
   - UI 组件。
3. 结合 `task_decomposition` 逐条执行命令，验证 orchestrator 能根据步骤自动推进。

### 场景 E：连续失败告警
1. 构造三次失败命令（例如运行不存在的 pytest 测试）。
2. 确认：
   - `orchestrator_failure_streak` 达到阈值后通知被触发。
   - 自动停止继续注入该命令并写入 blockers。

### 场景 F：指标与审计
1. 访问 `http://localhost:9108/metrics`，确认新增指标：
   - `orchestrator_command_success_total` / `orchestrator_command_failures_total`
   - `orchestrator_pending_confirmation_total`
   - `orchestrator_decision_latency_seconds`
2. 使用 `tmux-agent-orchestrator-replay --log .tmuxagent/logs/orchestrator-actions.jsonl` 核对命令、排队、确认事件完整性。
3. Grafana 仪表：展示成功率、失败率、队列深度、卡顿次数。

## 回归测试
1. 运行自动化：`.venv/bin/python -m pytest`。
2. 若 Prometheus/Grafana 可用，导出 10 分钟数据验证：无连续卡顿报警、失败率 <10%。
3. 汇总成果到 `docs/orchestrator_ai_development_test_report.md`（附主要指标截图）。

## 验收标准
- 所有场景通过且无高优先级 blocker。
- Prometheus/Grafana 指标显示成功率>90%、卡顿已恢复。
- 企业微信/Slack 通知链路与批准机制运作正常。
- Replay 工具能输出完整命令历史，未出现缺失条目。
- 全量 pytest 通过。
