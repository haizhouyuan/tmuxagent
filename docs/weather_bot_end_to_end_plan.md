# Weather Bot End-to-End Validation Plan

本方案用于在全新仓库 `/home/yuanhaizhou/projects/testprojectfortmuxagent` 上验证 tmux-agent Orchestrator 的端到端能力，包括模板初始化、命令注入、审批、监控与异常回放。按照步骤执行，即可搭建一个“查询天气并生成播报”的演示环境。

---

## 0. 前置准备

1. **创建试验仓库**
   ```bash
   mkdir -p /home/yuanhaizhou/projects/testprojectfortmuxagent
   cd /home/yuanhaizhou/projects/testprojectfortmuxagent
   git init
   ```

2. **建立虚拟环境并安装 tmux-agent**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ../tmuxagent-worktrees/fleetai[dev]
   ```

3. **初始化 tmux-agent**
   ```bash
   .venv/bin/tmux-agent init
   ```
   - 生成 `.tmuxagent/fleet.toml`、模板目录。
   - 如需共享状态，可将 `[state] db` 指向 `~/.tmux_agent/state.db`。

4. **端口预留**
   - Weather API stub：`9010`
   - Weather UI：`8787`
   - Orchestrator API：`9108`
   - Dashboard：`8702`
   - Prometheus exporter：`9108`

---

## 1. 业务脚手架

### 1.1 天气 API Stub

`scripts/weather_api_stub.py`
```python
from fastapi import FastAPI, HTTPException

app = FastAPI()

MOCK_DATA = {
    "beijing": {"temp": 26, "humidity": 65, "summary": "Cloudy"},
    "shanghai": {"temp": 29, "humidity": 70, "summary": "Sunny"},
    "london": {"temp": 21, "humidity": 55, "summary": "Light Rain"},
}

@app.get("/weather")
def get_weather(city: str):
    key = city.lower()
    if key not in MOCK_DATA:
        raise HTTPException(status_code=404, detail="city not found")
    return {"city": city, **MOCK_DATA[key]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9010)
```

运行：`python scripts/weather_api_stub.py`

### 1.2 Weather UI 面板

`ui/weather_panel.py`
```python
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
import httpx

app = FastAPI()

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <title>Weather Bot Demo</title>
  <style>
    body { font-family: sans-serif; margin: 2rem auto; max-width: 600px; }
    form { display: flex; gap: 0.5rem; }
    input[type=text] { flex: 1; padding: 0.5rem; }
    button { padding: 0.5rem 1rem; }
    .result { margin-top: 1.5rem; border: 1px solid #ccc; padding: 1rem; border-radius: 0.5rem; }
  </style>
</head>
<body>
  <h1>天气查询机器人</h1>
  <form method=\"post\" action=\"/query\"
        hx-post=\"/query\"
        hx-target=\"#result\"
        hx-swap=\"innerHTML\">
    <input type=\"text\" name=\"city\" placeholder=\"输入城市 (如 Beijing)\" required />
    <button type=\"submit\">查询</button>
  </form>
  <div id=\"result\" class=\"result\">等待查询...</div>
  <script src=\"https://unpkg.com/htmx.org@1.9.10\"></script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML_TEMPLATE

@app.post("/query", response_class=HTMLResponse)
async def query(city: str = Form(...)) -> str:
    city_clean = city.strip()
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            "http://127.0.0.1:9108/orchestrator/commands",
            json={"city": city_clean}
        )
    if resp.status_code != 200:
        return f"<p>调用 orchestrator 失败: {resp.text}</p>"
    data = resp.json()
    if data.get("status") != "ok":
        return f"<p>执行失败: {data}</p>"
    summary = data.get("summary", "暂无摘要")
    return f"<h2>{city_clean} 播报</h2><p>{summary}</p>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
```

---

## 2. Orchestrator 配置

### 2.1 Prompt 模板

`.tmuxagent/templates/weather_command.md`
```markdown
# Weather Bot Command Prompt

You are the orchestrator for weather assistant.

Context:
- Session: {session}
- Metadata: {metadata}
- Log excerpt:
```
{log_excerpt}
```

Return JSON:
{
  "summary": "status update",
  "phase": "planning|fetching|summarizing|done|blocked",
  "blockers": ["..."],
  "commands": [
    {
      "text": "curl -s http://127.0.0.1:9010/weather?city={metadata[city]}",
      "risk_level": "low"
    }
  ],
  "notify": null,
  "requires_confirmation": false
}

If city missing, set phase to "blocked".
```

`.tmuxagent/templates/weather_summary.md`
```markdown
Generate a concise Chinese summary for:
City: {metadata[city]}
Log:
```
{log_excerpt}
```

Return JSON {"summary": "..."}
```

### 2.2 `.tmuxagent/orchestrator.toml`

```toml
poll_interval = 10
cooldown_seconds = 5
session_cooldown_seconds = 8
metrics_port = 9108

[prompts]
command = "../templates/weather_command.md"
summary = "../templates/weather_summary.md"

[phase_prompts]
planning = "../templates/weather_command.md"
fetching = "../templates/weather_command.md"
summarizing = "../templates/weather_command.md"
done = "../templates/weather_command.md"

