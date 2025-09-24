# tmux-agent Dashboard 使用说明

## 启动
```bash
python3 -m tmux_agent.dashboard.cli \
  --db ~/.tmux_agent/state.db \
  --host 0.0.0.0 \
  --port 8700
```

## 站点访问
- HTML: http://<NAS>:8700/
- API:  http://<NAS>:8700/api/overview

## 停止
- 前台运行: `Ctrl+C`
- 后台运行: `kill <pid>`

