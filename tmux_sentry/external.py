"""
External status checker for async operations.
This solves the critical issue of false positives from external services.
"""

import asyncio
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union
from enum import Enum
import httpx
import re

logger = logging.getLogger(__name__)


class ExternalStatus(Enum):
    """Status of external operations."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class ExternalOperation:
    """Represents an external operation to monitor."""
    operation_id: str
    operation_type: str  # github_action, docker_build, deployment, etc.
    pane_id: str
    stage_name: str
    started_at: float
    metadata: Dict[str, Any]
    max_wait_time: int = 1800  # 30 minutes default
    check_interval: int = 30   # 30 seconds default


@dataclass
class StatusCheckResult:
    """Result of a status check."""
    status: ExternalStatus
    message: str
    details: Dict[str, Any]
    is_final: bool  # True if this is a terminal state
    next_check_in: Optional[int] = None  # Seconds until next check


class ExternalStatusChecker(ABC):
    """Abstract base class for external status checkers."""
    
    @abstractmethod
    async def check_status(self, operation: ExternalOperation) -> StatusCheckResult:
        """Check the status of an external operation."""
        pass
    
    @abstractmethod
    def can_handle(self, operation_type: str) -> bool:
        """Check if this checker can handle the operation type."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Checker name for logging."""
        pass


class GitHubActionsChecker(ExternalStatusChecker):
    """Checker for GitHub Actions workflows."""
    
    def __init__(self, github_token: Optional[str] = None):
        self.github_token = github_token
        self.base_url = "https://api.github.com"
    
    @property
    def name(self) -> str:
        return "GitHubActionsChecker"
    
    def can_handle(self, operation_type: str) -> bool:
        return operation_type in ["github_action", "github_workflow"]
    
    async def check_status(self, operation: ExternalOperation) -> StatusCheckResult:
        """Check GitHub Actions workflow status."""
        try:
            metadata = operation.metadata
            repo = metadata.get("repository")
            run_id = metadata.get("run_id")
            workflow_id = metadata.get("workflow_id")
            
            if not repo:
                return StatusCheckResult(
                    status=ExternalStatus.UNKNOWN,
                    message="Repository not specified",
                    details={},
                    is_final=True
                )
            
            # If we have a specific run ID, check it
            if run_id:
                return await self._check_run_status(repo, run_id)
            
            # If we have workflow ID, find the latest run
            if workflow_id:
                return await self._check_latest_workflow_run(repo, workflow_id)
            
            # Fallback: check recent runs
            return await self._check_recent_runs(repo)
            
        except Exception as e:
            logger.error(f"Error checking GitHub Actions status: {e}")
            return StatusCheckResult(
                status=ExternalStatus.UNKNOWN,
                message=f"Error checking status: {str(e)}",
                details={"error": str(e)},
                is_final=False,
                next_check_in=60
            )
    
    async def _check_run_status(self, repo: str, run_id: str) -> StatusCheckResult:
        """Check specific workflow run status."""
        headers = {}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{repo}/actions/runs/{run_id}",
                headers=headers,
                timeout=10.0
            )
            
            if response.status_code == 404:
                return StatusCheckResult(
                    status=ExternalStatus.UNKNOWN,
                    message=f"Workflow run {run_id} not found",
                    details={},
                    is_final=True
                )
            
            response.raise_for_status()
            data = response.json()
            
            gh_status = data.get("status")
            gh_conclusion = data.get("conclusion")
            
            # Map GitHub status to our status
            if gh_status == "completed":
                if gh_conclusion == "success":
                    status = ExternalStatus.SUCCESS
                elif gh_conclusion in ["failure", "cancelled", "timed_out"]:
                    status = ExternalStatus.FAILURE
                else:
                    status = ExternalStatus.UNKNOWN
                is_final = True
            elif gh_status in ["queued", "in_progress"]:
                status = ExternalStatus.IN_PROGRESS
                is_final = False
            else:
                status = ExternalStatus.UNKNOWN
                is_final = False
            
            return StatusCheckResult(
                status=status,
                message=f"GitHub Actions: {gh_status}/{gh_conclusion}",
                details={
                    "status": gh_status,
                    "conclusion": gh_conclusion,
                    "run_id": run_id,
                    "workflow_name": data.get("name", ""),
                    "html_url": data.get("html_url", "")
                },
                is_final=is_final,
                next_check_in=30 if not is_final else None
            )
    
    async def _check_latest_workflow_run(self, repo: str, workflow_id: str) -> StatusCheckResult:
        """Check the latest run of a specific workflow."""
        headers = {}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{repo}/actions/workflows/{workflow_id}/runs",
                headers=headers,
                params={"per_page": 1},
                timeout=10.0
            )
            
            response.raise_for_status()
            data = response.json()
            
            runs = data.get("workflow_runs", [])
            if not runs:
                return StatusCheckResult(
                    status=ExternalStatus.UNKNOWN,
                    message="No workflow runs found",
                    details={},
                    is_final=True
                )
            
            latest_run = runs[0]
            return await self._check_run_status(repo, str(latest_run["id"]))
    
    async def _check_recent_runs(self, repo: str) -> StatusCheckResult:
        """Check recent workflow runs."""
        headers = {}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{repo}/actions/runs",
                headers=headers,
                params={"per_page": 5},
                timeout=10.0
            )
            
            response.raise_for_status()
            data = response.json()
            
            runs = data.get("workflow_runs", [])
            if not runs:
                return StatusCheckResult(
                    status=ExternalStatus.UNKNOWN,
                    message="No recent workflow runs",
                    details={},
                    is_final=True
                )
            
            # Check the most recent run
            latest_run = runs[0]
            return await self._check_run_status(repo, str(latest_run["id"]))


