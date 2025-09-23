"""
Test suite for the smart log parser.
Validates all parsing strategies and fallback mechanisms.
"""

import pytest
from tmux_sentry.parser import (
    SmartLogParser, 
    SentryJsonParser, 
    KeywordHeuristicParser,
    NpmTestParser,
    ParsedMessage
)


class TestSentryJsonParser:
    """Test the SENTRY JSON format parser."""
    
    def test_valid_sentry_json(self):
        parser = SentryJsonParser()
        line = '### SENTRY {"type":"STATUS","stage":"build","ok":true,"summary":"Build completed"}'
        
        result = parser.parse(line)
        
        assert result is not None
        assert result.type == "STATUS"
        assert result.stage == "build"
        assert result.ok is True
        assert result.summary == "Build completed"
        assert result.confidence == 0.95
    
    def test_case_insensitive_sentry(self):
        parser = SentryJsonParser()
        line = '### sentry {"type":"ERROR","stage":"test","ok":false}'
        
        result = parser.parse(line)
        
        assert result is not None
        assert result.type == "ERROR"
        assert result.ok is False
    
    def test_invalid_json(self):
        parser = SentryJsonParser()
        line = '### SENTRY {invalid json}'
        
        result = parser.parse(line)
        
        assert result is None
    
    def test_no_sentry_marker(self):
        parser = SentryJsonParser()
        line = 'Regular log output without marker'
        
        result = parser.parse(line)
        
        assert result is None


class TestKeywordHeuristicParser:
    """Test the keyword heuristic parser."""
    
    def test_success_detection(self):
        parser = KeywordHeuristicParser()
        test_cases = [
            "Build completed successfully",
            "Tests passed ✓",
            "Compilation successful",
            "5 passing tests",
            "0 problems found"
        ]
        
        for line in test_cases:
            result = parser.parse(line)
            assert result is not None
            assert result.type == "STATUS"
            assert result.ok is True
    
    def test_error_detection(self):
        parser = KeywordHeuristicParser()
        test_cases = [
            "Build failed with errors",
            "3 failing tests",
            "Fatal error occurred",
            "Compilation failed ❌",
            "5 problems found"
        ]
        
        for line in test_cases:
            result = parser.parse(line)
            assert result is not None
            assert result.type == "ERROR"
            assert result.ok is False
    
    def test_question_detection(self):
        parser = KeywordHeuristicParser()
        test_cases = [
            "Should we continue with deployment?",
            "Would you like to retry?",
            "Continue anyway? (yes/no)"
        ]
        
        for line in test_cases:
            result = parser.parse(line)
            assert result is not None
            assert result.type == "ASK"
            assert "yes" in result.options
            assert "no" in result.options


class TestNpmTestParser:
    """Test the NPM test output parser."""
    
    def test_passing_tests(self):
        parser = NpmTestParser()
        line = "15 passing (2s)"
        
        result = parser.parse(line)
        
        assert result is not None
        assert result.type == "STATUS"
        assert result.stage == "test"
        assert result.ok is True
        assert result.metadata["passing_tests"] == 15
    
    def test_failing_tests(self):
        parser = NpmTestParser()
        line = "3 failing"
        
        result = parser.parse(line)
        
        assert result is not None
        assert result.type == "ERROR"
        assert result.stage == "test"
        assert result.ok is False
        assert result.metadata["failing_tests"] == 3


