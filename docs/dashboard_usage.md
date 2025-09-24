# tmux-agent Dashboard 使用指南

## 安装依赖
```bash
pip install .[dashboard]
```

## 启动服务
```bash
python3 -m tmux_agent.dashboard.cli \
  --db ~/.tmux_agent/state.db \
  --approval-dir ~/.tmux_agent/approvals \
  --host 0.0.0.0 \
  --port 8700
```

## 功能
- `/`：HTML 总览，显示所有 stage 状态，并为 `WAITING_APPROVAL` 阶段提供 `Approve` / `Reject` 按钮。
- `/api/overview`：返回 JSON 汇总与明细，可用于脚本消费。
- `/api/approvals/{host}/{pane}/{stage}`：POST JSON `{"decision": "approve"|"reject"}` 可编程审批。

## 停止服务
- 前台运行：`Ctrl+C`
- 后台运行：`kill <pid>`

## systemd 示例
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

部署后访问 `http://<nas>:8700/` 即可通过网页进行审批，无需手动写文件。
