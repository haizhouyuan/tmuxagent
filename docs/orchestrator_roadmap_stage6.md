# Orchestrator Stage 6 Execution Plan

## 背景与目标
- 目标对齐业务诉求：避免 Codex CLI 卡住、自动推进剩余任务、遇阻能自我诊断并协助人工。
- 在既有阶段 1~5 交付的基础上，继续升级调度闭环、决策辅助与自主开发实验能力。

## 里程碑概览
| Milestone | 目标 | 预计交付 | 负责人 | 依赖 |
| --- | --- | --- | --- | --- |
| M1 | 命令执行反馈闭环 | Runner 捕获 exit code/输出并写入 metadata；Orchestrator 依据结果更新 phase/blockers | Codex Team | Runner/StateStore 修改 |
| M2 | 卡顿检测与自愈 | 心跳+日志停滞监控、阻塞超时策略、自动提醒/重试 | Codex Team | M1 数据结构 |
| M3 | 决策辅助输出 | 生成“下一步行动建议”摘要并推送到 Dashboard/通知 | AI Ops | M1/M2 元数据稳定 |
| M4 | 自主开发实验增强 | Weather Bot 端到端流程扩展：任务分解 agent、模板+脚手架生成、集成测试 | AI Ops | 上述能力完备 |

## 工作分解
### M1 命令执行反馈闭环
1. **Runner 捕获结果**
   - 发送命令时注册 pane 标记，监听新日志块直到 prompt 返回或超时。
   - 解析 shell 提示/exit status（采集 `$?` 或监控 `command; printf` 尾标记）。
2. **StateStore 扩展**
   - `agent_sessions.metadata` 新增 `last_command_result`（包含文本、exit_code、ts）。
   - 引入 `command_history`（最多 20 条）供审计。
3. **Orchestrator 使用反馈**
   - `_handle_decision` 在命令 dispatch 后记录队列 ID；收到成功回报时移除队列并更新 phase。
   - 若 exit code !=0，追加 blockers/通知。
4. **测试&监控**
   - 新单测：模拟 LocalBus 命令执行+fake runner 回报。
   - 指标：`orchestrator_command_failures_total`。

### M2 卡顿检测与自愈
1. **心跳增强**
   - `OrchestratorService` 每次循环记录 `orchestrator_heartbeat`.add(`loop_status`).
   - Dashboard 增加“心跳延迟”标记，>90s 变红。
2. **停滞侦测器**
   - 新协程/定时器：读取 `metadata.last_output_at`, `last_command_result.ts`, 若超过 `stall_timeout`（默认 300 秒）触发。
   - 自动行为：重新渲染 prompt 或将任务置为 BLOCKED 并发送告警。
3. **自动重试/升级**
   - 针对高频失败命令（连续 n 次 exit!=0）暂停命令注入。
   - 通知 payload 包含错误摘要和建议动作。
4. **测试**
   - 伪造元数据模拟停滞，确保触发重试/告警。

### M3 决策辅助输出
1. **分析管线**
   - 新模块 `orchestrator/insights.py`：读取最新摘要、任务计划、git 状态（通过工作树路径）生成下一步建议。
   - 输出结构：`{"recommendations": [...], "confidence": 0-1, "evidence": ...}`。
2. **集成 Orchestrator Loop**
   - 每次 `run_once` 后更新 `metadata.next_actions`，在 Dashboard + 通知展示。
3. **通知策略**
   - 当建议改变或 confidence 降低时，推送一条“决策辅助”通知。
4. **文档**
   - 更新 `docs/orchestrator_monitoring.md` 与 Dashboard 使用指南。
5. **测试**
   - 单测覆盖 git 状态变化与推荐输出。

### M4 自主开发实验增强
1. **任务分解 Agent**
   - 新 `orchestrator/tasks/decomposer.py`：根据需求文档生成步骤命令列表。
   - 支持 fallback 模式：若主模型失败，返回人工指引。
2. **脚手架生成**
   - 融合模板库（`templates/`）生成基础文件；未完成部分标记 TODO。
3. **执行编排**
   - Orchestrator 根据分解结果逐步执行命令，结合 M1 反馈动态调整。
4. **验证脚本**
   - 扩展 `docs/weather_bot_end_to_end_plan.md`，新增 log 捕获与结果示例。
   - 新 pytest 场景使用 mock API/文件系统。
5. **成效评估**
   - 记录成功率、耗时、人工干预点；追加到 `docs/orchestrator_ai_development_test_report.md`。

## 风险与缓解
- **执行反馈对现有 Runner 影响**：逐步 feature flag (`runner.capture_results=true`)；落地后再默认开启。
- **性能开销**：心跳/停滞检测使用增量查询；命令结果解析使用异步线程避免阻塞。
- **AI 质量波动**：提供回退策略与人工确认环。

## 验收与发布
1. 全量 pytest + 新增集成测试通过。
2. Weather Bot 场景复测并记录于测试报告。
3. Prometheus 指标在演示环境验证，Grafana 仪表确认显示。
4. 试运行 1 个真实项目 >=24h；无严重回退后合入主分支。

