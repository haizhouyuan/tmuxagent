"""
Integration tests for tmux-sentry.
Tests the complete workflow end-to-end.
"""

import pytest
import asyncio
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from tmux_sentry.state import StateManager, PaneState
from tmux_sentry.config import ConfigManager, Config
from tmux_sentry.parser import SmartLogParser
from tmux_sentry.approval import ApprovalManager
from tmux_sentry.notify import NotificationManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    yield db_path
    
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def state_manager(temp_db):
    """Create a state manager with temporary database."""
    return StateManager(temp_db)


@pytest.fixture
def config_manager():
    """Create a config manager for testing."""
    return ConfigManager()


@pytest.fixture
def sample_config_data():
    """Sample configuration data."""
    return {
        'hosts': [
            {
                'name': 'test-host',
                'ssh': {
                    'host': 'localhost',
                    'port': 22,
                    'user': 'test',
                    'key': '/tmp/test_key'
                },
                'tmux': {
                    'socket': 'default',
                    'session_filters': ['^proj:'],
                    'pane_name_patterns': ['^codex'],
                    'capture_lines': 100,
                    'poll_interval_ms': 1000
                }
            }
        ]
    }


class TestStateManagerIntegration:
    """Test state manager database operations."""
    
    def test_state_persistence(self, state_manager):
        """Test that state persists across manager instances."""
        from datetime import datetime
        
        # Create and save a state
        state = PaneState(
            pane_id="%1",
            session_name="test-session",
            window_name="test-window",
            current_stage="build",
            stage_status="RUNNING",
            pipeline_name="test-pipeline",
            last_output_line=10,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            metadata={"test": "data"}
        )
        
        state_manager.save_pane_state(state)
        
        # Create new manager instance and verify state exists
        new_manager = StateManager(state_manager.db_path)
        restored_state = new_manager.get_pane_state("%1")
        
        assert restored_state is not None
        assert restored_state.pane_id == "%1"
        assert restored_state.session_name == "test-session"
        assert restored_state.current_stage == "build"
        assert restored_state.metadata["test"] == "data"
    
    def test_stage_history_tracking(self, state_manager):
        """Test stage history is properly tracked."""
        from datetime import datetime
        from tmux_sentry.state import StageHistory
        
        # Add stage history
        history = StageHistory(
            pane_id="%1",
            stage_name="test",
            status="COMPLETED",
            started_at=datetime.now(),
            completed_at=datetime.now(),
            exit_code=0,
            error_message=None,
            metadata={"duration": 5.2}
        )
        
        state_manager.add_stage_history(history)
        
        # Retrieve history
        retrieved = state_manager.get_stage_history("%1")
        
        assert len(retrieved) == 1
        assert retrieved[0].stage_name == "test"
        assert retrieved[0].status == "COMPLETED"
        assert retrieved[0].metadata["duration"] == 5.2
    
    def test_database_backup(self, state_manager):
        """Test database backup functionality."""
        # Force a backup
        state_manager._create_backup()
        
        # Check backup file exists
        backup_files = list(state_manager.db_path.parent.glob(f"{state_manager.db_path.stem}.backup.*"))
        assert len(backup_files) > 0


