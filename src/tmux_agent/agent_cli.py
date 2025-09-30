"""Agent CLI entrypoint for管理 Git worktree + tmux 会话."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .agents.config import FleetConfig
from .agents.config import load_fleet_config
from .agents.config import write_default_config
from .agents.service import AgentService
from .agents.templates import load_templates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tmux-agent", description="管理 AI 代理工作流 (worktree + tmux)")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init", help="初始化 .tmuxagent/fleet.toml 配置")
    init_cmd.add_argument("--path", type=Path, default=None, help="指定仓库根目录 (默认当前 Git 仓库)")

    templates_cmd = sub.add_parser("templates", help="列出可用模板")
    templates_cmd.add_argument("--path", type=Path, default=None, help="指定仓库根目录")

    list_cmd = sub.add_parser("sessions", help="列出已登记的代理会话")
    list_cmd.add_argument("--path", type=Path, default=None, help="指定仓库根目录")

    spawn_cmd = sub.add_parser("spawn", help="创建/启动代理会话")
    spawn_cmd.add_argument("branch", help="目标分支名称")
    spawn_cmd.add_argument("--task", help="任务描述，将写入状态表")
    spawn_cmd.add_argument("--template", help="模板名称 (来自 templates 目录)")
    spawn_cmd.add_argument("--model", help="覆盖默认模型")
    spawn_cmd.add_argument("--startup", help="覆盖默认启动命令模板")
    spawn_cmd.add_argument("--path", type=Path, default=None, help="指定仓库根目录")

    return parser


def cmd_init(args: argparse.Namespace) -> None:
    repo_root = _resolve_root(args.path)
    path = write_default_config(repo_root)
    print(f"已写入配置: {path}")


def cmd_templates(args: argparse.Namespace) -> None:
    config = load_fleet_config(args.path)
    templates = load_templates(config.templates_dir)
    if not templates:
        print(f"未在 {config.templates_dir} 找到模板")
        return
    for spec in templates.values():
        desc = spec.description or "(无描述)"
        model = spec.model or "默认"
        print(f"- {spec.name} (model={model})\n  {desc}\n  {spec.path}")


def cmd_sessions(args: argparse.Namespace) -> None:
    service = AgentService(load_fleet_config(args.path))
    try:
        agents = service.list_agents()
    finally:
        service.close()
    if not agents:
        print("当前没有登记的代理会话")
        return
    for agent in agents:
        line = f"{agent.branch} -> {agent.session_name} (@ {agent.worktree_path})"
        extras: list[str] = []
        if agent.model:
            extras.append(f"model={agent.model}")
        if agent.template:
            extras.append(f"template={agent.template}")
        if agent.description:
            extras.append(f"task={agent.description}")
        extras.append(f"status={agent.status}")
        if agent.log_path:
            extras.append(f"log={agent.log_path}")
        line += " [" + ", ".join(extras) + "]"
        print(line)


def cmd_spawn(args: argparse.Namespace) -> None:
    config = load_fleet_config(args.path)
    templates = load_templates(config.templates_dir)

    if args.template and args.template not in templates:
        available = ", ".join(sorted(templates.keys())) or "无"
        raise SystemExit(f"未找到模板 {args.template}；可用模板: {available}")

    service = AgentService(config)
    try:
        record = service.spawn_agent(
            branch=args.branch,
            task=args.task,
            template_name=args.template,
            model=args.model,
            command_template=args.startup,
            metadata={"source": "cli"},
        )
    finally:
        service.close()

    print(
        "已创建代理:\n"
        f"  分支: {record.branch}\n"
        f"  会话: {record.session_name}\n"
        f"  模型: {record.model}\n"
        f"  模板: {record.template or '无'}\n"
        f"  日志: {record.log_path}"
    )


def _resolve_root(path: Path | None) -> Path:
    if path is not None:
        if not (path / ".git").exists():
            raise SystemExit(f"{path} 不是 Git 仓库根目录")
        return path.resolve()
    return load_fleet_config(path).repo_root


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            cmd_init(args)
        elif args.command == "templates":
            cmd_templates(args)
        elif args.command == "sessions":
            cmd_sessions(args)
        elif args.command == "spawn":
            cmd_spawn(args)
        else:  # pragma: no cover - argparse 已限制
            parser.print_help()
            return 1
        return 0
    except Exception as exc:  # pragma: no cover - CLI 兜底
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
