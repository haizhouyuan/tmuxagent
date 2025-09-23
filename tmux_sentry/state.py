"""
SQLite-based state persistence system.
This solves the critical issue of losing state on NAS restarts.
"""

import sqlite3
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import logging

logger = logging.getLogger(__name__)


@dataclass
class PaneState:
    """Represents the state of a tmux pane."""
    pane_id: str
    session_name: str
    window_name: str
    current_stage: str
    stage_status: str  # IDLE, RUNNING, WAIT_APPROVAL, COMPLETED, FAILED
    pipeline_name: str
    last_output_line: int
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]


@dataclass  
class StageHistory:
    """Represents the history of a stage execution."""
    pane_id: str
    stage_name: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime]
    exit_code: Optional[int]
    error_message: Optional[str]
    metadata: Dict[str, Any]


class StateManager:
    """
    Persistent state manager using SQLite.
    
    Provides atomic operations and automatic backup for reliable state management.
    Critical for NAS environments where restarts are frequent.
    """
    
    def __init__(self, db_path: Union[str, Path], backup_interval: int = 300):
        self.db_path = Path(db_path).expanduser()
        self.backup_interval = backup_interval
        self._lock = threading.RLock()
        self._last_backup = 0
        
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_database()
        logger.info(f"StateManager initialized with database: {self.db_path}")
    
    def _init_database(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            # Pane states table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pane_states (
                    pane_id TEXT PRIMARY KEY,
                    session_name TEXT NOT NULL,
                    window_name TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    stage_status TEXT NOT NULL,
                    pipeline_name TEXT NOT NULL,
                    last_output_line INTEGER DEFAULT 0,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    metadata TEXT NOT NULL
                )
            """)
            
            # Stage history table  
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pane_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    exit_code INTEGER,
                    error_message TEXT,
                    metadata TEXT NOT NULL,
                    FOREIGN KEY (pane_id) REFERENCES pane_states (pane_id)
                )
            """)
            
            # Approval requests table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pane_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    request_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    requested_at TIMESTAMP NOT NULL,
                    responded_at TIMESTAMP,
                    response TEXT,
                    token TEXT UNIQUE,
                    FOREIGN KEY (pane_id) REFERENCES pane_states (pane_id)
                )
            """)
            
            # Create indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pane_states_session ON pane_states(session_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stage_history_pane ON stage_history(pane_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status)")
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with proper error handling."""
        conn = None
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")  # Better for concurrent access
            conn.execute("PRAGMA synchronous=NORMAL")  # Good balance of speed/safety
            conn.row_factory = sqlite3.Row  # Enable column access by name
            yield conn
        finally:
            if conn:
                conn.close()
    
    def save_pane_state(self, state: PaneState) -> None:
        """Save or update pane state atomically."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO pane_states 
                    (pane_id, session_name, window_name, current_stage, stage_status,
                     pipeline_name, last_output_line, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    state.pane_id,
                    state.session_name, 
                    state.window_name,
                    state.current_stage,
                    state.stage_status,
                    state.pipeline_name,
                    state.last_output_line,
                    state.created_at.isoformat(),
                    state.updated_at.isoformat(),
                    json.dumps(state.metadata)
                ))
                conn.commit()
                
        self._maybe_backup()
        logger.debug(f"Saved state for pane {state.pane_id}")
    
    def get_pane_state(self, pane_id: str) -> Optional[PaneState]:
        """Retrieve pane state by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM pane_states WHERE pane_id = ?", 
                (pane_id,)
            ).fetchone()
            
            if not row:
                return None
                
            return PaneState(
                pane_id=row['pane_id'],
                session_name=row['session_name'],
                window_name=row['window_name'], 
                current_stage=row['current_stage'],
                stage_status=row['stage_status'],
                pipeline_name=row['pipeline_name'],
                last_output_line=row['last_output_line'],
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at']),
                metadata=json.loads(row['metadata'])
            )
    
    def get_all_pane_states(self) -> List[PaneState]:
        """Get all pane states."""
        states = []
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM pane_states ORDER BY updated_at DESC").fetchall()
            
            for row in rows:
                states.append(PaneState(
                    pane_id=row['pane_id'],
                    session_name=row['session_name'],
                    window_name=row['window_name'],
                    current_stage=row['current_stage'], 
                    stage_status=row['stage_status'],
                    pipeline_name=row['pipeline_name'],
                    last_output_line=row['last_output_line'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at']),
                    metadata=json.loads(row['metadata'])
                ))
        return states
    
    def delete_pane_state(self, pane_id: str) -> None:
        """Delete pane state and related data."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM approval_requests WHERE pane_id = ?", (pane_id,))
                conn.execute("DELETE FROM stage_history WHERE pane_id = ?", (pane_id,))
                conn.execute("DELETE FROM pane_states WHERE pane_id = ?", (pane_id,))
                conn.commit()
                
        logger.info(f"Deleted state for pane {pane_id}")
    
    def add_stage_history(self, history: StageHistory) -> None:
        """Add stage execution history."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO stage_history 
                (pane_id, stage_name, status, started_at, completed_at, 
                 exit_code, error_message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                history.pane_id,
                history.stage_name,
                history.status,
                history.started_at.isoformat(),
                history.completed_at.isoformat() if history.completed_at else None,
                history.exit_code,
                history.error_message,
                json.dumps(history.metadata)
            ))
            conn.commit()
    
    def get_stage_history(self, pane_id: str, limit: int = 50) -> List[StageHistory]:
        """Get stage history for a pane."""
        history = []
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM stage_history 
                WHERE pane_id = ? 
                ORDER BY started_at DESC 
                LIMIT ?
            """, (pane_id, limit)).fetchall()
            
            for row in rows:
                history.append(StageHistory(
                    pane_id=row['pane_id'],
                    stage_name=row['stage_name'],
                    status=row['status'],
                    started_at=datetime.fromisoformat(row['started_at']),
                    completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
                    exit_code=row['exit_code'],
                    error_message=row['error_message'],
                    metadata=json.loads(row['metadata'])
                ))
        return history
    
    def update_stage_status(self, pane_id: str, stage: str, status: str, 
                           exit_code: Optional[int] = None, 
                           error_message: Optional[str] = None) -> None:
        """Update stage status for a pane."""
        now = datetime.now()
        
        with self._lock:
            # Update pane state
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE pane_states 
                    SET current_stage = ?, stage_status = ?, updated_at = ?
                    WHERE pane_id = ?
                """, (stage, status, now.isoformat(), pane_id))
                
                # Update stage history if completed
                if status in ('COMPLETED', 'FAILED'):
                    conn.execute("""
                        UPDATE stage_history 
                        SET status = ?, completed_at = ?, exit_code = ?, error_message = ?
                        WHERE pane_id = ? AND stage_name = ? AND completed_at IS NULL
                    """, (status, now.isoformat(), exit_code, error_message, pane_id, stage))
                
                conn.commit()
    
    def _maybe_backup(self) -> None:
        """Create backup if enough time has passed."""
        now = time.time()
        if now - self._last_backup > self.backup_interval:
            self._create_backup()
            self._last_backup = now
    
    def _create_backup(self) -> None:
        """Create a backup of the database."""
        try:
            backup_path = self.db_path.with_suffix(f'.backup.{int(time.time())}')
            
            with self._get_connection() as source:
                backup_conn = sqlite3.connect(backup_path)
                source.backup(backup_conn)
                backup_conn.close()
                
            # Keep only last 5 backups
            backup_files = sorted(
                self.db_path.parent.glob(f"{self.db_path.stem}.backup.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            
            for old_backup in backup_files[5:]:
                old_backup.unlink()
                
            logger.debug(f"Created backup: {backup_path}")
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics."""
        with self._get_connection() as conn:
            stats = {}
            
            # Pane counts
            stats['total_panes'] = conn.execute("SELECT COUNT(*) FROM pane_states").fetchone()[0]
            stats['active_panes'] = conn.execute(
                "SELECT COUNT(*) FROM pane_states WHERE stage_status NOT IN ('COMPLETED', 'FAILED')"
            ).fetchone()[0]
            
            # Stage statistics  
            stats['total_stages'] = conn.execute("SELECT COUNT(*) FROM stage_history").fetchone()[0]
            stats['pending_approvals'] = conn.execute(
                "SELECT COUNT(*) FROM approval_requests WHERE status = 'PENDING'"
            ).fetchone()[0]
            
            # Database size
            stats['db_size_bytes'] = self.db_path.stat().st_size
            
        return stats