# tmux-agent Dashboard 部署指南

本指南帮助你在 NAS 上以服务形式运行 Dashboard，并开启基础访问控制。

## 1. 安装依赖
```bash
pip install .[dashboard]
```

## 2. 创建配置目录
```bash
mkdir -p ~/.tmux_agent
```

## 3. 启动命令（带 Basic Auth）
```bash
python3 -m tmux_agent.dashboard.cli \
  --db ~/.tmux_agent/state.db \
  --host 0.0.0.0 \
  --port 8700 \
  --username dashboard \
  --password "<strong-password>"
```

访问时使用 `dashboard:<strong-password>` 进行 Basic Auth。`/healthz` 依旧无认证，用于探活。

## 4. systemd 服务示例
创建 `/etc/systemd/system/tmux-agent-dashboard.service`：
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
  --host 0.0.0.0 \
  --port 8700 \
  --username dashboard \
  --password ${DASHBOARD_PASSWORD}
Environment=DASHBOARD_PASSWORD=change_me
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用与启动：
```bash
sudo systemctl daemon-reload
sudo systemctl enable tmux-agent-dashboard
sudo systemctl start tmux-agent-dashboard
```

查看状态：`sudo systemctl status tmux-agent-dashboard`

## 5. 访问控制建议
- 使用 NAS 防火墙限制来源 IP。
- 如需 HTTPS，可在反向代理（Nginx/Caddy）层配置证书。
- 定期轮换 `DASHBOARD_PASSWORD`。

## 6. 常见问题
| 问题 | 可能原因 | 解决方案 |
| ---- | -------- | -------- |
| 访问返回 401 | 未提供或密码错误 | 浏览器输入 `dashboard:<password>` |
| 端口占用 | 8700 已被占用 | 修改 `--port` 与 systemd 配置 |
| 页面无数据 | SQLite 路径错误或 agent 未写入 | 检查 `--db` 与 tmux-agent 进程 |

## 7. 升级流程
```bash
cd /home/nasuser/projects/tmuxagent
git pull
pip install .[dashboard]
sudo systemctl restart tmux-agent-dashboard
```
