# tmux-agent 真实环境测试指导

本文档帮助你在 NAS 主机（例如 `fnos`）上快速验证 tmux-agent 在真实 tmux pane 中的行为，覆盖 **输出采集、自动输入、shell 动作、审批流以及升级通知**。

## 1. 测试前准备

- **环境变量 / 依赖**  
  - Python 3.10+、pip、tmux 已安装。
  - tmux-agent 代码同步至 NAS 对应目录，例如 `~/projects/tmuxagent`。
  - 确保你可以通过 `ssh fnos` 登录 NAS（密钥已配置）。

- **推荐 tmux Pane 布局**  
  1. 登录 NAS 后执行：
     ```bash
     tmux new-session -d -s proj:test -n agent:codex-ci "cd ~/workspace/storyapp && bash"
     tmux select-pane -t proj:test:1.1 -T "codex:ci"

     tmux new-window  -t proj:test -n agent:codex-ux "cd ~/workspace/storyapp && bash"
     tmux select-pane -t proj:test:2.1 -T "codex:ux"
     ```
  2. 在 pane `proj:test:agent:codex-ci.1` 中启动 codex CLI：
     ```bash
     codex --dangerously-bypass-approvals-and-sandbox chat --project storyapp
     ```
  3. 在 CLI 的系统提示里加入 `### SENTRY {json}` 输出约定，便于策略解析。

- **配置文件准备**  
  - 将 `examples/hosts.example.yaml` 复制成 `hosts.yaml`，并将 `ssh.host` 设置为 `fnos`，`ssh.user` 设置为 NAS 用户名。
  - 根据测试需求复制 `examples/policy.example.yaml` 为 `policy.yaml`，并确保包含：
    - 一段触发 `send_keys` 的 stage（例如 lint）。
    - 一段带 `shell` 动作的 stage（可仿照文末示例）。
    - 一个 `require_approval: true` 的 stage，并在 `on_fail` 中配置 `escalate`。

## 2. 启动代理

1. SSH 到 NAS：`ssh fnos`
2. 进入项目目录并激活虚拟环境（如有）：
   ```bash
   cd ~/projects/tmuxagent
   source .venv/bin/activate  # 若使用虚拟环境
   pip install -e .[dev]
   ```
3. 以 DEBUG 日志启动代理：
   ```bash
   python -m tmux_agent.main \
     --config hosts.yaml \
     --policy policy.yaml \
     --log-level DEBUG \
     --approval-secret secret123 \
     --public-base-url https://example.com  # 可选
   ```
4. 观察日志中是否出现：
   - “Starting tmux agent…”
   - “Discovered pane …” 等信息。

## 3. 输出采集验证

1. 在目标 pane 中输入或让 codex CLI 打印一行新的文本，例如：
   ```
   ### SENTRY {"type":"STATUS","stage":"lint","ok":true}
   ```
2. 代理日志应解析出 `STATUS` 消息；`~/.tmux_agent/state.db` 中的 `pane_offsets` 应更新。
3. 如要进一步确认，可在代理日志查找 `new_lines` 或 `ParsedMessage` 调试输出。

## 4. send_keys 模拟输入验证

1. 在 policy 中配置 lint stage 的触发正则（例如 `run lint`）。
2. 在目标 pane 中手动输出 `run lint`，或由 codex CLI 打印相同文本。
3. 代理应立刻向该 pane 注入 `npm run lint`（或你配置的命令）。
4. 在 pane 历史中应看到命令前缀 `[SENT:...]` 或命令被执行的效果。
5. `state.db` 中 lint 阶段状态应变为 `RUNNING` 或 `COMPLETED`（根据后续输出）。

## 5. shell 动作验证

1. 策略 stage 示例：
   ```yaml
   - name: shell-stage
     triggers:
       any_of:
         - log_regex: "shell please"
     actions_on_start:
       - send_keys: "say hello"
       - shell: "echo remote > /tmp/tmux_agent_shell_test.txt"
     success_when:
       any_of:
         - log_regex: "done"
   ```
