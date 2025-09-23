"""
Enhanced log parser with multiple fallback strategies.
This addresses the critical issue of unstable AI output parsing.
"""

import json
import re
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple, Pattern
from enum import Enum

logger = logging.getLogger(__name__)


class ParseResult(Enum):
    """Parsing result types."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    STATUS = "status"
    UNKNOWN = "unknown"


@dataclass
class ParsedMessage:
    """Represents a parsed log message."""
    type: str
    stage: Optional[str] = None
    ok: Optional[bool] = None
    summary: Optional[str] = None
    detail: Optional[str] = None
    question: Optional[str] = None
    options: Optional[List[str]] = None
    metadata: Dict[str, Any] = None
    confidence: float = 0.0  # 0.0 to 1.0
    source: str = "unknown"  # Which parser produced this
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class LogParser(ABC):
    """Abstract base class for log parsers."""
    
    @abstractmethod
    def can_parse(self, line: str) -> bool:
        """Check if this parser can handle the line."""
        pass
    
    @abstractmethod
    def parse(self, line: str) -> Optional[ParsedMessage]:
        """Parse the line and return a message."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Parser name for logging."""
        pass


class SentryJsonParser(LogParser):
    """Parser for ### SENTRY {json} format."""
    
    def __init__(self):
        self.sentry_pattern = re.compile(r'###\s*SENTRY\s*(.+)', re.IGNORECASE)
    
    @property
    def name(self) -> str:
        return "SentryJsonParser"
    
    def can_parse(self, line: str) -> bool:
        return "### SENTRY" in line.upper()
    
    def parse(self, line: str) -> Optional[ParsedMessage]:
        match = self.sentry_pattern.search(line)
        if not match:
            return None
            
        json_part = match.group(1).strip()
        try:
            data = json.loads(json_part)
            
            if not isinstance(data, dict):
                return None
                
            return ParsedMessage(
                type=data.get("type", "STATUS"),
                stage=data.get("stage"),
                ok=data.get("ok"),
                summary=data.get("summary"),
                detail=data.get("detail"),
                question=data.get("question"),
                options=data.get("options"),
                metadata=data,
                confidence=0.95,  # High confidence for explicit format
                source=self.name
            )
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to parse SENTRY JSON: {e}")
            return None


