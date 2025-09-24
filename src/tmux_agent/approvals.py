"""Approval helpers: file-based decisions and signed tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .state import StateStore


@dataclass
class ApprovalRequest:
    host: str
    pane_id: str
    stage: str
    file_path: Path
    token: str | None = None
    approve_url: str | None = None
    reject_url: str | None = None


class ApprovalManager:
    def __init__(
        self,
        store: StateStore,
        approval_dir: Path,
        secret: str | None = None,
        base_url: str | None = None,
        token_ttl_sec: int = 24 * 3600,
    ) -> None:
        self.store = store
        self.approval_dir = approval_dir.expanduser()
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        self.secret = secret
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token_ttl_sec = token_ttl_sec

    def approval_file(self, host: str, pane_id: str, stage: str) -> Path:
        safe_host = host.replace("/", "_")
        safe_pane = pane_id.replace("%", "pct")
        safe_stage = stage.replace("/", "_")
        directory = self.approval_dir / safe_host
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{safe_pane}__{safe_stage}.txt"

    def poll_file_decision(self, host: str, pane_id: str, stage: str) -> Optional[str]:
        file_path = self.approval_file(host, pane_id, stage)
        if not file_path.exists():
            return None
        decision = file_path.read_text(encoding="utf-8").strip().lower()
        file_path.unlink(missing_ok=True)
        if decision in {"approve", "approved", "yes"}:
            return "approve"
        if decision in {"reject", "rejected", "no"}:
            return "reject"
        return None

    def ensure_request(self, host: str, pane_id: str, stage: str) -> ApprovalRequest:
        token_info = None
        if self.secret:
            token_info = self.store.get_approval_token(host, pane_id, stage)
            if token_info is None:
                token = self._make_token(host, pane_id, stage)
                expires = int(time.time()) + self.token_ttl_sec
                self.store.upsert_approval_token(host, pane_id, stage, token, expires)
                token_info = (token, expires)
        else:
            self.store.delete_approval_token(host, pane_id, stage)

        file_path = self.approval_file(host, pane_id, stage)
        if not file_path.exists():
            instructions = (
                "# Approval Request\n"
                f"# Host: {host}\n"
                f"# Pane: {pane_id}\n"
                f"# Stage: {stage}\n"
                "#\n"
                f"# To respond, write one of approve/reject to this file.\n"
                f"# Example: echo approve > {file_path}\n\nPENDING\n"
            )
            file_path.write_text(instructions, encoding="utf-8")

        approve_url = reject_url = None
        token = token_info[0] if token_info else None
        if token and self.base_url:
            approve_url = f"{self.base_url}/a/{token}/approve"
            reject_url = f"{self.base_url}/a/{token}/reject"

        return ApprovalRequest(
            host=host,
            pane_id=pane_id,
            stage=stage,
            file_path=file_path,
            token=token,
            approve_url=approve_url,
            reject_url=reject_url,
        )

    def resolve_token(self, token: str) -> tuple[str, str, str]:
        if not self.secret:
            raise ValueError("Approval secret not configured")
        host, pane_id, stage, expires = self._parse_token(token)
        if expires < int(time.time()):
            raise ValueError("Token expired")
        self.store.delete_approval_token(host, pane_id, stage)
        return host, pane_id, stage

    def _make_token(self, host: str, pane_id: str, stage: str) -> str:
        assert self.secret, "secret required for token generation"
        now = int(time.time())
        payload = f"{host}|{pane_id}|{stage}|{now + self.token_ttl_sec}"
        sig = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        return f"{_b64(payload.encode())}.{_b64(sig)}"

    def _parse_token(self, token: str) -> tuple[str, str, str, int]:
        try:
            payload_b64, sig_b64 = token.split(".", 1)
        except ValueError as exc:
            raise ValueError("Malformed token") from exc
        payload_raw = _b64_decode(payload_b64)
        sig_raw = _b64_decode(sig_b64)
        expected_sig = hmac.new(self.secret.encode("utf-8"), payload_raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig_raw, expected_sig):
            raise ValueError("Invalid signature")
        host, pane_id, stage, expires = payload_raw.decode("utf-8").split("|", 3)
        return host, pane_id, stage, int(expires)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
