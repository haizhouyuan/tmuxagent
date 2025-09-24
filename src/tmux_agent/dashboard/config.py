"""Configuration helpers for the dashboard service."""
from __future__ import annotations

from pathlib import Path


class DashboardConfig:
    """Runtime configuration for the dashboard service."""

    def __init__(
        self,
        db_path: Path,
        template_path: Path | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.template_path = Path(template_path) if template_path else None
        self.username = username
        self.password = password

    def ensure_template_path(self, fallback: Path) -> Path:
        """Return the template search path, falling back to the packaged templates."""
        return self.template_path or fallback

    @property
    def auth_enabled(self) -> bool:
        return bool(self.username and self.password)
