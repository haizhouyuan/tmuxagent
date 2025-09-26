"""FastAPI application exposing a lightweight dashboard."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from .analyzer import PaneStatus
from .analyzer import PaneStatusAnalyzer
from .analyzer import _aggregate_status
from pydantic import BaseModel
from pydantic import Field
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import generate_latest
from typing import Optional

from .config import DashboardConfig
from .data import DashboardDataProvider
from .panes import PaneService
from .summary import PaneSummaryError
from .summary import PaneSummarizer
from .summary import SummaryOptions
from ..approvals import ApprovalManager
from ..state import StateStore
from ..tmux import TmuxAdapter


class PaneSummaryRequest(BaseModel):
    prompt: Optional[str] = None
    lines: Optional[int] = Field(default=None, gt=0)
    max_chars: Optional[int] = Field(default=None, gt=0)
    max_turns: Optional[int] = Field(default=None, gt=0, le=10)
    model: Optional[str] = None


class PaneSummaryResponse(BaseModel):
    pane_id: str
    summary: str
    model: str
    duration_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    raw: Optional[dict[str, Any]] = None


def create_app(config: DashboardConfig) -> FastAPI:
    app = FastAPI(title="tmux-agent dashboard", version="0.1.0")

    template_root = config.ensure_template_path(Path(__file__).with_name("templates"))
    templates = Jinja2Templates(directory=str(template_root))

    tmux_adapter = TmuxAdapter(tmux_bin=config.tmux_bin, socket=config.tmux_socket)
    pane_service = PaneService(tmux_adapter, capture_lines=config.capture_lines)
    pane_analyzer = PaneStatusAnalyzer()
    try:
        pane_summarizer = PaneSummarizer()
    except PaneSummaryError:
        pane_summarizer = None

    def get_provider() -> DashboardDataProvider:
        return DashboardDataProvider(config.db_path)

    def get_pane_service() -> PaneService:
        return pane_service

    def serialize_stage(row) -> dict[str, Any]:
        display_time = row.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return {
            "host": row.host,
            "pane_id": row.pane_id,
            "pipeline": row.pipeline,
            "stage": row.stage,
            "status": row.status,
            "retries": row.retries,
            "updated_at": row.updated_at.isoformat(),
            "updated_at_display": display_time,
            "details": row.details,
        }

    def serialize_pane_snapshot(snap) -> dict[str, Any]:
        preview_lines = PaneService.preview_lines(snap.lines, limit=8)
        tail_excerpt = "\n".join(preview_lines).rstrip()
        return {
            "pane_id": snap.pane_id,
            "session": snap.session,
            "window": snap.window,
            "title": snap.title,
            "is_active": snap.is_active,
            "width": snap.width,
            "height": snap.height,
            "lines": snap.lines,
            "text": "\n".join(snap.lines),
            "captured_at": snap.captured_at.isoformat(),
            "tail_excerpt": tail_excerpt,
            "tail_preview": preview_lines,
            "agent_info": None,
        }

    def load_agent_sessions(provider: DashboardDataProvider) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        rows = provider.agent_sessions()
        payload = [row.to_dict() for row in rows]
        lookup = {}
        for item in payload:
            session_name = item.get("session_name")
            if session_name:
                lookup[session_name] = item
        pane_analyzer.update_agent_sessions(lookup)
        return payload, lookup

    def write_decision(host: str, pane_id: str, stage: str, decision: str) -> None:
        store = StateStore(config.db_path)
        try:
            manager = ApprovalManager(
                store=store,
                approval_dir=config.approval_dir,
            )
            manager.ensure_request(host, pane_id, stage)
            approval_file = manager.approval_file(host, pane_id, stage)
            approval_file.write_text(f"{decision}\n", encoding="utf-8")
        finally:
            store.close()

    @app.get("/api/overview")
    def overview(provider: DashboardDataProvider = Depends(get_provider)) -> dict[str, Any]:
        rows = provider.stage_rows()
        summary = provider.status_summary()
        return {
            "summary": summary,
            "stages": [serialize_stage(row) for row in rows],
        }

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        pane_service: PaneService = Depends(get_pane_service),
        provider: DashboardDataProvider = Depends(get_provider),
    ) -> HTMLResponse:
        snapshots = pane_service.snapshots()
        agent_sessions_payload, agent_lookup = load_agent_sessions(provider)
        board = pane_analyzer.build_board(snapshots)
        pane_payload = [serialize_pane_snapshot(snap) for snap in snapshots]
        board_payload = [session.to_dict() for session in board]
        activities = _flatten_activity(board)
        projects = _group_by_project(board)
        orchestrator_overview = _build_orchestrator_overview(agent_sessions_payload)
        for snap_payload in pane_payload:
            activity = activities.get(snap_payload["pane_id"])
            if activity and activity.get("agent_info"):
                snap_payload["agent_info"] = activity.get("agent_info")
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "board": board_payload,
                "projects": projects,
                "pane_activity": activities,
                "panes": pane_payload,
                "agent_sessions": agent_sessions_payload,
                "capture_lines": config.capture_lines,
                "orchestrator": orchestrator_overview,
            },
        )

    @app.get("/healthz")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics_endpoint() -> Response:
        payload = generate_latest()
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/approvals/{host}/{pane_id}/{stage}")
    def api_approval_decision(
        host: str,
        pane_id: str,
        stage: str,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        decision = (payload.get("decision") or "").strip().lower()
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid decision")
        write_decision(host, pane_id, stage, decision)
        return {"status": "ok", "decision": decision}

    @app.post("/approvals/{decision}")
    def approval_form_submit(
        decision: str,
        host: str = Form(...),
        pane_id: str = Form(...),
        stage: str = Form(...),
    ) -> RedirectResponse:
        normalized = decision.lower()
        if normalized not in {"approve", "reject"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid decision")
        write_decision(host, pane_id, stage, normalized)
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/api/panes")
    def panes(
        pane_service: PaneService = Depends(get_pane_service),
        data_provider: DashboardDataProvider = Depends(get_provider),
    ) -> list[dict[str, Any]]:
        snapshots = pane_service.snapshots()
        load_agent_sessions(data_provider)
        board = pane_analyzer.build_board(snapshots)
        activity_map = _flatten_activity(board)
        items: list[dict[str, Any]] = []
        for snap in snapshots:
            preview = PaneService.preview_lines(snap.lines, limit=10)
            payload = serialize_pane_snapshot(snap)
            payload["preview"] = preview
            payload["activity"] = activity_map.get(snap.pane_id)
            payload["tail_excerpt"] = payload.get("tail_excerpt") or "\n".join(preview).rstrip()
            if payload["activity"] and payload["activity"].get("agent_info"):
                payload["agent_info"] = payload["activity"].get("agent_info")
            items.append(payload)
        return items

    @app.get("/api/panes/{pane_id}/tail")
    def pane_tail(
        pane_id: str,
        pane_service: PaneService = Depends(get_pane_service),
        data_provider: DashboardDataProvider = Depends(get_provider),
    ) -> dict[str, Any]:
        capture = pane_service.capture(pane_id)
        lines = capture.content.splitlines()
        pane_meta = next((pane for pane in pane_service.list_panes() if pane.pane_id == pane_id), None)
        snapshot = None
        load_agent_sessions(data_provider)
        for candidate in pane_service.snapshots():
            if candidate.pane_id == pane_id:
                snapshot = candidate
                break
        activity_dict = None
        if snapshot:
            activity = pane_analyzer.analyze(snapshot)
            activity_dict = activity.to_dict()
        preview_lines = PaneService.preview_lines(lines, limit=8)
        meta: dict[str, Any] = {
            "pane_id": pane_id,
            "lines": lines,
            "tail_excerpt": "\n".join(preview_lines).rstrip(),
            "tail_preview": preview_lines,
        }
        if pane_meta:
            meta.update(
                {
                    "session": pane_meta.session_name,
                    "window": pane_meta.window_name,
                    "title": pane_meta.pane_title,
                    "is_active": pane_meta.is_active,
                    "width": pane_meta.width,
                    "height": pane_meta.height,
                }
            )
        if activity_dict:
            meta["activity"] = activity_dict
            if activity_dict.get("agent_info"):
                meta["agent_info"] = activity_dict["agent_info"]
        return meta

    @app.post("/api/panes/{pane_id}/summary")
    def pane_summary(
        pane_id: str,
        request: PaneSummaryRequest = Body(default_factory=PaneSummaryRequest),
        provider: PaneService = Depends(get_pane_service),
    ) -> PaneSummaryResponse:
        if pane_summarizer is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude CLI 未安装或未配置")
        options = SummaryOptions()
        if request.prompt is not None:
            options.prompt = request.prompt
        if request.lines is not None:
            options.lines = request.lines
        if request.max_chars is not None:
            options.max_chars = request.max_chars
        if request.max_turns is not None:
            options.max_turns = request.max_turns
        if request.model is not None:
            options.model = request.model
        try:
            result = pane_summarizer.summarize(provider, pane_id, options=options)
        except PaneSummaryError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到指定 pane") from exc
        return PaneSummaryResponse(
            pane_id=pane_id,
            summary=result.summary,
            model=result.model,
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
            raw=result.raw,
        )

    @app.post("/api/panes/{pane_id}/send")
    def send_to_pane(pane_id: str, payload: dict[str, Any], provider: PaneService = Depends(get_pane_service)) -> dict[str, str]:
        text_value = payload.get("input")
        if text_value is not None and not isinstance(text_value, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="input must be string")

        keys_payload = payload.get("keys")
        keys: list[str] | None = None
        if keys_payload is not None:
            if isinstance(keys_payload, str):
                keys = [keys_payload]
            elif isinstance(keys_payload, list) and all(isinstance(item, str) for item in keys_payload):
                keys = list(keys_payload)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="keys must be string or list of strings")

        enter_flag = bool(payload.get("enter", True))

        if text_value is None and not enter_flag and not keys:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="input or keys required")

        try:
            provider.send(pane_id, text=text_value, enter=enter_flag, keys=keys)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        return {"status": "ok"}

    @app.get("/api/dashboard")
    def dashboard_state(
        panes: PaneService = Depends(get_pane_service),
        data_provider: DashboardDataProvider = Depends(get_provider),
    ) -> dict[str, Any]:
        snapshots = panes.snapshots()
        agent_sessions_payload, agent_lookup = load_agent_sessions(data_provider)
        board = pane_analyzer.build_board(snapshots)
        board_payload = [session.to_dict() for session in board]
        activity_map = _flatten_activity(board)
        projects = _group_by_project(board)
        orchestrator_overview = _build_orchestrator_overview(agent_sessions_payload)
        return {
            "board": board_payload,
            "projects": projects,
            "pane_activity": activity_map,
            "agent_sessions": agent_sessions_payload,
            "panes": [
                {
                    **serialize_pane_snapshot(snap),
                    "agent_info": (
                        activity_map.get(snap.pane_id, {}).get("agent_info")
                        if activity_map.get(snap.pane_id)
                        else None
                    ),
                }
                for snap in snapshots
            ],
            "orchestrator": orchestrator_overview,
        }

    return app


def _flatten_activity(board: list) -> dict[str, dict[str, Any]]:
    activity: dict[str, dict[str, Any]] = {}
    for session in board:
        for window in session.windows:
            for pane in window.panes:
                activity[pane.pane_id] = pane.to_dict()
    return activity


def _group_by_project(board: list[SessionActivity]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for session in board:
        project_name = session.project or "others"
        project = projects.setdefault(
            project_name,
            {
                "name": project_name,
                "status": PaneStatus.UNKNOWN.value,
                "attention": False,
                "sessions": [],
            },
        )
        project["sessions"].append(session.to_dict())
        project["attention"] = project["attention"] or session.attention
        project_status = PaneStatus(project["status"])
        agg_status = _aggregate_status([project_status, session.status])
        project["status"] = agg_status.value
    ordered_projects = []
    for name in sorted(projects.keys()):
        ordered_projects.append(projects[name])
    return ordered_projects


def _phase_progress(
    phase_plan: list[str],
    phase_history: list[str],
    current_phase: str | None,
) -> list[dict[str, Any]]:
    progress: list[dict[str, Any]] = []
    for phase in phase_plan:
        status = "pending"
        if phase in phase_history and phase != current_phase:
            status = "done"
        if current_phase == phase:
            status = "active"
        progress.append({"name": phase, "status": status})
    if current_phase and current_phase not in phase_plan:
        progress.append({"name": current_phase, "status": "active"})
    return progress


def _build_orchestrator_overview(agent_sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overview: list[dict[str, Any]] = []
    for session in agent_sessions:
        metadata = session.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        has_signal = any(
            metadata.get(field)
            for field in (
                "phase",
                "orchestrator_summary",
                "pending_confirmation",
                "queued_commands",
                "orchestrator_error",
            )
        )
        if not has_signal:
            continue

        phase_plan = [str(item) for item in metadata.get("phase_plan", []) if isinstance(item, str)]
        phase_history = [str(item) for item in metadata.get("phase_history", []) if isinstance(item, str)]
        current_phase = str(metadata.get("phase") or "未定义")
        blockers = [str(item) for item in metadata.get("blockers", []) if str(item)]
        pending_entries: list[dict[str, Any]] = []
        raw_pending = metadata.get("pending_confirmation")
        if isinstance(raw_pending, list):
            for entry in raw_pending:
                if isinstance(entry, dict):
                    meta_section = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
                    pending_entries.append(
                        {
                            "text": entry.get("text") or entry.get("command") or "",
                            "session": entry.get("session"),
                            "risk_level": meta_section.get("risk_level"),
                        }
                    )
                else:
                    pending_entries.append({"text": str(entry)})
        queued_entries: list[dict[str, Any]] = []
        raw_queued = metadata.get("queued_commands")
        if isinstance(raw_queued, list):
            for entry in raw_queued:
                if isinstance(entry, dict):
                    queued_at = entry.get("queued_at")
                    queued_iso = None
                    queued_display = None
                    if isinstance(queued_at, (int, float)):
                        try:
                            queued_dt = datetime.fromtimestamp(float(queued_at), tz=timezone.utc)
                            queued_iso = queued_dt.isoformat()
                            queued_display = queued_dt.astimezone().strftime("%H:%M:%S")
                        except (OSError, OverflowError, ValueError):
                            queued_display = str(queued_at)
                    queued_entries.append(
                        {
                            "queue_id": entry.get("queue_id"),
                            "text": entry.get("text") or "",
                            "session": entry.get("session"),
                            "risk_level": entry.get("risk_level"),
                            "queued_at": queued_at,
                            "queued_at_iso": queued_iso,
                            "queued_at_display": queued_display,
                        }
                    )
                else:
                    queued_entries.append({"text": str(entry)})
        depends = [str(item) for item in metadata.get("depends_on", []) if str(item)]

        heartbeat_ts = metadata.get("orchestrator_heartbeat")
        heartbeat_iso: str | None = None
        heartbeat_display: str | None = None
        if isinstance(heartbeat_ts, (int, float)) and heartbeat_ts:
            try:
                heartbeat_dt = datetime.fromtimestamp(float(heartbeat_ts), tz=timezone.utc)
                heartbeat_iso = heartbeat_dt.isoformat()
                heartbeat_display = heartbeat_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            except (OSError, OverflowError, ValueError):
                heartbeat_display = str(heartbeat_ts)

        phase_progress = _phase_progress(phase_plan, phase_history, metadata.get("phase"))
        progress_fraction = 0.0
        if phase_progress:
            total = len(phase_progress)
            completed = sum(1 for item in phase_progress if item["status"] == "done")
            active_index = next(
                (idx for idx, item in enumerate(phase_progress) if item["status"] == "active"),
                None,
            )
            if active_index is not None:
                progress_fraction = (active_index + 0.5) / total
            else:
                progress_fraction = completed / total

        attention = bool(blockers or queued_entries or pending_entries or metadata.get("orchestrator_error"))
        phase_order = (
            phase_plan.index(metadata.get("phase"))
            if metadata.get("phase") in phase_plan
            else len(phase_plan)
        )

        overview.append(
            {
                "branch": session.get("branch"),
                "session": session.get("session_name"),
                "title": metadata.get("orchestrator_task")
                or session.get("description")
                or session.get("session_name"),
                "phase": current_phase,
                "phase_plan": phase_plan,
                "phase_history": phase_history,
                "phase_progress": phase_progress,
                "progress_fraction": progress_fraction,
                "blockers": blockers,
                "pending_confirmation": pending_entries,
                "queued_commands": queued_entries,
                "depends_on": depends,
                "heartbeat": heartbeat_ts,
                "heartbeat_iso": heartbeat_iso,
                "heartbeat_display": heartbeat_display,
                "responsible": metadata.get("responsible"),
                "summary": metadata.get("orchestrator_summary"),
                "last_commands": metadata.get("orchestrator_last_command") or [],
                "error": metadata.get("orchestrator_error"),
                "attention": attention,
                "updated_at": session.get("updated_at"),
                "tags": metadata.get("tags") or [],
                "phase_order": phase_order,
            }
        )

    overview.sort(
        key=lambda item: (
            not item["attention"],
            item.get("phase_order", 0),
            item.get("session") or "",
        )
    )
    return overview
