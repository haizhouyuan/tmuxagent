# StoryApp Orchestrator 验证计划（2025-09-27 更新）

> 目标：在 StoryApp 场景下系统性验证 tmux-agent orchestrator 的调度、决策、自愈与观测能力，重点补救 2025-09-27 报告中暴露的 Codex JSON 解析、UTF-8 解码与错误循环问题。

## 0. 执行概览
- **验证周期**：修复完成后 1 个工作日内执行，持续 45–60 分钟。
- **预期产物**：新版验证记录（A-H 场景）、指标截图、WeCom 通知截图、问题单跟踪。
- **通过门槛**：关键场景（A、B、C、G、H）全部通过，其余至少 2/3 通过。

## 1. 验证范围
- **仓库**：`~/projects/storyapp`（主仓）、`.tmuxagent/worktrees/storyapp-*`（工作树）。
- **Orchestrator**：tmux-agent orchestrator（Prometheus 9108），Codex CLI 通过 `codex-simple-tty` 包装器运行。
- **重点能力**：
  1. StoryApp 会话识别与命令调度。
  2. 卡顿/失败检测、自愈与人工干预闭环。
  3. Codex 决策 JSON 输出、编码兼容、错误断路器。
  4. 指标、日志、通知链路完整性。

## 2. 预检查（Stage -1）
| 步骤 | 命令/动作 | 判定标准 |
| --- | --- | --- |
| 检查 orchestrator 配置 | `grep codex-simple-tty .tmuxagent/orchestrator.toml` | 路径正确，`extra_args` 合理 |
| Codex CLI 健康检查 | `script -qfc "codex --version" /dev/null` | 输出版本号，无错误 |
| JSON 输出验证 | `script -qfc "codex --dangerously-bypass-approvals-and-sandbox 'return {"status": "ok"}'" /dev/null` | 输出为合法 JSON，无额外文本 |
| UTF-8 兼容测试 | 运行自定义脚本输出中文 JSON | 无解码错误 |
| Stop/Start orchestrator | 重启服务，确认 9108 端口可用 | 服务平稳启动，无异常日志 |

未通过任何一项需先排查修复，再进入场景验证。

## 3. 场景矩阵（A-H）
| 场景 | 名称 | 目标 | 触发方式（StoryApp 对应） | 验证要点 |
| --- | --- | --- | --- | --- |
| A | 命令执行反馈 | 命令执行闭环 | `npm run build/test`、`docker compose up` | 成功/失败状态上报、指标增长、日志记录、通知 |
| B | 卡顿检测与自愈 | Stall 检测 + 建议 | 在 StoryApp 启动 `sleep 200` 或 `npm install --no-progress` | 触发 stall 日志、给出恢复建议、可人工干预 |
| C | 决策辅助 | Codex 拆解任务建议 | 在会话输入“重新实现 StoryTree 高级模式” | 生成结构化建议，引用 StoryApp 文档，无 JSON 错误 |
| D | 需求分解 | 文档理解与计划 | 引导阅读 `docs/story-generation-analysis.md` | 计划合理、引用准确、输出结构化 |
| E | 连续失败告警 | 告警节流与通知 | 连续注入 3 个失败命令 | 限频触发、WeCom 或本地告警、指标计数 |
| F | 指标与审计 | 监控与日志 | 查询 Prometheus、审计日志 | 指标存在 storyapp 标签、日志记录完整 |
| G | 通知降级 | 通知容错 | 暂时注释 `WECOM_WEBHOOK`，观察行为 | 服务不退出、记录降级、恢复后通知恢复 |
| H | Codex 输出健康 | 重点新增：验证 JSON/编码/断路器 | 触发 Codex 决策 3 次（含中文），观察 JSON/错误处理 | 无 JSON 解析错误；UTF-8 正常；错误不循环；提供 fallback |

## 4. 执行步骤
1. **准备会话**：创建/确认 tmux 会话 `proj:storyapp`、`agent-storyapp-*`，pane 名称符合策略。
2. **逐场景执行**：按照 A→H 顺序，单场景≤10 分钟；关键步骤通过 tmux `send-keys` 注入。
3. **数据记录**：
   - Orchestrator 日志：`.tmuxagent/logs/orchestrator-actions.jsonl`。
   - Prometheus 指标：`curl http://localhost:9108/metrics | grep storyapp`。
   - WeCom/本地通知截图。
   - Codex 输出（若必要 `~/.tmux_agent/replay`）。
4. **异常处置**：若出现阻塞或循环错误，立即记录日志、停止重复、重启 orchestrator，再继续下一场景。

## 5. 判定标准
- **核心指标**：
  - `orchestrator_decision_latency_seconds_count{branch="storyapp/..."}` 按场景递增。
  - `orchestrator_command_success_total / _failures_total` 与命令结果一致。
  - `orchestrator_json_parse_failures_total` 与 `utf8_decode_errors_total` 在场景 H 期间为 0。
- **Codex 健康**：
  - 每次决策输出合法 JSON（可通过 `jq` 验证）。
  - 中文决策无解码错误。
  - 同类错误不重复触发超过 3 次；若发生，触发断路器/暂停并提示人工处理。
- **通知链路**：降级期间记录清晰，恢复后有恢复提示。

## 6. 记录模板
```
场景：H Codex 输出健康
时间：2025-09-27 10:15
操作：tmux send-keys ... "# Orchestrator：规划 StoryTree >=500 字的内容" Enter
预期：返回合法 JSON，包含建议列表
结果：✅/❌
Orchestrator 输出：...
指标变化：...
问题单：...
备注：...
```

## 7. 风险与缓解
- **Codex CLI 再次异常**：保留 fallback mock 脚本，必要时切换验证。
- **UTF-8 数据污染**：测试前清除无效日志，确保终端编码为 UTF-8。
- **错误循环**：设置 `MAX_ERROR_RETRY=3`（如配置支持），避免通知风暴。
- **Stall 测试**：需确保命令安全可终止，建议在临时分支/环境执行。

## 8. 验收与输出
- 完整填写场景执行记录。
- 提取关键指标截图、通知记录。
- 汇总问题，按优先级创建修复任务：
  - P0：Codex JSON/编码/循环问题。
  - P1：会话识别、卡顿检测灵敏度。
  - P2：决策质量优化、自动化测试补充。
- 生成《StoryApp Orchestrator 验证报告（新）》并与上一版对比。

---
*维护者：tmux-agent 团队 / 更新日期：2025-09-27*
