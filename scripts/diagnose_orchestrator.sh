#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '\n== %s ==\n' "$1"
}

log "载入环境"
set -a && source "${HOME}/.env" && set +a
printf 'HTTP_PROXY=%s\n' "${HTTP_PROXY:-}" || true
printf 'ALL_PROXY=%s\n' "${ALL_PROXY:-}" || true
printf 'OPENAI_API_KEY=%s\n' "${OPENAI_API_KEY:+<set>}"

log "代理连通性"
if [ -n "${HTTP_PROXY:-}" ]; then
  port="${HTTP_PROXY##*:}"
  nc -vz 127.0.0.1 "$port" 2>/dev/null || echo "代理端口检查跳过"
else
  echo "未配置 HTTP_PROXY，跳过端口检测"
fi
if [ -n "${OPENAI_API_KEY:-}" ]; then
  curl -sS -I https://api.openai.com/v1/models -H "Authorization: Bearer ${OPENAI_API_KEY}" || true
else
  echo "OPENAI_API_KEY 未配置，跳过 API 连通性"
fi

log "codex 可执行"
if ! command -v codex >/dev/null 2>&1; then
  echo "codex 不在 PATH，请确认已安装或放入 ~/.local/bin" >&2
  exit 1
fi
codex --help | head -n 3 || true

log "WeCom Webhook 测试"
if [ -z "${WECOM_WEBHOOK:-}" ]; then
  echo "WECOM_WEBHOOK 未设置，跳过测试"
else
  curl -sS -X POST "$WECOM_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d '{"msgtype":"text","text":{"content":"[自检] Webhook OK"}}' || true
fi

log "启动 orchestrator (5s 探活)"
python3 -m src.tmux_agent.main --config .tmuxagent/orchestrator.toml &
PID=$!
sleep 5 || true
curl -sS http://localhost:9108/metrics | head -n 10 || true
kill "$PID" 2>/dev/null || true

log "DONE"
