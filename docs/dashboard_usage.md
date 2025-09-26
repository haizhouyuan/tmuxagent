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
  --port 8702
```

## 功能
- `/`：HTML 总览，显示所有 stage 状态，并为 `WAITING_APPROVAL` 阶段提供 `Approve` / `Reject` 按钮。
- `/api/overview`：返回 JSON 汇总与明细，可用于脚本消费。
- `/api/approvals/{host}/{pane}/{stage}`：POST JSON `{"decision": "approve"|"reject"}` 可编程审批。
- “Agents” 分组：当 tmux 会话名以 `agent-` 开头、且通过 `tmux-agent spawn` 记录到 `agent_sessions` 表时，会自动归档到左栏新分组，显示对应分支、模板、模型和任务描述。

## 搭配 tmux-agent CLI
1. `tmux-agent init`：在仓库下创建 `.tmuxagent/fleet.toml` 与模板目录。
2. `tmux-agent spawn feature/foo --template qa-template --task "检查日志"`：
   - 根据分支创建/复用 worktree；
   - 启动前缀 `agent-` 的 tmux 会话并导出 `AI_AGENT_*` 环境变量；
   - 将模板 prompt 与任务描述写入面板，同时登记到 SQLite `agent_sessions` 表。
3. Dashboard 左侧 “Agents” 即可看到该会话，pane 卡片也会展示模板/模型/任务标签。

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
  --port 8702
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

部署后访问 `http://<nas>:8702/` 即可通过网页进行审批，无需手动写文件（工作树环境默认使用 8702 端口，避免与主仓部署冲突）。