class TestSmartLogParser:
    """Test the complete smart log parser."""
    
    def test_parser_priority(self):
        parser = SmartLogParser()
        
        # SENTRY format should have highest priority
        line = '### SENTRY {"type":"STATUS","ok":true} and also success keyword'
        result = parser.parse_line(line)
        
        assert result is not None
        assert result.source == "SentryJsonParser"
        assert result.confidence == 0.95
    
    def test_fallback_to_heuristics(self):
        parser = SmartLogParser()
        
        # Should fall back to heuristic parser
        line = "Build completed successfully"
        result = parser.parse_line(line)
        
        assert result is not None
        assert result.source == "KeywordHeuristicParser"
        assert result.type == "STATUS"
        assert result.ok is True
    
    def test_multiple_line_parsing(self):
        parser = SmartLogParser()
        
        lines = [
            "Starting build...",
            "npm run build",
            "Build completed successfully",
            "### SENTRY {\"type\":\"STATUS\",\"stage\":\"build\",\"ok\":true}"
        ]
        
        results = parser.parse_lines(lines)
        
        # Should find at least 2 results (success keyword + SENTRY)
        assert len(results) >= 2
        
        # Get best result (should be SENTRY due to higher confidence)
        best = parser.get_best_result(lines)
        assert best is not None
        assert best.source == "SentryJsonParser"
    
    def test_output_summary(self):
        parser = SmartLogParser()
        
        lines = [
            "Starting tests...",
            "5 passing",
            "Build failed",
            "### SENTRY {\"type\":\"ERROR\",\"stage\":\"build\",\"ok\":false}"
        ]
        
        summary = parser.summarize_output(lines)
        
        assert summary["total_lines"] == 4
        assert summary["parsed_messages"] >= 2
        assert summary["has_errors"] is True
        assert summary["has_success"] is True
        assert summary["primary_result"] is not None
    
    def test_empty_input(self):
        parser = SmartLogParser()
        
        result = parser.parse_line("")
        assert result is None
        
        results = parser.parse_lines([])
        assert results == []
        
        summary = parser.summarize_output([])
        assert summary["total_lines"] == 0
        assert summary["confidence"] == 0.0


class TestRealWorldScenarios:
    """Test with real-world log outputs."""
    
    def test_npm_build_output(self):
        parser = SmartLogParser()
        
        npm_output = [
            "> my-app@1.0.0 build",
            "> webpack --mode=production",
            "",
            "asset main.js 87.3 KiB [emitted] [minimized] (name: main)",
            "webpack 5.88.0 compiled successfully in 2.1s",
            "### SENTRY {\"type\":\"STATUS\",\"stage\":\"build\",\"ok\":true,\"summary\":\"Webpack build completed\"}"
        ]
        
        summary = parser.summarize_output(npm_output)
        
        assert summary["has_success"] is True
        assert summary["primary_result"].ok is True
        assert summary["confidence"] > 0.9
    
    def test_test_failure_output(self):
        parser = SmartLogParser()
        
        test_output = [
            "npm test",
            "",
            "FAIL src/utils.test.js",
            "  ✓ should work correctly (5 ms)",
            "  ✗ should handle errors (3 ms)",
            "",
            "    Expected: true",
            "    Received: false",
            "",
            "Tests: 1 failed, 1 passed, 2 total",
            "### SENTRY {\"type\":\"ERROR\",\"stage\":\"test\",\"ok\":false,\"detail\":\"1 test failed\"}"
        ]
        
        summary = parser.summarize_output(test_output)
        
        assert summary["has_errors"] is True
        assert summary["primary_result"].ok is False
    
    def test_github_actions_output(self):
        parser = SmartLogParser()
        
        gh_output = [
            "gh workflow run deploy.yml",
            "✓ Created workflow run 1234567890",
            "To see the run in your browser, visit:",
            "https://github.com/user/repo/actions/runs/1234567890"
        ]
        
        results = parser.parse_lines(gh_output)
        
        # Should detect success from the ✓ symbol
        success_results = [r for r in results if r.type == "STATUS" and r.ok]
        assert len(success_results) > 0


# Fixtures for testing
@pytest.fixture
def sample_logs():
    return [
        "Starting application...",
        "### SENTRY {\"type\":\"STATUS\",\"stage\":\"init\",\"ok\":true}",
        "Running tests...",
        "5 passing (1.2s)",
        "Build started",
        "Build completed successfully",
        "### SENTRY {\"type\":\"STATUS\",\"stage\":\"build\",\"ok\":true,\"summary\":\"All tasks completed\"}"
    ]


def test_integration_with_sample_logs(sample_logs):
    """Integration test with sample logs."""
    parser = SmartLogParser()
    
    summary = parser.summarize_output(sample_logs)
    
    assert summary["total_lines"] == 7
    assert summary["has_success"] is True
    assert summary["confidence"] > 0.8
    assert summary["primary_result"] is not None