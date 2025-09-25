# 调度面板 AI 摘要与左栏输出增强

## 主要改动
- 默认使用 `claude-sonnet-4-20250514` 生成 pane 摘要，后端 `PaneSummarizer` 及 `scripts/summarize_pane.sh` 支持通过环境变量 `TMUX_AGENT_CLAUDE_MODEL` 覆盖。
- Dashboard API (`/api/panes*`, `/api/dashboard`, `/api/panes/{id}/tail`) 新增 `tail_excerpt/tail_preview` 字段，左栏 Pane 卡片直接展示最近 8 行输出，聚焦按钮可让右栏仅保留目标 pane。
- `/api/panes/{id}/summary` 采用全局 `PaneSummaryResponse`，前端缓存 AI 摘要并同步渲染在左栏（“AI总结”按钮点击后自动刷新）。
- 样式优化：左栏与右栏摘要区域统一使用等宽字体与滚动容器，便于快速浏览长日志片段。

## 使用指引
1. **生成 AI 摘要（CLI）**
   ```bash
   scripts/summarize_pane.sh            # 在 tmux pane 内执行，默认当前 pane
   scripts/summarize_pane.sh %3         # 指定 pane
   TMUX_AGENT_CLAUDE_MODEL=claude-sonnet-4-20250514 scripts/summarize_pane.sh --format json %10
   ```
   - 首次需运行一次 `claude` 完成订阅登录；脚本会去 ANSI、控制字符并截断 60 万字符以内。
2. **Dashboard 上生成摘要**
   - 左栏某个 pane 的 “AI总结” 按钮会调用 `/api/panes/{pane_id}/summary`，生成内容同时写入右栏卡片。
   - “聚焦”按钮可在右栏只保留该 pane，方便比对 AI 摘要与实时日志。
3. **后端接口要点**
   - `/api/panes/{pane_id}/tail`：除原始行列表外，还返回 `tail_excerpt`，供前端快速更新。
   - `/api/dashboard`：`projects[].sessions[].windows[].panes[]` 节点包含 `tail_excerpt` 与实时状态。

## 测试
- 自动化：`.venv/bin/python -m pytest`（共 19 项，全通过）。
- 手动验证建议：
  1. `lsof -ti:8701 | xargs -r kill` 清理端口；`.venv/bin/python -m tmux_agent.dashboard.cli --port 8701` 启动服务。
  2. 浏览器访问 `http://127.0.0.1:8701`，确认左栏显示 tail 片段、AI 摘要刷新、聚焦按钮生效。
  3. 在目标 pane 执行命令触发错误，再点 “AI总结” 检查 Claude 输出是否准确；必要时调整 `PaneStatusAnalyzer` 关键词或脚本参数。
