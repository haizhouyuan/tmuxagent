"""
Advanced approval system with file and web callback support.
Provides secure, flexible human-in-the-loop capabilities.
"""

import os
import time
import hmac
import hashlib
import base64
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """Represents an approval request."""
    pane_id: str
    stage_name: str
    request_type: str  # "require_approval", "ask_human", "confirm_action"
    message: str
    options: list = None
    metadata: Dict[str, Any] = None
    requested_at: datetime = None
    
    def __post_init__(self):
        if self.options is None:
            self.options = ["approve", "reject"]
        if self.metadata is None:
            self.metadata = {}
        if self.requested_at is None:
            self.requested_at = datetime.now()


class ApprovalManager:
    """
    Manages approval requests with multiple response channels.
    
    Supports both file-based and web-based approval workflows,
    with security measures to prevent unauthorized approvals.
    """
    
    def __init__(self, 
                 approval_dir: Optional[Path] = None,
                 secret_key: Optional[str] = None,
                 token_ttl: int = 86400):  # 24 hours
        
        self.approval_dir = approval_dir or Path.home() / ".tmux_sentry" / "approvals"
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        
        self.secret_key = secret_key or os.getenv("APPROVAL_SECRET", "change-me-in-production")
        self.token_ttl = token_ttl
        
        # Track pending requests
        self.pending_requests: Dict[str, ApprovalRequest] = {}
        
        logger.info(f"ApprovalManager initialized with directory: {self.approval_dir}")
    
    def create_approval_request(self, request: ApprovalRequest) -> str:
        """
        Create a new approval request.
        
        Returns a unique token for web-based approvals.
        """
        request_id = self._generate_request_id(request)
        self.pending_requests[request_id] = request
        
        # Create file for file-based approval
        file_path = self._get_approval_file_path(request_id)
        file_path.write_text(
            f"# Approval Request\n"
            f"# Pane: {request.pane_id}\n"
            f"# Stage: {request.stage_name}\n"
            f"# Type: {request.request_type}\n"
            f"# Message: {request.message}\n"
            f"# Options: {', '.join(request.options)}\n"
            f"# Created: {request.requested_at.isoformat()}\n"
            f"#\n"
            f"# To respond, replace this content with one of: {', '.join(request.options)}\n"
            f"# Example: echo 'approve' > {file_path}\n"
            f"\n"
            f"PENDING\n",
            encoding="utf-8"
        )
        
        # Generate secure token for web approval
        token = self._generate_approval_token(request_id)
        
        logger.info(f"Created approval request {request_id} for {request.pane_id}/{request.stage_name}")
        return token
    
    def check_approval_response(self, request_id: str) -> Optional[str]:
        """
        Check for approval response via file or web.
        
        Returns the response ('approve', 'reject', etc.) or None if pending.
        """
        if request_id not in self.pending_requests:
            return None
        
        # Check file-based response first
        file_response = self._check_file_response(request_id)
        if file_response:
            self._cleanup_request(request_id)
            return file_response
        
        # TODO: Web responses are handled via the web server endpoint
        # This would be updated when we receive a web response
        
        return None
    
    def process_web_approval(self, token: str, response: str) -> bool:
        """
        Process a web-based approval response.
        
        Returns True if the response was valid and processed.
        """
        try:
            request_id = self._validate_approval_token(token)
            if not request_id or request_id not in self.pending_requests:
                logger.warning(f"Invalid or expired approval token")
                return False
            
            request = self.pending_requests[request_id]
            
            if response not in request.options:
                logger.warning(f"Invalid response '{response}' for request {request_id}")
                return False
            
            # Write response to file (for consistency with file-based approval)
            file_path = self._get_approval_file_path(request_id)
            file_path.write_text(response, encoding="utf-8")
            
            logger.info(f"Processed web approval for {request_id}: {response}")
            return True
            
        except Exception as e:
            logger.error(f"Error processing web approval: {e}")
            return False
    
    def get_pending_requests(self) -> Dict[str, ApprovalRequest]:
        """Get all pending approval requests."""
        return self.pending_requests.copy()
    
    def cleanup_expired_requests(self, max_age_hours: int = 24) -> int:
        """
        Clean up expired approval requests.
        
        Returns the number of requests cleaned up.
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        expired_requests = []
        
        for request_id, request in self.pending_requests.items():
            if request.requested_at < cutoff_time:
                expired_requests.append(request_id)
        
        for request_id in expired_requests:
            self._cleanup_request(request_id)
            logger.info(f"Cleaned up expired approval request {request_id}")
        
        return len(expired_requests)
    
    def _generate_request_id(self, request: ApprovalRequest) -> str:
        """Generate a unique request ID."""
        timestamp = int(time.time())
        pane_clean = request.pane_id.replace("%", "pct").replace("/", "_")
        return f"{pane_clean}__{request.stage_name}__{timestamp}"
    
    def _get_approval_file_path(self, request_id: str) -> Path:
        """Get the file path for an approval request."""
        return self.approval_dir / f"{request_id}.txt"
    
    def _check_file_response(self, request_id: str) -> Optional[str]:
        """Check for file-based approval response."""
        file_path = self._get_approval_file_path(request_id)
        
        if not file_path.exists():
            return None
        
        try:
            content = file_path.read_text(encoding="utf-8").strip()
            
            # Skip empty files or files that still contain the template
            if not content or content == "PENDING" or content.startswith("#"):
                return None
            
            # Extract the first meaningful line
            response = content.split('\n')[0].strip().lower()
            
            request = self.pending_requests[request_id]
            if response in [opt.lower() for opt in request.options]:
                logger.info(f"File approval response for {request_id}: {response}")
                return response
            else:
                logger.warning(f"Invalid file response '{response}' for {request_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error reading approval file {file_path}: {e}")
            return None
    
    def _cleanup_request(self, request_id: str) -> None:
        """Clean up a completed or expired request."""
        # Remove from pending requests
        self.pending_requests.pop(request_id, None)
        
        # Remove file
        file_path = self._get_approval_file_path(request_id)
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception as e:
                logger.error(f"Error removing approval file {file_path}: {e}")
    
    def _generate_approval_token(self, request_id: str) -> str:
        """Generate a secure approval token."""
        timestamp = int(time.time())
        payload = f"{request_id}|{timestamp}|{self.token_ttl}"
        
        # Create HMAC signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        # Encode payload and signature
        token_data = {
            "payload": base64.urlsafe_b64encode(payload.encode('utf-8')).decode('ascii').rstrip('='),
            "signature": base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')
        }
        
        return base64.urlsafe_b64encode(json.dumps(token_data).encode('utf-8')).decode('ascii').rstrip('=')
    
    def _validate_approval_token(self, token: str) -> Optional[str]:
        """Validate approval token and return request_id if valid."""
        try:
            # Add padding and decode
            token_padded = token + '=' * (4 - len(token) % 4)
            token_json = base64.urlsafe_b64decode(token_padded).decode('utf-8')
            token_data = json.loads(token_json)
            
            # Extract payload and signature
            payload_b64 = token_data["payload"] + '=' * (4 - len(token_data["payload"]) % 4)
            signature_b64 = token_data["signature"] + '=' * (4 - len(token_data["signature"]) % 4)
            
            payload = base64.urlsafe_b64decode(payload_b64).decode('utf-8')
            signature = base64.urlsafe_b64decode(signature_b64)
            
            # Verify signature
            expected_signature = hmac.new(
                self.secret_key.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha256
            ).digest()
            
            if not hmac.compare_digest(signature, expected_signature):
                logger.warning("Invalid token signature")
                return None
            
            # Parse payload
            parts = payload.split('|')
            if len(parts) != 3:
                logger.warning("Invalid token format")
                return None
            
            request_id, timestamp_str, ttl_str = parts
            timestamp = int(timestamp_str)
            ttl = int(ttl_str)
            
            # Check expiration
            if time.time() > timestamp + ttl:
                logger.warning(f"Token expired for request {request_id}")
                return None
            
            return request_id
            
        except Exception as e:
            logger.error(f"Error validating approval token: {e}")
            return None
    
    def generate_approval_links(self, token: str, base_url: str) -> Dict[str, str]:
        """Generate approval links for web interface."""
        base_url = base_url.rstrip('/')
        
        return {
            "approve": f"{base_url}/approve/{token}",
            "reject": f"{base_url}/reject/{token}"
        }
    
    def get_request_by_token(self, token: str) -> Optional[ApprovalRequest]:
        """Get approval request by token."""
        request_id = self._validate_approval_token(token)
        if request_id:
            return self.pending_requests.get(request_id)
        return None