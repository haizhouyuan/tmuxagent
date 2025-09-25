# Dashboard 优化开发说明

## 变更概览
- 主控台首页替换原 stage 表格为“调度控制板”，树状展示 session/window/pane，附带状态徽章与一句话总结。
- 后端新增 `PaneStatusAnalyzer`，统一解析 Codex/Claude CLI 输出，判定 `RUNNING / WAITING_INPUT / SUCCESS / ERROR / IDLE / UNKNOWN` 等状态并输出摘要。
- `/api/dashboard` 现在提供 `board` 和 `pane_activity` 数据结构；`/api/panes`、`/api/panes/{pane_id}/tail` 返回每个 pane 的实时状态附加信息。
- Pane 交互仍支持滚动保持、置顶、放大视图、特殊按键、仅发送回车等操作。

## 主要实现细节
### 后端
- `TmuxAdapter` 扩展 `list-panes` 输出解析，补充 `is_active`、宽高等信息，`send_keys` 支持多行文本与按键序列。
- `PaneService` 快照新增 `captured_at`，配合 `PaneStatusAnalyzer` 生成 `PaneActivity`/`SessionActivity` 树状数据。
- `/api/dashboard` 返回 `board`、`pane_activity`、`panes` 三类数据；`/api/panes`、`/api/panes/{pane_id}/tail` 嵌入分析结果，便于前端即时更新。

### 前端
- Stage 表格被替换为调度控制板：树状显示 session → window → pane，状态徽章与摘要文本同步更新，异常节点高亮。
- Pane 卡片顶部增加状态徽章与摘要区，随分析结果实时刷新；错误/待输入 pane 会在卡片上高亮提醒。
- 页面对 `/api/dashboard` 轮询，支持暂停/手动刷新；用户偏好（布局、置顶）继续保存在 `localStorage`。

## 自动化测试
- 使用虚拟环境运行单元测试：
  ```bash
  .venv/bin/python -m pytest
  ```
  - 重点校验 `/api/dashboard`、pane send 行为（文本/按键/仅回车）以及调度控制板/Pane 状态更新。

## 建议的手动验证
- 启动 Dashboard：`python -m tmux_agent.dashboard.cli --db <path> --host 0.0.0.0 --port 8700`。
- 在浏览器中确认：
  - 调度控制板能列出所有 session/window/pane，状态徽章与摘要符合实际 CLI 输出；异常 pane 高亮。
  - Pane 卡片搜索、置顶、布局切换、放大、回到底部提示等交互仍按预期表现。
  - 向运行中的 CLI pane 发送命令、特殊按键、仅回车后，状态与日志能即时刷新。
