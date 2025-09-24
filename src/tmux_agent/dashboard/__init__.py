"""Lightweight web dashboard for tmux-agent state visibility."""

from .app import create_app
from .config import DashboardConfig

__all__ = ["create_app", "DashboardConfig"]