class DockerBuildChecker(ExternalStatusChecker):
    """Checker for Docker build operations."""
    
    @property
    def name(self) -> str:
        return "DockerBuildChecker"
    
    def can_handle(self, operation_type: str) -> bool:
        return operation_type in ["docker_build", "docker_push"]
    
    async def check_status(self, operation: ExternalOperation) -> StatusCheckResult:
        """Check Docker build status via registry API or build logs."""
        try:
            metadata = operation.metadata
            registry = metadata.get("registry", "docker.io")
            image_name = metadata.get("image_name")
            tag = metadata.get("tag", "latest")
            
            if not image_name:
                return StatusCheckResult(
                    status=ExternalStatus.UNKNOWN,
                    message="Image name not specified",
                    details={},
                    is_final=True
                )
            
            # Check if image exists in registry
            return await self._check_image_exists(registry, image_name, tag)
            
        except Exception as e:
            logger.error(f"Error checking Docker build status: {e}")
            return StatusCheckResult(
                status=ExternalStatus.UNKNOWN,
                message=f"Error checking Docker status: {str(e)}",
                details={"error": str(e)},
                is_final=False,
                next_check_in=60
            )
    
    async def _check_image_exists(self, registry: str, image_name: str, tag: str) -> StatusCheckResult:
        """Check if Docker image exists in registry."""
        if registry == "docker.io":
            # Docker Hub API
            url = f"https://hub.docker.com/v2/repositories/{image_name}/tags/{tag}"
        else:
            # Generic registry API (v2)
            url = f"https://{registry}/v2/{image_name}/manifests/{tag}"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.head(url, timeout=10.0)
                
                if response.status_code == 200:
                    return StatusCheckResult(
                        status=ExternalStatus.SUCCESS,
                        message=f"Image {image_name}:{tag} found in registry",
                        details={
                            "registry": registry,
                            "image": image_name,
                            "tag": tag
                        },
                        is_final=True
                    )
                elif response.status_code == 404:
                    return StatusCheckResult(
                        status=ExternalStatus.IN_PROGRESS,
                        message=f"Image {image_name}:{tag} not yet available",
                        details={},
                        is_final=False,
                        next_check_in=30
                    )
                else:
                    return StatusCheckResult(
                        status=ExternalStatus.UNKNOWN,
                        message=f"Unexpected response: {response.status_code}",
                        details={"status_code": response.status_code},
                        is_final=False,
                        next_check_in=60
                    )
                    
            except httpx.RequestError:
                return StatusCheckResult(
                    status=ExternalStatus.IN_PROGRESS,
                    message="Registry not accessible, build may be in progress",
                    details={},
                    is_final=False,
                    next_check_in=30
                )


class WebhookChecker(ExternalStatusChecker):
    """Checker for webhook-based status updates."""
    
    def __init__(self):
        self.pending_operations: Dict[str, ExternalOperation] = {}
        self.status_updates: Dict[str, StatusCheckResult] = {}
    
    @property
    def name(self) -> str:
        return "WebhookChecker"
    
    def can_handle(self, operation_type: str) -> bool:
        return operation_type in ["webhook", "custom_deployment"]
    
    async def check_status(self, operation: ExternalOperation) -> StatusCheckResult:
        """Check status via webhook updates."""
        operation_id = operation.operation_id
        
        # Store operation for webhook updates
        self.pending_operations[operation_id] = operation
        
        # Check if we have a status update
        if operation_id in self.status_updates:
            result = self.status_updates[operation_id]
            if result.is_final:
                # Clean up completed operations
                self.pending_operations.pop(operation_id, None)
                self.status_updates.pop(operation_id, None)
            return result
        
        # No update yet, still pending
        elapsed = time.time() - operation.started_at
        if elapsed > operation.max_wait_time:
            return StatusCheckResult(
                status=ExternalStatus.FAILURE,
                message="Operation timed out waiting for webhook",
                details={"elapsed_time": elapsed},
                is_final=True
            )
        
        return StatusCheckResult(
            status=ExternalStatus.IN_PROGRESS,
            message="Waiting for webhook update",
            details={"elapsed_time": elapsed},
            is_final=False,
            next_check_in=30
        )
    
    def receive_webhook_update(self, operation_id: str, status: ExternalStatus, 
                              message: str, details: Dict[str, Any] = None) -> None:
        """Receive a webhook status update."""
        if details is None:
            details = {}
            
        self.status_updates[operation_id] = StatusCheckResult(
            status=status,
            message=message,
            details=details,
            is_final=status in {ExternalStatus.SUCCESS, ExternalStatus.FAILURE, ExternalStatus.CANCELLED}
        )