class PureJsonParser(LogParser):
    """Parser for pure JSON lines."""
    
    @property
    def name(self) -> str:
        return "PureJsonParser"
    
    def can_parse(self, line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("{") and stripped.endswith("}")
    
    def parse(self, line: str) -> Optional[ParsedMessage]:
        try:
            data = json.loads(line.strip())
            
            if not isinstance(data, dict):
                return None
                
            # Must have a recognized type
            msg_type = data.get("type")
            if msg_type not in {"STATUS", "ERROR", "ASK", "ACTION", "LOG"}:
                return None
                
            return ParsedMessage(
                type=msg_type,
                stage=data.get("stage"),
                ok=data.get("ok"),
                summary=data.get("summary"),
                detail=data.get("detail"),
                question=data.get("question"),
                options=data.get("options"),
                metadata=data,
                confidence=0.90,  # High confidence for valid JSON
                source=self.name
            )
            
        except (json.JSONDecodeError, TypeError):
            return None


class KeywordHeuristicParser(LogParser):
    """Parser using keyword heuristics."""
    
    def __init__(self):
        # Success patterns
        self.success_patterns = [
            re.compile(r'\b(success|successful|completed|done|finished|passed|ok)\b', re.I),
            re.compile(r'\b\d+\s+passing\b', re.I),
            re.compile(r'\bbuilt?\s+successfully\b', re.I),
            re.compile(r'\bcompiled?\s+successfully\b', re.I),
            re.compile(r'\bdeployed?\s+successfully\b', re.I),
            re.compile(r'✓|✅|[✔️]', re.I),
            re.compile(r'\b0\s+problems?\b', re.I),
        ]
        
        # Error patterns
        self.error_patterns = [
            re.compile(r'\b(error|failed|failure|exception|fatal)\b', re.I),
            re.compile(r'\b\d+\s+failing\b', re.I),
            re.compile(r'\bcompilation\s+failed\b', re.I),
            re.compile(r'\bbuild\s+failed\b', re.I),
            re.compile(r'✗|❌|[✖️]', re.I),
            re.compile(r'\b\d+\s+problems?\b', re.I),
        ]
        
        # Question patterns
        self.question_patterns = [
            re.compile(r'\?', re.I),
            re.compile(r'\b(should|would|can)\s+\w+\b', re.I),
            re.compile(r'\b(yes|no)\b.*\?', re.I),
            re.compile(r'\bcontinue\b.*\?', re.I),
        ]
        
        # Stage detection patterns
        self.stage_patterns = [
            re.compile(r'\b(lint|linting)\b', re.I),
            re.compile(r'\b(test|testing)\b', re.I),
            re.compile(r'\b(build|building)\b', re.I),
            re.compile(r'\b(deploy|deploying)\b', re.I),
            re.compile(r'\bcompil(e|ing)\b', re.I),
        ]
    
    @property
    def name(self) -> str:
        return "KeywordHeuristicParser"
    
    def can_parse(self, line: str) -> bool:
        # Can always attempt heuristic parsing
        return True
    
    def parse(self, line: str) -> Optional[ParsedMessage]:
        line_lower = line.lower()
        
        # Detect stage
        stage = None
        for pattern in self.stage_patterns:
            if pattern.search(line):
                stage = pattern.pattern.split('|')[0].strip('\\b()').lower()
                break
        
        # Check for success
        for pattern in self.success_patterns:
            if pattern.search(line):
                return ParsedMessage(
                    type="STATUS",
                    stage=stage,
                    ok=True,
                    summary=line.strip()[:100],
                    confidence=0.70,
                    source=self.name
                )
        
        # Check for errors
        for pattern in self.error_patterns:
            if pattern.search(line):
                return ParsedMessage(
                    type="ERROR",
                    stage=stage,
                    ok=False,
                    detail=line.strip()[:100],
                    confidence=0.75,
                    source=self.name
                )
        
        # Check for questions
        for pattern in self.question_patterns:
            if pattern.search(line):
                return ParsedMessage(
                    type="ASK",
                    stage=stage,
                    question=line.strip()[:200],
                    options=["yes", "no"],
                    confidence=0.60,
                    source=self.name
                )
        
        return None


class ExitCodeParser(LogParser):
    """Parser for process exit codes."""
    
    def __init__(self):
        self.exit_pattern = re.compile(r'\bexit\s+code\s*[:=]?\s*(\d+)\b', re.I)
        self.return_pattern = re.compile(r'\breturn\s+code\s*[:=]?\s*(\d+)\b', re.I)
    
    @property
    def name(self) -> str:
        return "ExitCodeParser"
    
    def can_parse(self, line: str) -> bool:
        return bool(self.exit_pattern.search(line) or self.return_pattern.search(line))
    
    def parse(self, line: str) -> Optional[ParsedMessage]:
        match = self.exit_pattern.search(line) or self.return_pattern.search(line)
        if not match:
            return None
            
        exit_code = int(match.group(1))
        
        return ParsedMessage(
            type="STATUS" if exit_code == 0 else "ERROR",
            ok=exit_code == 0,
            detail=f"Process exited with code {exit_code}",
            metadata={"exit_code": exit_code},
            confidence=0.85,
            source=self.name
        )


class NpmTestParser(LogParser):
    """Specialized parser for npm test output."""
    
    def __init__(self):
        self.passing_pattern = re.compile(r'\b(\d+)\s+passing\b', re.I)
        self.failing_pattern = re.compile(r'\b(\d+)\s+failing\b', re.I)
        self.test_summary_pattern = re.compile(r'\b(\d+)\s+tests?\s+passed\b', re.I)
    
    @property
    def name(self) -> str:
        return "NpmTestParser"
    
    def can_parse(self, line: str) -> bool:
        return bool(
            self.passing_pattern.search(line) or 
            self.failing_pattern.search(line) or
            self.test_summary_pattern.search(line)
        )
    
    def parse(self, line: str) -> Optional[ParsedMessage]:
        # Check for passing tests
        passing_match = self.passing_pattern.search(line)
        failing_match = self.failing_pattern.search(line)
        
        if passing_match:
            count = int(passing_match.group(1))
            return ParsedMessage(
                type="STATUS",
                stage="test",
                ok=True,
                summary=f"{count} tests passing",
                metadata={"passing_tests": count},
                confidence=0.90,
                source=self.name
            )
        
        if failing_match:
            count = int(failing_match.group(1))
            return ParsedMessage(
                type="ERROR",
                stage="test",
                ok=False,
                detail=f"{count} tests failing",
                metadata={"failing_tests": count},
                confidence=0.90,
                source=self.name
            )
        
        return None


class CompilationParser(LogParser):
    """Parser for compilation/build output."""
    
    def __init__(self):
        self.success_patterns = [
            re.compile(r'\bbuilt?\s+successfully\b', re.I),
            re.compile(r'\bcompiled?\s+successfully\b', re.I),
            re.compile(r'\bbuild\s+complete\b', re.I),
        ]
        
        self.error_patterns = [
            re.compile(r'\bcompilation\s+failed\b', re.I),
            re.compile(r'\bbuild\s+failed\b', re.I),
            re.compile(r'\berror\s+TS\d+\b', re.I),  # TypeScript errors
            re.compile(r'\bsyntax\s+error\b', re.I),
        ]
    
    @property
    def name(self) -> str:
        return "CompilationParser"
    
    def can_parse(self, line: str) -> bool:
        return any(
            pattern.search(line) 
            for pattern in self.success_patterns + self.error_patterns
        )
    
    def parse(self, line: str) -> Optional[ParsedMessage]:
        for pattern in self.success_patterns:
            if pattern.search(line):
                return ParsedMessage(
                    type="STATUS",
                    stage="build",
                    ok=True,
                    summary=line.strip()[:100],
                    confidence=0.85,
                    source=self.name
                )
        
        for pattern in self.error_patterns:
            if pattern.search(line):
                return ParsedMessage(
                    type="ERROR",
                    stage="build",
                    ok=False,
                    detail=line.strip()[:100],
                    confidence=0.85,
                    source=self.name
                )
        
        return None


class SmartLogParser:
    """
    Multi-strategy log parser with fallback capabilities.
    
    Applies multiple parsing strategies in order of confidence,
    ensuring reliable message extraction even with inconsistent AI output.
    """
    
    def __init__(self):
        self.parsers: List[LogParser] = [
            SentryJsonParser(),        # Highest confidence
            PureJsonParser(),          # High confidence
            NpmTestParser(),           # Specialized, high confidence
            CompilationParser(),       # Specialized, high confidence
            ExitCodeParser(),          # Medium-high confidence
            KeywordHeuristicParser(),  # Lowest confidence, but always tries
        ]
        
        logger.info(f"Initialized SmartLogParser with {len(self.parsers)} strategies")
    
    def parse_line(self, line: str) -> Optional[ParsedMessage]:
        """
        Parse a single line using multiple strategies.
        
        Returns the result from the first parser that can handle the line,
        prioritized by confidence.
        """
        line = line.strip()
        if not line:
            return None
        
        for parser in self.parsers:
            if parser.can_parse(line):
                try:
                    result = parser.parse(line)
                    if result:
                        logger.debug(f"Parsed with {parser.name}: {result.type}")
                        return result
                except Exception as e:
                    logger.warning(f"Parser {parser.name} failed: {e}")
                    continue
        
        return None
    
    def parse_lines(self, lines: List[str]) -> List[ParsedMessage]:
        """Parse multiple lines and return all valid messages."""
        messages = []
        
        for line in lines:
            result = self.parse_line(line)
            if result:
                messages.append(result)
        
        return messages
    
    def parse_text(self, text: str) -> List[ParsedMessage]:
        """Parse text blob and return all valid messages."""
        lines = text.splitlines()
        return self.parse_lines(lines)
    
    def get_best_result(self, lines: List[str]) -> Optional[ParsedMessage]:
        """
        Get the best (highest confidence) result from multiple lines.
        
        Useful when you want a single definitive answer from a block of output.
        """
        messages = self.parse_lines(lines)
        if not messages:
            return None
        
        # Sort by confidence, then by type priority
        type_priority = {"ERROR": 3, "ASK": 2, "STATUS": 1}
        
        messages.sort(
            key=lambda m: (m.confidence, type_priority.get(m.type, 0)),
            reverse=True
        )
        
        return messages[0]
    
    def summarize_output(self, lines: List[str]) -> Dict[str, Any]:
        """
        Analyze output and provide a summary.
        
        Returns comprehensive analysis of the output including:
        - Success/failure status
        - Key messages
        - Confidence metrics
        """
        messages = self.parse_lines(lines)
        
        summary = {
            "total_lines": len(lines),
            "parsed_messages": len(messages),
            "has_errors": False,
            "has_success": False,
            "has_questions": False,
            "confidence": 0.0,
            "primary_result": None,
            "all_messages": messages
        }
        
        if not messages:
            summary["confidence"] = 0.0
            return summary
        
        # Analyze message types
        error_count = sum(1 for m in messages if m.type == "ERROR")
        success_count = sum(1 for m in messages if m.type == "STATUS" and m.ok)
        question_count = sum(1 for m in messages if m.type == "ASK")
        
        summary["has_errors"] = error_count > 0
        summary["has_success"] = success_count > 0
        summary["has_questions"] = question_count > 0
        
        # Get primary result (highest confidence)
        primary = self.get_best_result(lines)
        if primary:
            summary["primary_result"] = primary
            summary["confidence"] = primary.confidence
        
        return summary