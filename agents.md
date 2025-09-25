# 调度面板现状速记（2025-09-23）

- **默认模型升级**: Pane summarizer & `scripts/summarize_pane.sh` 默认改为 `claude-sonnet-4-20250514`，支持通过 `TMUX_AGENT_CLAUDE_MODEL` 环境变量覆盖。
- **左栏视图**: 调度控制板按项目 → session → window → pane 分层展示，每个 pane 卡片内直接嵌入最近 8 行输出摘要（去 ANSI），若已生成 AI 摘要会附加在下方；支持“聚焦”按钮让右栏只显示对应 pane。
- **API 扩展**: `/api/panes*` 与 `/api/dashboard` 返回 `tail_excerpt`/`tail_preview`，`/api/panes/{id}/tail` 同步附带片段，前端重用这些字段渲染。
- **AI 摘要联动**: `/api/panes/{id}/summary` 接口返回结构改为模块级模型 `PaneSummaryResponse`，左栏在缓存 summary 后即时刷新展示。
- **验证**: `.venv/bin/python -m pytest` 全量通过（19 项），Dashboard 本地跑在 `:8701`，确认左/右栏行为与聚焦逻辑正常。

## 后续观察
1. 真机联动 Claude/Codex 会话，观察 `tail_excerpt` 是否会因超长输出截断关键信息，必要时调大后端截断阈值或增加折叠交互。
2. 调整 `PaneStatusAnalyzer` 关键词与项目归类逻辑，确保 storyapp/points 以外的 session 能自动归档到合理类别。
3. 若要持久化 AI 摘要，可考虑在 SQLite 增加缓存表，避免频繁调用 Claude CLI。
