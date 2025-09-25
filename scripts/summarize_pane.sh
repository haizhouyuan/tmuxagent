#!/usr/bin/env bash
set -euo pipefail

PROMPT_DEFAULT="请用要点总结以下终端输出，突出错误、风险、当前状态以及下一步建议（给出具体命令）。"
LINES_DEFAULT=5000
FORMAT_DEFAULT="text"
MODEL_DEFAULT="${TMUX_AGENT_CLAUDE_MODEL:-claude-sonnet-4-20250514}"
MAX_TURNS_DEFAULT=1
MAX_CHARS_DEFAULT=600000

usage() {
  cat <<'USAGE'
用法: summarize_pane.sh [选项] [pane]

  pane                tmux pane 标识（如 %3 或 session:win.pane）。省略时默认当前 pane（需在 tmux 中执行）。

选项：
  -l, --lines N       最多保留输出的末尾 N 行（默认 5000）
  -p, --prompt TEXT   总结提示词（默认内置）
  -f, --format FMT    Claude 输出格式 text|json|stream-json（默认 text）
  -m, --model MODEL   Claude 模型名称（默认 claude-sonnet-4-20250514，或 TMUX_AGENT_CLAUDE_MODEL）
  --max-chars N       字符截断上限（默认 600000）
  --max-turns N       Claude 最大回合数（默认 1）
  -h, --help          显示本帮助

示例：
  # 总结当前 pane
  summarize_pane.sh

  # 总结指定 pane 并输出 JSON
  summarize_pane.sh --format json %3

USAGE
}

PROMPT="$PROMPT_DEFAULT"
LINES="$LINES_DEFAULT"
FORMAT="$FORMAT_DEFAULT"
MODEL="$MODEL_DEFAULT"
MAX_TURNS="$MAX_TURNS_DEFAULT"
MAX_CHARS="$MAX_CHARS_DEFAULT"
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--prompt)
      PROMPT="$2"
      shift 2
      ;;
    -l|--lines)
      LINES="$2"
      shift 2
      ;;
    -f|--format)
      FORMAT="$2"
      shift 2
      ;;
    -m|--model)
      MODEL="$2"
      shift 2
      ;;
    --max-turns)
      MAX_TURNS="$2"
      shift 2
      ;;
    --max-chars)
      MAX_CHARS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "未知选项: $1" >&2
      usage
      exit 1
      ;;
    *)
      TARGET="$1"
      shift
      ;;
  esac
done

if [[ -n "$@" ]]; then
  echo "多余的参数: $@" >&2
  usage
  exit 1
fi

if [[ -z "$TARGET" ]]; then
  if [[ -z "${TMUX:-}" ]]; then
    echo "错误: 未指定 pane，且当前不在 tmux 会话内" >&2
    exit 1
  fi
  TARGET="$(tmux display -p '#{pane_id}')"
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "错误: 未找到 tmux 命令" >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "错误: 未找到 claude CLI，请先安装/登录 Claude Code" >&2
  exit 1
fi

RAW_CONTENT="$(tmux capture-pane -pJ -t "$TARGET" -S -999999 || true)"

CLEAN_CONTENT="$(printf "%s" "$RAW_CONTENT" \
  | sed -r 's/\x1B\[[0-9;?]*[ -/]*[@-~]//g' \
  | tr -d '\000-\010\013\014\016-\037\177')"

if [[ -n "$LINES" && "$LINES" -gt 0 ]]; then
  CLEAN_CONTENT="$(printf "%s" "$CLEAN_CONTENT" | tail -n "$LINES")"
fi

CURRENT_LEN=$(printf "%s" "$CLEAN_CONTENT" | wc -c | awk '{print $1}')
if (( CURRENT_LEN > MAX_CHARS )); then
  CLEAN_CONTENT="$(printf "%s" "$CLEAN_CONTENT" | tail -c "$MAX_CHARS")"
fi

CLAUDE_ARGS=("-p" "$PROMPT" "--output-format" "$FORMAT" "--max-turns" "$MAX_TURNS" "--model" "$MODEL")

printf "%s" "$CLEAN_CONTENT" | claude "${CLAUDE_ARGS[@]}"