[[tasks]]
branch = "weather/bot"
title = "城市天气播报"
phases = ["planning", "fetching", "summarizing", "done"]
depends_on = []
responsible = "weather-ops"
tags = ["demo", "weather"]
```

### 2.3 模板

`.tmuxagent/templates/weather-bot.md`
```markdown
---
name: weather-bot
description: Weather assistant session
model: gpt-5-codex
---
你会接收城市名称，协调 orchestrator 发送命令，请求天气 API 并生成播报。
```

### 2.4 Orchestrator API

`orchestrator_api.py`
```python
from fastapi import FastAPI, HTTPException
from tmux_agent.config import load_agent_config
from tmux_agent.local_bus import LocalBus

CONFIG_PATH = "agent-config.yaml"
app = FastAPI()

def _bus() -> LocalBus:
    cfg = load_agent_config(CONFIG_PATH)
    return LocalBus(cfg.expanded_bus_dir())

@app.post("/orchestrator/commands")
async def orchestrator_commands(payload: dict[str, str]) -> dict[str, str]:
    city = (payload.get("city") or "").strip()
    if not city:
        raise HTTPException(status_code=400, detail="city required")
    bus = _bus()
    bus.append_command({
        "text": f"echo CITY={city}",
        "session": "weather/bot",
        "sender": "ui",
    })
    return {"status": "ok", "summary": f"已触发天气查询: {city}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9108)
```

---

## 3. 演练步骤

1. `source .venv/bin/activate`
2. 启动天气 stub：`python scripts/weather_api_stub.py`
3. 启动 orchestrator API：`uvicorn orchestrator_api:app --host 0.0.0.0 --port 9108`
4. 启动 Weather UI：`uvicorn ui.weather_panel:app --host 0.0.0.0 --port 8787`
5. 启动 orchestrator（先 dry-run）：
   ```bash
   tmux-agent-orchestrator \
     --config agent-config.yaml \
     --orchestrator-config .tmuxagent/orchestrator.toml \
     --dry-run
   ```
6. 生成 Weather 会话（若未创建）：`.venv/bin/tmux-agent spawn weather/bot --template weather-bot`
7. 若需要 Dashboard：`.venv/bin/python -m tmux_agent.dashboard.cli --db ~/.tmux_agent/state.db --approval-dir ~/.tmux_agent/approvals`

---

## 4. 测试矩阵

| 测试 | 步骤 | 预期 |
| ---- | ---- | ---- |
| 正常流程 | UI 提交 `Beijing` | Orchestrator 生成 `curl` 命令，`phase` 最终为 `done`，UI 显示天气摘要 |
| 并发 | 快速提交 `Shanghai`, `London` | `queued_commands` 短暂存在后清空，执行顺序保持 FIFO |
| Dry-run | 使用 `--dry-run` | 无命令写入 bus，`orchestrator_commands_total{result="dry_run"}` 增加 |
| 错误处理 | 停止天气 API 后再查询 | `blockers` 包含错误原因，发送 `requires_attention` 通知，`orchestrator_decision_errors_total` 增加 |
| 审批流程 | 在 prompt 中模拟高风险命令 | 出现 `pending_confirmation`，通过手机门户/API 批准后命令执行 |
| Metrics | `curl http://127.0.0.1:9108/metrics` | 暴露 `orchestrator_queue_size` 等指标 |
| 回放 | `tmux-agent-orchestrator-replay --branch weather/bot --limit 200` | 输出 command/queued/confirmation 统计、最大队列深度 |

---

## 5. 自动化测试建议

- 新建 `tests/test_weather_flow.py`，使用 `pytest` + `pytest-asyncio`，mock API stub，模拟提交 → 轮询 `StateStore` → 断言 `phase == "done"`、`orchestrator_summary` 存在。
- 使用 `pytest-httpx` 替换真实 HTTP 调用。
- 将 orchestrator、stub、UI 启动封装成 fixture（`subprocess.Popen` + `yield`）。

---

## 6. 运维与告警检查

1. `curl http://127.0.0.1:9108/metrics` 验证指标；Prometheus/Graphana 建立面板（队列深度、错误率、决策耗时）。
2. `tmux-agent-orchestrator-replay` 定期审计，确认无异常队列堆积。
3. `tmux_agent.notify_bridge` 转发 `requires_attention` 通知到企业微信/Slack。
4. `.tmuxagent/logs/orchestrator-actions.jsonl` 确认包含 `command/queued/confirmation` 事件。
5. 回归测试：`.venv/bin/python -m pytest`（含天气用例）。

---

## 7. 常见故障排查

| 现象 | 排查 | 处理 |
| ---- | ---- | ---- |
| UI 报错 "调用 orchestrator 失败" | 检查 `orchestrator_api` 是否运行，端口 9108 是否被占用 | 重启 API；确认 `agent-config.yaml` 路径正确 |
| 队列不出队 | 查看 `orchestrator_queue_size`；`tmux capture-pane` 确认命令执行 | 手动清除队列或排查命令报错 |
| 无心跳 | `orchestrator_heartbeat` 超过阈值 | 重启 orchestrator；配置告警提醒 |
| Dry-run 仍执行命令 | 是否开启 `--dry-run`；检查日志中 `dry_run: true` | 升级 tmux-agent 版本；确认配置项 |

执行完上述步骤即可在独立仓库验证 Orchestrator 的完整流程，为集成真实业务提供模板。遇到问题可针对相应模块（prompt、service、API）迭代。 
