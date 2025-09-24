"""FastAPI application exposing a lightweight dashboard."""
from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any, Callable

from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Form
from fastapi import Request
from fastapi import status
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import DashboardConfig
from .data import DashboardDataProvider
from ..approvals import ApprovalManager
from ..state import StateStore


def create_app(config: DashboardConfig) -> FastAPI:
    app = FastAPI(title="tmux-agent dashboard", version="0.1.0")

    template_root = config.ensure_template_path(Path(__file__).with_name("templates"))
    templates = Jinja2Templates(directory=str(template_root))

    def get_provider() -> DashboardDataProvider:
        return DashboardDataProvider(config.db_path)

    auth_dependency = _build_auth_dependency(config)

    def _write_decision(host: str, pane_id: str, stage: str, decision: str) -> None:
        store = StateStore(config.db_path)
        try:
            manager = ApprovalManager(
                store=store,
                approval_dir=config.approval_dir,
            )
            manager.ensure_request(host, pane_id, stage)
            path = manager.approval_file(host, pane_id, stage)
            path.write_text(f"{decision}\n", encoding="utf-8")
        finally:
            store.close()

    @app.get("/api/overview")
    def overview(
        provider: DashboardDataProvider = Depends(get_provider),
        _: None = Depends(auth_dependency),
    ) -> dict[str, Any]:
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
        _: None = Depends(auth_dependency),
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
    def submit_approval(
        host: str,
        pane_id: str,
        stage: str,
        payload: dict[str, Any],
        _: None = Depends(auth_dependency),
    ) -> dict[str, str]:
        decision = (payload.get("decision") or "").strip().lower()
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid decision")
        _write_decision(host, pane_id, stage, decision)
        return {"status": "ok", "decision": decision}

    @app.post("/approvals/{decision}")
    def submit_approval_form(
        decision: str,
        host: str = Form(...),
        pane_id: str = Form(...),
        stage: str = Form(...),
        _: None = Depends(auth_dependency),
    ) -> RedirectResponse:
        normalized = decision.lower()
        if normalized not in {"approve", "reject"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid decision")
        _write_decision(host, pane_id, stage, normalized)
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    return app


def _build_auth_dependency(config: DashboardConfig) -> Callable[..., None]:
    if not config.auth_enabled:
        def passthrough() -> None:  # pragma: no cover - trivial branch
            return None
        return passthrough

    security = HTTPBasic()

    def guard(credentials: HTTPBasicCredentials = Depends(security)) -> None:
        username_ok = secrets.compare_digest(credentials.username or "", config.username or "")
        password_ok = secrets.compare_digest(credentials.password or "", config.password or "")
        if not (username_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    return guard
