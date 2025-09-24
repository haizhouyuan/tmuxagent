# tmux-agent 可视化面板调研与实施方案

## 目标

为 NAS 上的多 tmux session / 多项目工作流提供一套可视化面板，实现：
- 实时查看每个 host/pipeline/stage 的状态、耗时、失败原因和升级提示；
- 自由触发审批、重试等操作，替代手写文件；
- 集中展示待审批、最近失败、通知日志等关键信息；
- 支持未来扩展通知、历史查看等需求。

## 现有数据与能力

- **StateStore (SQLite)**：
  - `pane_offsets`：每个 host/pane 的尾部游标与更新时间。
  - `stage_state`：记录 host/pipeline/stage 状态、重试次数、扩展数据（`data` 字段含 `escalate_code` 等）。
  - `approval_tokens`：审批令牌、过期时间，用于 webhook/令牌式审批。
- **Runner 逻辑**（`src/tmux_agent/runner.py`）：
  - 每次轮询遍历 pane，捕获新日志 -> 解析 -> 评估策略 -> 写入 SQLite。
  - 调用 `Notifier` 发送通知（stdout/serverchan/wecom）。
  - 执行动作（send_keys/shell），对失败/审批进行日志提醒。
- **ApprovalManager**：对文件式审批及 token 进行管理；`ensure_request` 会创建说明文件并记录 token。

## 可视化实现选型比较

| 方案 | 优点 | 缺点 | 适配性 |
| ---- | ---- | ---- | ---- |
| FastAPI + 前端 (HTMX/Vue) | 灵活、扩展性好、易集成审批操作 | 需额外 Web 服务、认证处理 | ★★★★ 推荐 |
| 终端 TUI (Textual/urwid) | 无需浏览器、部署轻 | 多用户不便、界面受限 | ★★ |
| Prometheus + Grafana | 强大的可视化与告警 | 部署复杂、交互性弱、审批难集成 | ★★ |
| Streamlit/NiceGUI | 开发快、组件多 | 常驻资源高、定制灵活性差 | ★★ |

> 推荐首选：**FastAPI + HTMX** 搭建轻量 Web 面板。

## 推荐方案概要

### 后端（FastAPI）

1. **数据库访问**：使用只读 `StateStore` 或直接 `sqlite3` 连接提供以下接口：
   - `GET /api/overview`：统计每个 host/pipeline 的 stage 状态、更新时间。
   - `GET /api/pipelines/{name}`：返回指定 pipeline 的 stage 列表、状态、重试次数、最近错误信息（`data.reason`）。
   - `GET /api/approvals`：列出待审批项（审批文件路径、token、等待时间）。
   - `POST /api/approvals/{id}`：通过 Web 提交审批（内部可写审批文件或调用新方法写 SQLite）。
   - `GET /events`：SSE 或 WebSocket 推送 stage 状态更新。

2. **与 Runner 协同**：
   - 同进程：在 `Runner.run_forever` 中使用 asyncio 创建后台任务 (`uvicorn.Server`)，共享同一 SQLite 连接，避免锁冲突。
   - 独立进程：Runner 和 Web 面板分别运行，只读连接 SQLite，需开启 `PRAGMA journal_mode=WAL`，并通过审批 API 写文件或写决定表。

3. **安全控制**：
   - 内网访问控制或 Basic Auth；
   - 审批 API 检查 token/用户权限，防止滥用；
   - 对外只暴露必要服务端点。

### 前端界面（HTMX/轻量框架）

- **Dashboard 页面**：
  - 卡片展示各项目当前 `RUNNING / WAITING_APPROVAL / FAILED` 数量；
  - 列表显示最近失败、升级事件（利用 `stage_state.data.escalate_code`）。
- **Pipeline 详情页**：
  - 表格显示 stage 顺序、状态、重试次数、更新时间、最后两条日志（可从 `StageState.data` 或直接回读 pane 日志片段）。
  - 提供重试/重置按钮（调用新 API）。
- **审批中心**：
  - 待审批列表：显示 host/pane/stage、等待时长、token、审批文件路径；
  - 按钮执行 `approve/reject` 调用 `POST /api/approvals`。
- **Pane 监控**：
  - 显示所有匹配 pane 的 session/window/title、最后 N 行输出；
  - 可使用 HTMX 轮询或 SSE 实现实时刷新。

### 扩展与优化

- 在 `Runner._handle_outcome` 中添加事件钩子，把阶段状态变化或通知写入 `events` 表或消息队列（Redis/ZeroMQ），供前端实时刷新。
- 封装审批逻辑：新增 `ApprovalManager.decide(host, pane, stage, decision)`，由 Web 端直接调用，避免写文件。
- 将 `policy.yaml` 与 `hosts.yaml` 元数据在面板中展示，帮助团队了解规则配置。
- 支持历史查询：
  - 在 `stage_state` 写入历史表或追加变更日志；
  - 或将关键事件写入 `notifications` 表。

## 预估实施步骤

1. **基础接口**
   - 新建 `tmux_agent.api` 模块，封装 FastAPI 应用和数据库查询方法。
   - 暴露 `/api/overview`, `/api/approvals` 等基础 API。
   - 编写简易前端模板（HTML + HTMX）渲染 dashboard。

2. **Runner 集成**
   - 选用单进程或双进程模式：若选单进程，在 `main.py` 启动时同时启动 Uvicorn；若独立部署，则提供单独命令 `python -m tmux_agent.dashboard`。
   - 在 Runner 中调用 `api.notify_stage_update(...)` 钩子，实现实时更新。

3. **审批与操作**
   - 抽象审批决定接口，使 Web 按钮直接调用（无需写文件）。
   - 加入重试/重置 API，允许面板触发策略动作。

4. **安全与部署**
   - 添加 Basic Auth 或引入 token；
   - 对外只监听内网地址（如 `127.0.0.1` + Nginx 反代）；
   - 编写 systemd / supervisord 脚本保证 Web 与 Runner 常驻。

5. **测试与文档**
   - 扩展当前测试：对 API 编写集成测试（FastAPI TestClient），确保接口返回结构稳定。
   - 更新文档：
     - 新增 `docs/dashboard_usage.md` 说明部署、认证、功能；
     - 更新 README 强调可视化面板特性。

## 资源与工具建议

- **后端**：FastAPI, SQLModel/Dataclasses, Uvicorn；
- **前端**：HTMX + Tailwind（或简单 Bootstrap），SSE for live updates；
- **部署**：systemd 服务文件、Nginx 反向代理（如需外网访问）；
- **监控**：可选接入 Sentry/Prometheus，观测 API 性能。

## 总结

- SQLite 已聚合了调度所需关键状态，构建面板只需在其上方提供读写 API 和界面即可；
- 轻量 Web 面板最符合“集中管理、多 session、多项目”的诉求，也便于 future feature（审批、重试、历史审计）；
- 实施优先级：先完成基础 Dashboard + 审批中心，再迭代实时推送与历史分析。

