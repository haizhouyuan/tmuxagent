# Orchestrator Real-World Validation Playbook (Updated)

## 目标
在真实多代理、多仓库环境下验证阶段 1~6 能力：命令调度反馈、卡顿自愈、AI 决策辅助、需求分解、监控告警与通知闭环。

## 一、环境准备
1. **同步代码并安装依赖**
   ```bash
   cd ~/projects/tmuxagent-worktrees/fleetai
   git pull
   .venv/bin/python -m pip install -e .[dev]
   ```
2. **统一配置 `~/.env`**（示例）
   ```bash
   HTTP_PROXY=http://127.0.0.1:7890
   HTTPS_PROXY=http://127.0.0.1:7890
   ALL_PROXY=socks5://127.0.0.1:7890
   NO_PROXY=localhost,127.0.0.1,::1,.local,.lan

   OPENAI_API_KEY=sk-your-key
   CODEX_TIMEOUT_SECONDS=120

   # Webhook 或企业应用必选其一
   WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your-key
   # WECOM_CORP_ID=wwxxx
   # WECOM_AGENT_ID=1000002
   # WECOM_APP_SECRET=xxx
   ```
3. **载入环境并刷新 tmux server**
   ```bash
   set -a && source ~/.env && set +a
   tmux kill-server 2>/dev/null || true
   ```
4. **准备 codex CLI**（真实或 Fake）
   ```bash
   mkdir -p ~/.local/bin
   cp /path/to/codex ~/.local/bin/codex && chmod +x ~/.local/bin/codex
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   which codex && codex --version
   ```
5. **诊断脚本一键自检**
   ```bash
   ./scripts/diagnose_orchestrator.sh
   ```
   通过后再进行后续测试。

## 二、基础服务启动
1. **生成/检查配置**
   ```bash
   tmux-agent init  # 首次使用
   ```
   - `agent-config.yaml`: 指定工作树与 tmux 配置
   - `.tmuxagent/orchestrator.toml`: 更新重点项
     ```toml
     stall_timeout_seconds = 120
     stall_retries_before_notify = 2
     failure_alert_threshold = 3

     [codex]
     bin = ". ~/.proxy-on.sh && proxychains4 -q codex"
     extra_args = ["--dangerously-bypass-approvals-and-sandbox"]
     ```
2. **启动服务栈**
   ```bash
   .venv/bin/tmux-agent run &
   .venv/bin/tmux-agent-orchestrator --config agent-config.yaml --metrics-port 9108 &
   .venv/bin/python -m tmux_agent.dashboard.cli --db ~/.tmux_agent/state.db &
   ```
3. **Prometheus（可选）**
   ```yaml
   scrape_configs:
     - job_name: tmux_orchestrator
       static_configs:
         - targets: ['localhost:9108']
     - job_name: tmux_dashboard
       static_configs:
         - targets: ['localhost:8702']
   ```

## 三、测试场景
### 场景 A：命令执行反馈
1. 在 `weather/bot` pane 注入成功/失败命令
2. 验证 `last_command_result`、Prometheus 计数、Dashboard 反馈
3. 检查 `.tmuxagent/logs/orchestrator-actions.jsonl`

### 场景 B：卡顿检测与自愈
1. 执行 `sleep 600`
2. 等待超时后，确认：
   - `orchestrator_stall_count` 增加
   - WeCom 产生“命令卡顿”通知
   - 冷却解除后命令能继续执行

### 场景 C：决策辅助
1. 人为设置 blocker（例如缺少 API Key ）
2. 观察 `metadata.next_actions` 与“下一步建议”通知
3. Dashboard 推荐卡片需更新

### 场景 D：需求文档分解
1. 会话 metadata 设置 `requirements_doc="docs/weather_bot_end_to_end_plan.md"`
2. 验证生成 `task_decomposition`
3. 按步骤驱动 orchestrator 执行命令

### 场景 E：连续失败告警
1. 连续注入失败命令 ≥ 阈值
2. 验证 `orchestrator_failure_streak`、通知与阻断行为

### 场景 F：指标与审计
1. `curl http://localhost:9108/metrics`
2. Prometheus/Grafana 观察成功率、失败率、队列深度等
3. `tmux-agent-orchestrator-replay` 复盘 JSONL 日志

### 场景 G：通知降级验证
1. 临时移除 `WECOM_WEBHOOK`
2. 确认 orchestrator 不会异常退出，日志出现降级提示
3. 还原变量后通知恢复正常

## 四、回归测试
1. `.venv/bin/python -m pytest`
2. 导出 Prometheus/Grafana 10 分钟数据（如使用）
3. 汇总结果至 `docs/orchestrator_real_world_test_report.md`

## 五、验收标准
- 七大场景全部通过，指标无红色告警
- 通知链路稳定，降级策略正确
- Replay 审计完整
- 全量 pytest 0 失败
