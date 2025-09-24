"""FastAPI application exposing a lightweight dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import DashboardConfig
from .data import DashboardDataProvider
from .panes import PaneService
from ..approvals import ApprovalManager
from ..state import StateStore
from ..tmux import TmuxAdapter


def create_app(config: DashboardConfig) -> FastAPI:
    app = FastAPI(title="tmux-agent dashboard", version="0.1.0")

    template_root = config.ensure_template_path(Path(__file__).with_name("templates"))
    templates = Jinja2Templates(directory=str(template_root))

    tmux_adapter = TmuxAdapter(tmux_bin=config.tmux_bin, socket=config.tmux_socket)
    pane_service = PaneService(tmux_adapter, capture_lines=config.capture_lines)

    def get_provider() -> DashboardDataProvider:
        return DashboardDataProvider(config.db_path)

    def get_pane_service() -> PaneService:
        return pane_service

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
            "stages": [
                {
                    "host": row.host,
                    "pane_id": row.pane_id,
                    "pipeline": row.pipeline,
                    "stage": row.stage,
                    "status": row.status,
                    "retries": row.retries,
                    "updated_at": row.updated_at.isoformat(),
                    "details": row.details,
                }
                for row in rows
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        provider: DashboardDataProvider = Depends(get_provider),
    ) -> HTMLResponse:
        rows = provider.stage_rows()
        summary = provider.status_summary()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "rows": rows,
                "summary": summary,
            },
        )

    @app.get("/healthz")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

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
    def panes(provider: PaneService = Depends(get_pane_service)) -> list[dict[str, Any]]:
        snapshots = provider.snapshots()
        items: list[dict[str, Any]] = []
        for snap in snapshots:
            preview = PaneService.preview_lines(snap.lines, limit=10)
            items.append(
                {
                    "pane_id": snap.pane_id,
                    "session": snap.session,
                    "window": snap.window,
                    "title": snap.title,
                    "preview": preview,
                }
            )
        return items

    @app.get("/api/panes/{pane_id}/tail")
    def pane_tail(pane_id: str, provider: PaneService = Depends(get_pane_service)) -> dict[str, Any]:
        capture = provider.capture(pane_id)
        return {"pane_id": pane_id, "lines": capture.content.splitlines()}

    @app.post("/api/panes/{pane_id}/send")
    def send_to_pane(pane_id: str, payload: dict[str, Any], provider: PaneService = Depends(get_pane_service)) -> dict[str, str]:
        text = (payload.get("input") or "").rstrip("\n")
        if not text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="input required")
        enter = payload.get("enter", True)
        provider.send(pane_id, text, enter=bool(enter))
        return {"status": "ok"}

    @app.get("/panes", response_class=HTMLResponse)
    def panes_page(request: Request, provider: PaneService = Depends(get_pane_service)) -> HTMLResponse:
        snapshots = provider.snapshots()
        return templates.TemplateResponse(
            request,
            "panes.html",
            {
                "panes": snapshots,
                "capture_lines": config.capture_lines,
            },
        )

    return app
