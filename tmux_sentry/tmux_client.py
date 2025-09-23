"""
tmux SSH client for remote session management.
Provides secure, reliable tmux session interaction.
"""

import re
import time
import logging
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
import paramiko

logger = logging.getLogger(__name__)


@dataclass
class TmuxPane:
    """Represents a tmux pane."""
    pane_id: str
    session_name: str
    window_name: str
    window_index: int
    pane_index: int
    pane_title: str
    is_active: bool
    width: int
    height: int


@dataclass
class TmuxSession:
    """Represents a tmux session."""
    name: str
    windows: List[str]
    panes: List[TmuxPane]
    created: str
    last_attached: str


class TmuxClient:
    """
    SSH-based tmux client.
    
    Provides secure remote tmux session management with automatic
    connection handling and error recovery.
    """
    
    def __init__(self, 
                 host: str,
                 port: int = 22,
                 username: str = "root",
                 key_filename: Optional[str] = None,
                 password: Optional[str] = None,
                 timeout: int = 30):
        
        self.host = host
        self.port = port
        self.username = username
        self.key_filename = key_filename
        self.password = password
        self.timeout = timeout
        
        self._ssh_client: Optional[paramiko.SSHClient] = None
        self._connected = False
        
        logger.info(f"TmuxClient initialized for {username}@{host}:{port}")
    
    def connect(self) -> bool:
        """Establish SSH connection."""
        try:
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                'hostname': self.host,
                'port': self.port,
                'username': self.username,
                'timeout': self.timeout,
            }
            
            if self.key_filename:
                connect_kwargs['key_filename'] = self.key_filename
            elif self.password:
                connect_kwargs['password'] = self.password
            else:
                # Try default SSH agent
                connect_kwargs['look_for_keys'] = True
            
            self._ssh_client.connect(**connect_kwargs)
            self._connected = True
            
            logger.info(f"Connected to {self.host}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to {self.host}: {e}")
            self._connected = False
            return False
    
    def disconnect(self) -> None:
        """Close SSH connection."""
        if self._ssh_client:
            self._ssh_client.close()
            self._ssh_client = None
        self._connected = False
        logger.info(f"Disconnected from {self.host}")
    
    def is_connected(self) -> bool:
        """Check if SSH connection is active."""
        if not self._connected or not self._ssh_client:
            return False
        
        try:
            # Test connection with a simple command
            self._execute_command("echo test", timeout=5)
            return True
        except:
            self._connected = False
            return False
    
    def _execute_command(self, command: str, timeout: int = 30) -> Tuple[str, str, int]:
        """Execute SSH command and return stdout, stderr, exit_code."""
        if not self.is_connected():
            if not self.connect():
                raise ConnectionError(f"Cannot connect to {self.host}")
        
        try:
            stdin, stdout, stderr = self._ssh_client.exec_command(command, timeout=timeout)
            
            stdout_data = stdout.read().decode('utf-8', errors='replace')
            stderr_data = stderr.read().decode('utf-8', errors='replace')
            exit_code = stdout.channel.recv_exit_status()
            
            return stdout_data, stderr_data, exit_code
            
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            self._connected = False
            raise
    
    def list_sessions(self, socket: str = "default") -> List[TmuxSession]:
        """List all tmux sessions."""
        try:
            cmd = f"tmux -S {socket} list-sessions -F '{{session_name}}|{{session_created}}|{{session_last_attached}}'"
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                if "no server running" in stderr:
                    logger.debug("No tmux server running")
                    return []
                else:
                    logger.error(f"Failed to list sessions: {stderr}")
                    return []
            
            sessions = []
            for line in stdout.strip().split('\n'):
                if not line:
                    continue
                    
                parts = line.split('|')
                if len(parts) >= 3:
                    session_name = parts[0]
                    created = parts[1]
                    last_attached = parts[2]
                    
                    # Get panes for this session
                    panes = self.list_panes(session_name, socket)
                    
                    # Extract unique window names
                    windows = list(set(pane.window_name for pane in panes))
                    
                    sessions.append(TmuxSession(
                        name=session_name,
                        windows=windows,
                        panes=panes,
                        created=created,
                        last_attached=last_attached
                    ))
            
            return sessions
            
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            return []
    
    def list_panes(self, session_name: Optional[str] = None, socket: str = "default") -> List[TmuxPane]:
        """List panes in a session (or all sessions if session_name is None)."""
        try:
            target = f"-t {session_name}" if session_name else ""
            cmd = f"tmux -S {socket} list-panes -a {target} -F '{{pane_id}}|{{session_name}}|{{window_name}}|{{window_index}}|{{pane_index}}|{{pane_title}}|{{pane_active}}|{{pane_width}}|{{pane_height}}'"
            
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to list panes: {stderr}")
                return []
            
            panes = []
            for line in stdout.strip().split('\n'):
                if not line:
                    continue
                    
                parts = line.split('|')
                if len(parts) >= 9:
                    panes.append(TmuxPane(
                        pane_id=parts[0],
                        session_name=parts[1],
                        window_name=parts[2],
                        window_index=int(parts[3]),
                        pane_index=int(parts[4]),
                        pane_title=parts[5],
                        is_active=parts[6] == '1',
                        width=int(parts[7]),
                        height=int(parts[8])
                    ))
            
            return panes
            
        except Exception as e:
            logger.error(f"Error listing panes: {e}")
            return []
    
    def capture_pane(self, 
                    pane_id: str, 
                    socket: str = "default",
                    start_line: Optional[int] = None,
                    end_line: Optional[int] = None) -> str:
        """Capture pane content."""
        try:
            cmd = f"tmux -S {socket} capture-pane -t {pane_id} -p"
            
            if start_line is not None:
                cmd += f" -S {start_line}"
            if end_line is not None:
                cmd += f" -E {end_line}"
            
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to capture pane {pane_id}: {stderr}")
                return ""
            
            return stdout
            
        except Exception as e:
            logger.error(f"Error capturing pane {pane_id}: {e}")
            return ""
    
    def send_keys(self, 
                  pane_id: str,
                  keys: str,
                  socket: str = "default",
                  enter: bool = True) -> bool:
        """Send keys to a pane."""
        try:
            # Escape special characters
            escaped_keys = keys.replace('"', '\\"')
            
            cmd = f'tmux -S {socket} send-keys -t {pane_id} "{escaped_keys}"'
            if enter:
                cmd += " Enter"
            
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to send keys to pane {pane_id}: {stderr}")
                return False
            
            logger.debug(f"Sent keys to {pane_id}: {keys}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending keys to pane {pane_id}: {e}")
            return False
    
    def get_pane_info(self, pane_id: str, socket: str = "default") -> Optional[Dict[str, str]]:
        """Get detailed pane information."""
        try:
            cmd = f"tmux -S {socket} display-message -t {pane_id} -p '{{pane_id}}|{{session_name}}|{{window_name}}|{{pane_title}}|{{pane_current_command}}|{{pane_pid}}'"
            
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to get pane info for {pane_id}: {stderr}")
                return None
            
            parts = stdout.strip().split('|')
            if len(parts) >= 6:
                return {
                    'pane_id': parts[0],
                    'session_name': parts[1],
                    'window_name': parts[2],
                    'pane_title': parts[3],
                    'current_command': parts[4],
                    'pid': parts[5]
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting pane info for {pane_id}: {e}")
            return None
    
    def filter_panes(self, 
                    panes: List[TmuxPane],
                    session_filters: List[str] = None,
                    pane_name_patterns: List[str] = None) -> List[TmuxPane]:
        """Filter panes based on session and name patterns."""
        filtered_panes = panes
        
        # Filter by session name
        if session_filters:
            session_regexes = [re.compile(pattern) for pattern in session_filters]
            filtered_panes = [
                pane for pane in filtered_panes
                if any(regex.match(pane.session_name) for regex in session_regexes)
            ]
        
        # Filter by pane title/name patterns
        if pane_name_patterns:
            name_regexes = [re.compile(pattern) for pattern in pane_name_patterns]
            filtered_panes = [
                pane for pane in filtered_panes
                if any(
                    regex.match(pane.pane_title) or regex.match(pane.window_name)
                    for regex in name_regexes
                )
            ]
        
        return filtered_panes
    
    def create_session(self, session_name: str, socket: str = "default") -> bool:
        """Create a new tmux session."""
        try:
            cmd = f"tmux -S {socket} new-session -d -s {session_name}"
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to create session {session_name}: {stderr}")
                return False
            
            logger.info(f"Created tmux session: {session_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating session {session_name}: {e}")
            return False
    
    def kill_session(self, session_name: str, socket: str = "default") -> bool:
        """Kill a tmux session."""
        try:
            cmd = f"tmux -S {socket} kill-session -t {session_name}"
            stdout, stderr, exit_code = self._execute_command(cmd)
            
            if exit_code != 0:
                logger.error(f"Failed to kill session {session_name}: {stderr}")
                return False
            
            logger.info(f"Killed tmux session: {session_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error killing session {session_name}: {e}")
            return False
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()