"""FastAPI app for mobile-friendly local agent portal."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Any
from typing import Optional

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import AgentConfig
from .config import load_agent_config
from .local_bus import LocalBus
from .state import StateStore
from .tmux import TmuxAdapter


class CommandPayload(BaseModel):
    text: str
    session: str | None = None
    host: str | None = None
    enter: bool = True
    sender: str | None = None


def _load_state_store(path: Path) -> StateStore:
    store = StateStore(path)
    return store


def create_app(*, config: AgentConfig, bus: LocalBus, auth_token: str | None = None) -> FastAPI:
    state_path = config.expanded_sqlite_path()
    app = FastAPI(title="tmux-agent portal", version="1.0")

    adapters = [
        TmuxAdapter(
            tmux_bin=config.tmux_bin,
            socket=host.tmux.socket,
            ssh=host.ssh,
        )
        for host in config.hosts
    ]

    def capture_session_tail(session_name: str, lines: int = 200) -> str:
        for adapter in adapters:
            try:
                panes = adapter.panes_for_session(session_name)
            except Exception:  # pragma: no cover - tmux probing failures fall back to next adapter
                continue
            if not panes:
                continue
            pane = panes[0]
            try:
                capture = adapter.capture_pane(pane.pane_id, lines)
            except Exception:  # pragma: no cover - capture failures should not break API
                continue
            return capture.content.rstrip()
        return ""

    async def require_token(x_auth_token: Optional[str] = Header(default=None)) -> None:
        if auth_token and x_auth_token != auth_token:
            raise HTTPException(status_code=401, detail="invalid auth token")

    def fetch_state_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        store = _load_state_store(state_path)
        try:
            cur = store._conn.execute(query, params)  # type: ignore[attr-defined]
            rows = [dict(row) for row in cur.fetchall()]
        finally:
            store.close()
        return rows

    @app.get("/", response_class=HTMLResponse)
    async def root() -> str:
        token_declaration = (
            f"const AUTH_TOKEN = '{auth_token}';" if auth_token else "const AUTH_TOKEN = null;"
        )
        token_placeholder = "__TOKEN_DECLARATION__"
        html = textwrap.dedent(
            """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>tmux Agent 控制台</title>
  <style>
    body { font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; margin:0; background:#0f172a; color:#f8fafc; }
    header { padding:16px; text-align:center; font-size:1.1rem; background:#1e293b; position:sticky; top:0; z-index:1; }
    #session-details { padding:16px; max-height:32vh; overflow-y:auto; border-bottom:1px solid rgba(148,163,184,0.25); }
    #notifications { padding:16px; max-height:40vh; overflow-y:auto; }
    #session-details pre { background:#0f172a; padding:12px; border-radius:10px; white-space:pre-wrap; word-break:break-word; }
    .card { background:#1f2937; margin-bottom:12px; padding:12px; border-radius:12px; box-shadow:0 8px 16px rgba(15,23,42,0.3); }
    .card h3 { margin:0 0 8px; font-size:1rem; color:#38bdf8; }
    .card time { display:block; font-size:0.75rem; color:#cbd5f5; margin-bottom:6px; }
    form { position:fixed; bottom:0; left:0; right:0; background:#1e293b; padding:12px; display:flex; gap:8px; align-items:center; }
    input, select { flex:1; padding:10px; border-radius:10px; border:none; background:#111827; color:#e2e8f0; }
    button { padding:10px 16px; border:none; border-radius:10px; background:#38bdf8; color:#0f172a; font-weight:600; }
    button:active { transform:scale(0.98); }
    #status { font-size:0.75rem; color:#fbbf24; margin-left:8px; }
  </style>
</head>
<body>
  <header>tmux Agent 控制台</header>
  <section id="session-details"></section>
  <section id="notifications"></section>
  <form id="command-form">
    <select id="session-select"></select>
    <input id="command-input" type="text" placeholder="输入指令..." autocomplete="off" />
    <button type="submit">发送</button>
  </form>
  <span id="status"></span>
<script>
__TOKEN_DECLARATION__
let lastTs = 0;
let sessionCache = [];
const notifEl = document.getElementById('notifications');
const statusEl = document.getElementById('status');
const formEl = document.getElementById('command-form');
const inputEl = document.getElementById('command-input');
const sessionEl = document.getElementById('session-select');
const sessionDetailEl = document.getElementById('session-details');

sessionEl.innerHTML = '<option value="">(加载中…)</option>';
sessionDetailEl.innerHTML = '<article class="card"><div>会话信息加载中…</div></article>';
statusEl.textContent = '会话列表加载中…';

function authHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  if (AUTH_TOKEN) headers['X-Auth-Token'] = AUTH_TOKEN;
  return headers;
}

function escapeHtml(text) {
  return (text || '').replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatTimestamp(ts) {
  if (!ts) return '尚无输出';
  const parsed = Number(ts);
  if (!Number.isFinite(parsed) || parsed <= 0) return '尚无输出';
  return new Date(parsed * 1000).toLocaleString();
}

function renderSessionDetail(sessionName) {
  if (!sessionName) {
    sessionDetailEl.innerHTML = '<article class="card"><div>未选择会话</div></article>';
    return;
  }
  const info = sessionCache.find(item => item.session_name === sessionName);
  if (!info) {
    sessionDetailEl.innerHTML = '<article class="card"><div>未找到会话信息</div></article>';
    return;
  }
  const title = escapeHtml(info.description || info.session_name);
  const branch = escapeHtml(info.branch || '');
  const model = escapeHtml(info.model || '');
  const template = escapeHtml(info.template || '默认');
  const status = escapeHtml(info.status || 'unknown');
  const logPath = escapeHtml(info.log_path || '');
  const lastPrompt = escapeHtml(info.last_prompt || '');
  const metadata = typeof info.metadata === 'object' && info.metadata ? info.metadata : {};
  const orchestratorSummary = metadata.orchestrator_summary ? escapeHtml(String(metadata.orchestrator_summary)) : '';
  const orchestratorError = metadata.orchestrator_error ? escapeHtml(String(metadata.orchestrator_error)) : '';
  const metadataDump = escapeHtml(JSON.stringify(metadata, null, 2));
  const body = info.last_output ? `<pre>${escapeHtml(info.last_output)}</pre>` : '<div>暂无输出</div>';
  const logLine = logPath ? `<div>日志：${logPath}</div>` : '';
  const promptLine = lastPrompt ? `<div>最近提示：${lastPrompt}</div>` : '';
  const summaryLine = orchestratorSummary ? `<div>🤖 摘要：${orchestratorSummary}</div>` : '';
  const errorLine = orchestratorError ? `<div>⚠️ ${orchestratorError}</div>` : '';
  sessionDetailEl.innerHTML = `
    <article class="card">
      <h3>${title}</h3>
      <time>最近输出时间：${formatTimestamp(info.last_output_at)}</time>
      <div>分支：${branch}</div>
      <div>模型：${model}</div>
      <div>模板：${template}</div>
      <div>状态：${status}</div>
      ${promptLine}
      ${logLine}
      ${summaryLine}
      ${errorLine}
      <details><summary>元数据</summary><pre>${metadataDump}</pre></details>
      ${body}
    </article>
  `;
}

async function fetchNotifications() {
  try {
    const resp = await fetch(`/api/notifications?since=${lastTs}`, { headers: authHeaders() });
    if (!resp.ok) throw new Error('请求失败');
    const data = await resp.json();
    if (!Array.isArray(data.items)) return;
    data.items.forEach(item => appendNotification(item));
    if (data.items.length > 0) {
      lastTs = data.items[data.items.length - 1].ts;
    }
  } catch (err) {
    statusEl.textContent = `⚠️ ${err}`;
  }
}

function appendNotification(item) {
  const card = document.createElement('article');
  card.className = 'card';
  const ts = new Date(item.ts * 1000).toLocaleString();
  const bodyHtml = (item.body || '').split('\\n').join('<br/>');
  card.innerHTML = `<h3>${item.title || '通知'}</h3><time>${ts}</time><div>${bodyHtml}</div>`;
  notifEl.appendChild(card);
  notifEl.scrollTop = notifEl.scrollHeight;
}

async function fetchSessions() {
  try {
    const resp = await fetch('/api/sessions', { headers: authHeaders() });
    if (!resp.ok) throw new Error('加载会话失败');
    const data = await resp.json();
    sessionCache = Array.isArray(data.sessions) ? data.sessions : [];
    sessionEl.innerHTML = '';
    if (!sessionCache.length) {
      sessionEl.innerHTML = '<option value="">(无可用会话)</option>';
      sessionDetailEl.innerHTML = '<article class="card"><div>暂无会话</div></article>';
      return;
    }
    sessionCache.forEach(sess => {
      const option = document.createElement('option');
      option.value = sess.session_name;
      option.textContent = sess.session_name;
      sessionEl.appendChild(option);
    });
    let current = sessionEl.value;
    if (!sessionCache.some(item => item.session_name === current)) {
      current = sessionCache[0].session_name;
      sessionEl.value = current;
    }
    renderSessionDetail(current);
    statusEl.textContent = `会话数：${sessionCache.length}`;
  } catch (err) {
    sessionEl.innerHTML = '<option value="">(无可用会话)</option>';
    sessionDetailEl.innerHTML = `<article class="card"><div>⚠️ ${escapeHtml(String(err))}</div></article>`;
    statusEl.textContent = `⚠️ ${escapeHtml(String(err))}`;
  }
}

sessionEl.addEventListener('change', () => {
  renderSessionDetail(sessionEl.value);
});

formEl.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  const payload = { text, session: sessionEl.value || null };
  try {
    const resp = await fetch('/api/commands', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload)
    });
    if (!resp.ok) throw new Error('发送失败');
    inputEl.value = '';
    statusEl.textContent = '✅ 已发送';
    setTimeout(() => statusEl.textContent = '', 2000);
  } catch (err) {
    statusEl.textContent = `⚠️ ${err}`;
  }
});

fetchSessions();
fetchNotifications();
setInterval(fetchNotifications, 3000);
setInterval(fetchSessions, 8000);
</script>
</body>
</html>
"""
        )
        return html.replace(token_placeholder, token_declaration)

    @app.get("/api/notifications")
    async def api_notifications(
        since: float | None = None,
        _: None = Depends(require_token),
    ) -> JSONResponse:
        items = [
            {
                **item,
                "ts": float(item.get("ts", 0)),
            }
            for item in bus.recent_notifications(limit=50, since_ts=since)
        ]
        return JSONResponse({"items": items})

    @app.get("/api/sessions")
    async def api_sessions(_: None = Depends(require_token)) -> JSONResponse:
        rows = fetch_state_rows(
            """
            SELECT
                branch,
                session_name,
                worktree_path,
                model,
                template,
                description,
                status,
                last_prompt,
                last_output,
                last_output_at,
                log_path
            FROM agent_sessions
            ORDER BY updated_at DESC
            """
        )
        enriched_rows: list[dict[str, Any]] = []
        for row in rows:
            last_output = row.get("last_output")
            if not last_output:
                tail = capture_session_tail(row["session_name"])
                if tail:
                    last_output = tail
            row["last_output"] = last_output or ""
            enriched_rows.append(row)
        if not enriched_rows:
            # fallback to tmux list sessions for visibility
            try:
                import subprocess

                proc = subprocess.run(
                    [config.tmux_bin, "list-sessions", "-F", "#{session_name}"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                sessions = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                enriched_rows = [
                    {
                        "branch": name,
                        "session_name": name,
                        "worktree_path": "",
                        "model": None,
                        "template": None,
                        "description": None,
                        "status": "unknown",
                        "last_prompt": None,
                        "last_output": capture_session_tail(name),
                        "last_output_at": None,
                        "log_path": None,
                    }
                    for name in sessions
                ]
            except Exception:  # pragma: no cover
                enriched_rows = []
        return JSONResponse({"sessions": enriched_rows})

    @app.post("/api/commands")
    async def api_commands(
        payload: CommandPayload,
        _: None = Depends(require_token),
    ) -> JSONResponse:
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        record = payload.model_dump()
        record.setdefault("sender", "mobile")
        bus.append_command(record)
        return JSONResponse({"status": "ok"})

    return app


def build_app_from_config(config_path: Path, auth_token: str | None = None) -> FastAPI:
    agent_config = load_agent_config(config_path)
    bus = LocalBus(agent_config.expanded_bus_dir())
    return create_app(config=agent_config, bus=bus, auth_token=auth_token)


__all__ = ["create_app", "build_app_from_config"]