class TestConfigManagerIntegration:
    """Test configuration loading and validation."""
    
    def test_config_loading_with_env_vars(self, config_manager, sample_config_data):
        """Test configuration loading with environment variable substitution."""
        import os
        import tempfile
        import yaml
        
        # Set environment variables
        os.environ['TEST_HOST'] = 'example.com'
        os.environ['TEST_PORT'] = '2222'
        
        # Create temporary config file with env vars
        config_content = yaml.dump({
            'hosts': [
                {
                    'name': 'test',
                    'ssh': {
                        'host': '${TEST_HOST:localhost}',
                        'port': '${TEST_PORT:22}',
                        'user': 'test'
                    },
                    'tmux': {
                        'socket': 'default'
                    }
                }
            ]
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_file = f.name
        
        try:
            # Load configuration
            data = config_manager._load_yaml_file(config_file)
            
            # Check substitution worked
            assert data['hosts'][0]['ssh']['host'] == 'example.com'
            assert data['hosts'][0]['ssh']['port'] == '2222'
            
        finally:
            Path(config_file).unlink()
            # Clean up environment
            del os.environ['TEST_HOST']
            del os.environ['TEST_PORT']


class TestLogParserIntegration:
    """Test log parser with real-world scenarios."""
    
    def test_npm_workflow_parsing(self):
        """Test parsing a complete npm workflow."""
        parser = SmartLogParser()
        
        npm_log = [
            "> my-app@1.0.0 lint",
            "> eslint src/",
            "",
            "✓ 0 problems (0 errors, 0 warnings)",
            "### SENTRY {\"type\":\"STATUS\",\"stage\":\"lint\",\"ok\":true}",
            "",
            "> my-app@1.0.0 test", 
            "> jest",
            "",
            "PASS src/utils.test.js",
            "  ✓ should work (2 ms)",
            "",
            "Test Suites: 1 passed, 1 total",
            "Tests: 1 passed, 1 total",
            "### SENTRY {\"type\":\"STATUS\",\"stage\":\"test\",\"ok\":true}",
            "",
            "> my-app@1.0.0 build",
            "> webpack --mode=production",
            "",
            "asset main.js 45.2 KiB [emitted] [minimized]",
            "webpack 5.88.0 compiled successfully in 1.52s",
            "### SENTRY {\"type\":\"STATUS\",\"stage\":\"build\",\"ok\":true}"
        ]
        
        summary = parser.summarize_output(npm_log)
        
        # Should detect multiple successful stages
        assert summary["has_success"] is True
        assert summary["has_errors"] is False
        assert len(summary["all_messages"]) >= 3  # lint, test, build
        
        # Primary result should be high confidence
        assert summary["confidence"] > 0.9
        assert summary["primary_result"].ok is True
    
    def test_error_scenario_parsing(self):
        """Test parsing error scenarios."""
        parser = SmartLogParser()
        
        error_log = [
            "> my-app@1.0.0 build",
            "> webpack --mode=production",
            "",
            "ERROR in src/main.js",
            "Module not found: Error: Can't resolve 'missing-module'",
            "",
            "webpack 5.88.0 compiled with 1 error in 0.43s",
            "### SENTRY {\"type\":\"ERROR\",\"stage\":\"build\",\"ok\":false,\"detail\":\"Build failed\"}"
        ]
        
        summary = parser.summarize_output(error_log)
        
        assert summary["has_errors"] is True
        assert summary["primary_result"].ok is False
        assert summary["primary_result"].type == "ERROR"


class TestApprovalManagerIntegration:
    """Test approval manager with file and token operations."""
    
    def test_file_approval_workflow(self):
        """Test complete file-based approval workflow."""
        with tempfile.TemporaryDirectory() as temp_dir:
            approval_dir = Path(temp_dir) / "approvals"
            
            manager = ApprovalManager(approval_dir=approval_dir)
            
            # Create approval request
            from tmux_sentry.approval import ApprovalRequest
            from datetime import datetime
            
            request = ApprovalRequest(
                pane_id="%1",
                stage_name="deploy",
                request_type="require_approval",
                message="Deploy to production?",
                requested_at=datetime.now()
            )
            
            token = manager.create_approval_request(request)
            
            # Check approval file was created
            pending = manager.get_pending_requests()
            assert len(pending) == 1
            
            # Simulate file-based response
            request_id = list(pending.keys())[0]
            file_path = manager._get_approval_file_path(request_id)
            file_path.write_text("approve")
            
            # Check response is detected
            response = manager.check_approval_response(request_id)
            assert response == "approve"
            
            # Request should be cleaned up
            assert len(manager.get_pending_requests()) == 0
    
    def test_token_security(self):
        """Test approval token security."""
        manager = ApprovalManager(secret_key="test-secret")
        
        # Generate token
        token = manager._generate_approval_token("test-request")
        
        # Validate token
        request_id = manager._validate_approval_token(token)
        assert request_id == "test-request"
        
        # Test with wrong secret
        wrong_manager = ApprovalManager(secret_key="wrong-secret")
        invalid_request_id = wrong_manager._validate_approval_token(token)
        assert invalid_request_id is None


class TestNotificationIntegration:
    """Test notification system integration."""
    
    @pytest.mark.asyncio
    async def test_multi_channel_notification(self):
        """Test notifications across multiple channels."""
        manager = NotificationManager()
        
        # Mock channels to avoid actual network calls
        with patch.object(manager.channels[0], 'send', new_callable=AsyncMock) as mock_stdout:
            mock_stdout.return_value = True
            
            from tmux_sentry.notify import NotificationMessage
            
            message = NotificationMessage(
                title="Test Notification",
                content="This is a test",
                level="info"
            )
            
            results = await manager.send_notification(message)
            
            # Should have tried to send via available channels
            assert len(results) > 0
            mock_stdout.assert_called_once()


@pytest.mark.asyncio
async def test_end_to_end_workflow():
    """Test a complete end-to-end workflow."""
    
    # This would be a full integration test that:
    # 1. Sets up a test tmux session
    # 2. Starts the monitoring engine
    # 3. Simulates AI output with SENTRY messages
    # 4. Verifies state transitions
    # 5. Tests approval workflow
    # 6. Validates notifications
    
    # For now, this is a placeholder for the full E2E test
    # which would require a real tmux environment
    
    parser = SmartLogParser()
    
    # Simulate a pipeline execution
    pipeline_output = [
        "Starting CI pipeline...",
        "### SENTRY {\"type\":\"STATUS\",\"stage\":\"start\",\"ok\":true}",
        "Running lint...",
        "### SENTRY {\"type\":\"STATUS\",\"stage\":\"lint\",\"ok\":true}",
        "Running tests...",
        "### SENTRY {\"type\":\"STATUS\",\"stage\":\"test\",\"ok\":true}",
        "Starting build...",
        "### SENTRY {\"type\":\"ASK\",\"stage\":\"build\",\"question\":\"Deploy to production?\"}",
    ]
    
    messages = parser.parse_lines(pipeline_output)
    
    # Should have detected stages and approval request
    status_messages = [m for m in messages if m.type == "STATUS"]
    ask_messages = [m for m in messages if m.type == "ASK"]
    
    assert len(status_messages) >= 3  # start, lint, test
    assert len(ask_messages) == 1     # approval request
    
    # Verify stages
    stages = [m.stage for m in status_messages if m.stage]
    assert "start" in stages
    assert "lint" in stages
    assert "test" in stages
    
    # Verify approval request
    approval = ask_messages[0]
    assert approval.stage == "build"
    assert "production" in approval.question.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])