2. 在 pane 中输出 `shell please`，待代理触发后：
   - pane 应收到 `say hello`；
   - NAS 上 `/tmp/tmux_agent_shell_test.txt` 应出现 `remote`。
3. 输出 `done`，验证阶段转为 `COMPLETED`。

## 6. 审批与升级流程

1. 策略里为某 stage 设置 `require_approval: true`，并在 `on_fail` 中加入 `escalate: <code>`。
2. 触发该 stage，让代理写出审批文件，默认路径例如：
   `~/.tmux_agent/approvals/proj:test/%1__build.txt`
3. 在 NAS 上：
   ```bash
   echo approve > ~/.tmux_agent/approvals/proj:test/%1__build.txt
   ```
   - 阶段应进入 `RUNNING`。
4. 如果输出失败信息（或直接在策略里模拟），阶段应进入 `FAILED`；日志中会出现“Escalation: …”通知。

## 7. 诊断与清理

- 查看数据库内容：
  ```bash
  sqlite3 ~/.tmux_agent/state.db 'SELECT * FROM stage_state;'
  sqlite3 ~/.tmux_agent/state.db 'SELECT * FROM pane_offsets;'
  ```
- 清理测试数据：
  ```bash
  rm -f /tmp/tmux_agent_shell_test.txt
  rm -rf ~/.tmux_agent/approvals/proj:test
  sqlite3 ~/.tmux_agent/state.db 'DELETE FROM stage_state; DELETE FROM pane_offsets; DELETE FROM approval_tokens;'
  ```
- 停止代理：在运行窗口 `Ctrl+C` 即可。

## 8. 常见问题排查

| 症状 | 可能原因 | 处理方式 |
| ---- | -------- | -------- |
| 代理找不到 pane | `session_filters`/`pane_name_patterns` 正则不匹配 | 调整 hosts.yaml 中的正则；`tmux list-panes -a -F '#{session_name} #{window_name} #{pane_title}'` 辅助调试 |
| send_keys 无效果 | pane 中 CLI 已退出、会话冻结或命令未按回车发送 | 检查 CLI 状态；若需保持会话活跃，可在策略中加入 keep-alive |
| shell 动作失败 | 远程命令非零退出 / SSH 无权限 | 查看代理日志；必要时为命令包装 `set -xe` 报错、修正权限 |
| 审批文件未生成 | `approval_dir` 未创建或权限不足 | 确认 `~/.tmux_agent/approvals` 可写；首次审批时代理会自动创建 |

## 9. 附录：快速策略模板

```yaml
principles: []
pipelines:
  - name: demo
    match:
      any_of:
        - window_name: "^agent:codex-ci$"
    stages:
      - name: lint
        triggers:
          any_of: [{ log_regex: "run lint" }]
        actions_on_start:
          - send_keys: "npm run lint"
        success_when:
          any_of: [{ log_regex: "lint ok" }]

      - name: shell-stage
        triggers:
          any_of: [{ log_regex: "shell please" }]
        actions_on_start:
          - send_keys: "say hello"
          - shell: "echo remote > /tmp/tmux_agent_shell_test.txt"
        success_when:
          any_of: [{ log_regex: "done" }]

      - name: build
        triggers:
          any_of: [{ after_stage_success: "lint" }]
        require_approval: true
        actions_on_start:
          - send_keys: "npm run build"
        success_when:
          any_of: [{ log_regex: "build ok" }]
        fail_when:
          any_of: [{ log_regex: "build failed" }]
        on_fail:
          - escalate: "build_failure"
```

按照以上步骤执行后，你应能完整验证 tmux-agent 在真实 pane 中的输入输出、shell 动作、审批与升级行为。如遇到异常，请记录代理日志、SQL 查询结果与策略片段，再反馈排查。
