#!/usr/bin/env python3
"""
tmux-sentry main entry point.
CLI interface for the intelligent tmux monitoring system.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .config import ConfigManager, Config
from .state import StateManager, PaneState
from .parser import SmartLogParser
from .tmux_client import TmuxClient
from .notify import NotificationManager
from .approval import ApprovalManager, ApprovalRequest
from .external import ExternalStatusManager

console = Console()
logger = logging.getLogger(__name__)


class TmuxSentryEngine:
    """Main engine for tmux monitoring and automation."""
    
    def __init__(self, config: Config):
        self.config = config
        self.running = False
        
        # Initialize components
        self.state_manager = StateManager(config.database.path)
        self.parser = SmartLogParser()
        self.notification_manager = NotificationManager()
        self.approval_manager = ApprovalManager(
            secret_key=config.security.approval_secret,
            token_ttl=config.security.approval_token_ttl
        )
        self.external_manager = ExternalStatusManager()
        
        # Track tmux clients
        self.tmux_clients: Dict[str, TmuxClient] = {}
        
        logger.info("TmuxSentryEngine initialized")
    
    async def start(self) -> None:
        """Start the monitoring engine."""
        logger.info("Starting tmux-sentry engine...")
        
        # Initialize tmux clients
        for host_config in self.config.hosts:
            client = TmuxClient(
                host=host_config.ssh.host,
                port=host_config.ssh.port,
                username=host_config.ssh.user,
                key_filename=host_config.ssh.key_path,
                password=host_config.ssh.password,
                timeout=host_config.ssh.timeout
            )
            
            if client.connect():
                self.tmux_clients[host_config.name] = client
                logger.info(f"Connected to host: {host_config.name}")
            else:
                logger.error(f"Failed to connect to host: {host_config.name}")
        
        if not self.tmux_clients:
            logger.error("No tmux clients connected. Exiting.")
            return
        
        # Restore state from database
        await self._restore_state()
        
        # Start main monitoring loop
        self.running = True
        await self._monitoring_loop()
    
    async def stop(self) -> None:
        """Stop the monitoring engine."""
        logger.info("Stopping tmux-sentry engine...")
        self.running = False
        
        # Disconnect tmux clients
        for client in self.tmux_clients.values():
            client.disconnect()
        
        # Clean up approval requests
        self.approval_manager.cleanup_expired_requests()
    
    async def _restore_state(self) -> None:
        """Restore state from persistent storage."""
        logger.info("Restoring state from database...")
        
        states = self.state_manager.get_all_pane_states()
        logger.info(f"Restored {len(states)} pane states")
        
        # TODO: Resume monitoring for active panes
        # This would involve checking which panes are still active
        # and continuing their pipeline execution
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        logger.info("Starting monitoring loop...")
        
        while self.running:
            try:
                # Monitor all configured hosts
                for host_name, client in self.tmux_clients.items():
                    await self._monitor_host(host_name, client)
                
                # Process approval requests
                await self._process_approvals()
                
                # Clean up expired data
                await self._cleanup()
                
                # Wait before next iteration
                await asyncio.sleep(1.0)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5.0)  # Wait before retrying
    
    async def _monitor_host(self, host_name: str, client: TmuxClient) -> None:
        """Monitor a single host."""
        host_config = next(h for h in self.config.hosts if h.name == host_name)
        
        try:
            # Get all panes
            all_panes = client.list_panes(socket=host_config.tmux.socket)
            
            # Filter panes based on configuration
            filtered_panes = client.filter_panes(
                all_panes,
                session_filters=host_config.tmux.session_filters,
                pane_name_patterns=host_config.tmux.pane_name_patterns
            )
            
            # Monitor each filtered pane
            for pane in filtered_panes:
                await self._monitor_pane(host_name, client, pane, host_config)
                
        except Exception as e:
            logger.error(f"Error monitoring host {host_name}: {e}")
    
    async def _monitor_pane(self, host_name: str, client: TmuxClient, pane, host_config) -> None:
        """Monitor a single pane."""
        pane_id = pane.pane_id
        
        try:
            # Get or create pane state
            state = self.state_manager.get_pane_state(pane_id)
            if not state:
                # Create new state
                state = PaneState(
                    pane_id=pane_id,
                    session_name=pane.session_name,
                    window_name=pane.window_name,
                    current_stage="idle",
                    stage_status="IDLE",
                    pipeline_name="",
                    last_output_line=0,
                    created_at=time.time(),
                    updated_at=time.time(),
                    metadata={}
                )
            
            # Capture pane output
            output = client.capture_pane(
                pane_id,
                socket=host_config.tmux.socket
            )
            
            if not output:
                return
            
            # Parse new output
            lines = output.splitlines()
            new_lines = lines[state.last_output_line:]
            
            if new_lines:
                # Update last output line
                state.last_output_line = len(lines)
                state.updated_at = time.time()
                self.state_manager.save_pane_state(state)
                
                # Parse for SENTRY messages
                messages = self.parser.parse_lines(new_lines)
                
                if messages:
                    await self._process_messages(pane_id, messages)
                
        except Exception as e:
            logger.error(f"Error monitoring pane {pane_id}: {e}")
    
    async def _process_messages(self, pane_id: str, messages: List) -> None:
        """Process parsed messages from a pane."""
        for message in messages:
            try:
                if message.type == "STATUS":
                    await self._handle_status_message(pane_id, message)
                elif message.type == "ERROR":
                    await self._handle_error_message(pane_id, message)
                elif message.type == "ASK":
                    await self._handle_ask_message(pane_id, message)
                    
            except Exception as e:
                logger.error(f"Error processing message for pane {pane_id}: {e}")
    
    async def _handle_status_message(self, pane_id: str, message) -> None:
        """Handle a status message."""
        if message.stage:
            self.state_manager.update_stage_status(
                pane_id,
                message.stage,
                "COMPLETED" if message.ok else "FAILED"
            )
            
            # Send notification
            await self.notification_manager.send_stage_notification(
                pane_id=pane_id,
                stage=message.stage,
                status="COMPLETED" if message.ok else "FAILED",
                message=message.summary or message.detail or "Stage completed"
            )
    
    async def _handle_error_message(self, pane_id: str, message) -> None:
        """Handle an error message."""
        if message.stage:
            self.state_manager.update_stage_status(
                pane_id,
                message.stage,
                "FAILED",
                error_message=message.detail
            )
            
            # Send error notification
            await self.notification_manager.send_stage_notification(
                pane_id=pane_id,
                stage=message.stage,
                status="FAILED",
                message=message.detail or "Stage failed",
                details={"error": True}
            )
    
    async def _handle_ask_message(self, pane_id: str, message) -> None:
        """Handle an ask message (approval request)."""
        request = ApprovalRequest(
            pane_id=pane_id,
            stage_name=message.stage or "unknown",
            request_type="ask_human",
            message=message.question or "Approval required",
            options=message.options or ["approve", "reject"]
        )
        
        # Create approval request
        token = self.approval_manager.create_approval_request(request)
        
        # Send approval notification
        await self.notification_manager.send_approval_notification(
            pane_id=pane_id,
            stage=request.stage_name,
            message=request.message
        )
    
    async def _process_approvals(self) -> None:
        """Process pending approval requests."""
        for request_id, request in self.approval_manager.get_pending_requests().items():
            response = self.approval_manager.check_approval_response(request_id)
            
            if response:
                logger.info(f"Approval response for {request.pane_id}: {response}")
                
                # Update pane state based on response
                if response == "approve":
                    self.state_manager.update_stage_status(
                        request.pane_id,
                        request.stage_name,
                        "RUNNING"
                    )
                else:
                    self.state_manager.update_stage_status(
                        request.pane_id,
                        request.stage_name,
                        "CANCELLED"
                    )
    
    async def _cleanup(self) -> None:
        """Perform periodic cleanup."""
        # Clean up expired approval requests
        self.approval_manager.cleanup_expired_requests()


# CLI Commands

@click.group()
@click.option('--log-level', default='INFO', help='Log level')
@click.option('--config-dir', type=click.Path(), help='Configuration directory')
@click.pass_context
def cli(ctx, log_level, config_dir):
    """tmux-sentry: Intelligent tmux session monitoring and automation."""
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)]
    )
    
    ctx.ensure_object(dict)
    ctx.obj['config_dir'] = Path(config_dir) if config_dir else Path.cwd()


@cli.command()
@click.option('--hosts', required=True, type=click.Path(exists=True), help='Hosts configuration file')
@click.option('--policy', required=True, type=click.Path(exists=True), help='Policy configuration file')
@click.option('--env-file', type=click.Path(), help='Environment file')
@click.pass_context
def run(ctx, hosts, policy, env_file):
    """Start monitoring tmux sessions."""
    try:
        # Load configuration
        config_manager = ConfigManager()
        config = config_manager.load_config(hosts, policy, env_file)
        
        # Validate configuration
        issues = config_manager.validate_config(config)
        if issues:
            console.print("[red]Configuration issues found:[/red]")
            for issue in issues:
                console.print(f"  • {issue}")
            sys.exit(1)
        
        # Start engine
        engine = TmuxSentryEngine(config)
        
        console.print("[green]Starting tmux-sentry...[/green]")
        asyncio.run(engine.start())
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option('--hosts', required=True, type=click.Path(exists=True), help='Hosts configuration file')
@click.pass_context
def status(ctx, hosts):
    """Show current monitoring status."""
    try:
        config_manager = ConfigManager()
        # Load minimal config for status check
        hosts_data = config_manager._load_yaml_file(hosts)
        
        table = Table(title="tmux-sentry Status")
        table.add_column("Host", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Sessions", justify="right")
        table.add_column("Panes", justify="right")
        
        for host_data in hosts_data.get('hosts', []):
            ssh_config = host_data.get('ssh', {})
            tmux_config = host_data.get('tmux', {})
            
            try:
                client = TmuxClient(
                    host=ssh_config['host'],
                    port=ssh_config.get('port', 22),
                    username=ssh_config.get('user', 'root'),
                    key_filename=ssh_config.get('key'),
                    timeout=10
                )
                
                if client.connect():
                    sessions = client.list_sessions(tmux_config.get('socket', 'default'))
                    all_panes = client.list_panes(socket=tmux_config.get('socket', 'default'))
                    filtered_panes = client.filter_panes(
                        all_panes,
                        session_filters=tmux_config.get('session_filters', []),
                        pane_name_patterns=tmux_config.get('pane_name_patterns', [])
                    )
                    
                    table.add_row(
                        host_data['name'],
                        "[green]Connected[/green]",
                        str(len(sessions)),
                        f"{len(filtered_panes)}/{len(all_panes)}"
                    )
                    client.disconnect()
                else:
                    table.add_row(
                        host_data['name'],
                        "[red]Failed[/red]",
                        "-",
                        "-"
                    )
                    
            except Exception as e:
                table.add_row(
                    host_data['name'],
                    f"[red]Error: {e}[/red]",
                    "-",
                    "-"
                )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option('--output', type=click.Path(), help='Output file path')
def init(output):
    """Initialize configuration templates."""
    output_dir = Path(output) if output else Path.cwd()
    
    config_manager = ConfigManager()
    
    # Create hosts template
    hosts_file = output_dir / "hosts.example.yaml"
    config_manager.save_config_template(hosts_file)
    
    console.print(f"[green]Created configuration template: {hosts_file}[/green]")
    console.print("Edit the template and rename to hosts.yaml")


if __name__ == "__main__":
    cli()