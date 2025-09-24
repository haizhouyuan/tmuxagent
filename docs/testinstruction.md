# tmux-agent Dashboard 测试指引

下列步骤帮助你在 NAS 上完成从环境准备、自动化测试、tmux 实操到 Dashboard 审批验证的完整流程。

## 1. 环境准备
1. 登录 NAS，进入项目目录：
   ```bash
   cd ~/projects/tmuxagent
   git fetch origin
   git switch main
   git pull
   # 如需验证 PR，切换到对应分支，例如：
   git switch feature/dashboard-approvals
   ```
2. 建议使用虚拟环境：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. 安装依赖：
   ```bash
   pip install .[dashboard]
   pip install -e .[dev]
   ```
4. 创建状态与审批目录（如尚未存在）：
   ```bash
   mkdir -p ~/.tmux_agent
   mkdir -p ~/.tmux_agent/approvals
   ```
5. 准备配置：
   ```bash
   cp examples/hosts.example.yaml hosts.yaml
   cp examples/policy.example.yaml policy.yaml
   ```
   - 根据实际 tmux session、pane 命名修改 `hosts.yaml`。
   - 在 `policy.yaml` 配置 `require_approval` 的 stage 以便测试审批流程。

## 2. 单元与集成测试
```bash
PYTHONPATH=src pytest            # 全量测试
# 或仅跑 Dashboard 相关测试
PYTHONPATH=src pytest tests/test_dashboard.py
```

## 3. tmux 准备
1. 创建被监控的 tmux 会话：
   ```bash
   tmux new-session -d -s proj:test -n agent:demo-ci "cd ~/workspace/demo && bash"
   tmux select-pane -t proj:test:1.1 -T "demo:ci"
   ```
2. 在目标 pane 中运行 codex CLI 或脚本，确保输出会包含触发关键字（例如 `run lint`）。

## 4. 启动 tmux-agent
```bash
python -m tmux_agent.main --config hosts.yaml --policy policy.yaml
# 可加 --once 或 --dry-run 做快速验证
```

## 5. 启动 Dashboard
```bash
python -m tmux_agent.dashboard.cli \
  --db ~/.tmux_agent/state.db \
  --approval-dir ~/.tmux_agent/approvals \
  --host 0.0.0.0 \
  --port 8700
```
访问：
- HTML: `http://<NAS>:8700/`
- JSON: `http://<NAS>:8700/api/overview`

## 6. 审批流程验证
1. 通过 tmux 输出触发 `require_approval` 的 stage。
2. Dashboard 表格中 `WAITING_APPROVAL` 行会显示 `Approve / Reject` 按钮。
3. 点击按钮后：
   - 对应审批文件写入 `~/.tmux_agent/approvals/<host>/<pane>__<stage>.txt`；
   - agent 读取文件，继续执行或标记失败；
   - Dashboard 刷新显示最新状态（RUNNING/COMPLETED/FAILED）。
4. 如需脚本触发，可使用：
   ```bash
   curl -X POST http://<NAS>:8700/api/approvals/local/%1/build \
        -H "Content-Type: application/json" \
        -d '{"decision":"approve"}'
   ```

## 7. 数据与状态检查
- 数据库：
  ```bash
  sqlite3 ~/.tmux_agent/state.db 'SELECT host,pipeline,stage,status,retries,updated_at FROM stage_state;'
  ```
- 审批文件：`cat ~/.tmux_agent/approvals/<host>/<pane>__<stage>.txt`
- Dashboard JSON：`curl http://<NAS>:8700/api/overview | python3 -m json.tool`

## 8. systemd 部署（可选）
```ini
[Unit]
Description=tmux-agent dashboard
After=network.target

[Service]
Type=simple
User=nasuser
WorkingDirectory=/home/nasuser/projects/tmuxagent
ExecStart=/usr/bin/python3 -m tmux_agent.dashboard.cli \
  --db /home/nasuser/.tmux_agent/state.db \
  --approval-dir /home/nasuser/.tmux_agent/approvals \
  --host 0.0.0.0 \
  --port 8700
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

部署后执行：
```bash
sudo systemctl daemon-reload
sudo systemctl enable tmux-agent-dashboard
sudo systemctl start tmux-agent-dashboard
sudo systemctl status tmux-agent-dashboard
```

## 9. 收尾
- 停止 Dashboard：`Ctrl+C` 或 `kill <pid>`
- 停止 agent：`Ctrl+C` 或关闭 tmux pane
- 清理数据（如需）：
  ```bash
  rm -f ~/.tmux_agent/state.db
  rm -rf ~/.tmux_agent/approvals/*
  ```

完成上述流程后，即完成一次端到端测试：tmux pane → agent 策略 → SQLite 状态 → Dashboard 展示与审批。根据测试结果决定是否合并或部署。