class ExternalStatusManager:
    """
    Manager for monitoring external operations.
    
    Coordinates multiple checker types and provides a unified interface
    for tracking external operations until completion.
    """
    
    def __init__(self, github_token: Optional[str] = None):
        self.checkers: List[ExternalStatusChecker] = [
            GitHubActionsChecker(github_token),
            DockerBuildChecker(),
            WebhookChecker(),
        ]
        
        self.active_operations: Dict[str, ExternalOperation] = {}
        self.check_tasks: Dict[str, asyncio.Task] = {}
        
        logger.info(f"ExternalStatusManager initialized with {len(self.checkers)} checkers")
    
    def start_monitoring(self, operation: ExternalOperation) -> None:
        """Start monitoring an external operation."""
        self.active_operations[operation.operation_id] = operation
        
        # Start async monitoring task
        task = asyncio.create_task(self._monitor_operation(operation))
        self.check_tasks[operation.operation_id] = task
        
        logger.info(f"Started monitoring {operation.operation_type} operation {operation.operation_id}")
    
    def stop_monitoring(self, operation_id: str) -> None:
        """Stop monitoring an operation."""
        if operation_id in self.check_tasks:
            self.check_tasks[operation_id].cancel()
            del self.check_tasks[operation_id]
        
        self.active_operations.pop(operation_id, None)
        logger.info(f"Stopped monitoring operation {operation_id}")
    
    async def _monitor_operation(self, operation: ExternalOperation) -> StatusCheckResult:
        """Monitor a single operation until completion."""
        checker = self._get_checker_for_operation(operation.operation_type)
        if not checker:
            logger.error(f"No checker found for operation type: {operation.operation_type}")
            return StatusCheckResult(
                status=ExternalStatus.UNKNOWN,
                message=f"No checker available for {operation.operation_type}",
                details={},
                is_final=True
            )
        
        start_time = time.time()
        
        while True:
            try:
                result = await checker.check_status(operation)
                
                logger.debug(f"Operation {operation.operation_id}: {result.status}")
                
                # Check if operation is complete
                if result.is_final:
                    logger.info(f"Operation {operation.operation_id} completed: {result.status}")
                    return result
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > operation.max_wait_time:
                    logger.warning(f"Operation {operation.operation_id} timed out after {elapsed}s")
                    return StatusCheckResult(
                        status=ExternalStatus.FAILURE,
                        message=f"Operation timed out after {elapsed:.0f} seconds",
                        details={"elapsed_time": elapsed},
                        is_final=True
                    )
                
                # Wait before next check
                wait_time = result.next_check_in or operation.check_interval
                await asyncio.sleep(wait_time)
                
            except asyncio.CancelledError:
                logger.info(f"Monitoring cancelled for operation {operation.operation_id}")
                raise
            except Exception as e:
                logger.error(f"Error monitoring operation {operation.operation_id}: {e}")
                await asyncio.sleep(60)  # Wait before retry
    
    def _get_checker_for_operation(self, operation_type: str) -> Optional[ExternalStatusChecker]:
        """Get the appropriate checker for an operation type."""
        for checker in self.checkers:
            if checker.can_handle(operation_type):
                return checker
        return None
    
    def get_operation_status(self, operation_id: str) -> Optional[ExternalOperation]:
        """Get current status of an operation."""
        return self.active_operations.get(operation_id)
    
    def get_all_operations(self) -> List[ExternalOperation]:
        """Get all active operations."""
        return list(self.active_operations.values())
    
    async def wait_for_operation(self, operation_id: str, timeout: Optional[float] = None) -> StatusCheckResult:
        """Wait for a specific operation to complete."""
        if operation_id not in self.check_tasks:
            raise ValueError(f"Operation {operation_id} is not being monitored")
        
        task = self.check_tasks[operation_id]
        try:
            result = await asyncio.wait_for(task, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return StatusCheckResult(
                status=ExternalStatus.FAILURE,
                message=f"Timeout waiting for operation {operation_id}",
                details={},
                is_final=True
            )


def parse_github_workflow_from_output(output: str) -> Optional[Dict[str, str]]:
    """
    Parse GitHub workflow information from command output.
    
    Extracts repository and run ID from common CLI output patterns.
    """
    patterns = [
        # gh workflow run output
        r'https://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)',
        # Direct run URL
        r'github\.com/([^/]+/[^/]+)/actions/runs/(\d+)',
        # Workflow run started message
        r'workflow run (\d+) started.*github\.com/([^/]+/[^/]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            if "github.com" in pattern:
                repo, run_id = match.groups()
            else:
                run_id, repo = match.groups()
            return {
                "repository": repo,
                "run_id": run_id,
                "operation_type": "github_action"
            }
    
    return None