#!/usr/bin/env python3
"""
Smart wrapper for AI commands to ensure SENTRY format output.
This script solves the critical issue of unstable AI output format.
"""

import subprocess
import sys
import json
import re
import time
from typing import Optional, List, Dict, Any
from pathlib import Path


class SentryOutputGuard:
    """Ensures AI commands always output SENTRY format for monitoring."""
    
    def __init__(self):
        self.last_sentry_seen = None
        self.command_start_time = time.time()
        self.output_lines: List[str] = []
        
    def parse_sentry_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a SENTRY format line."""
        if "### SENTRY" not in line:
            return None
            
        try:
            # Extract JSON part after "### SENTRY"
            json_part = line.split("### SENTRY", 1)[1].strip()
            return json.loads(json_part)
        except (json.JSONDecodeError, IndexError):
            return None
    
    def detect_command_outcome(self, exit_code: int, output: str) -> Dict[str, Any]:
        """Detect command outcome using heuristics."""
        output_lower = output.lower()
        
        if exit_code == 0:
            # Success indicators
            if any(keyword in output_lower for keyword in [
                "success", "completed", "done", "✓", "✅", "passed", "built successfully"
            ]):
                return {
                    "type": "STATUS",
                    "stage": "auto_detected",
                    "ok": True,
                    "auto": True,
                    "summary": "Command completed successfully"
                }
        
        # Error indicators
        if exit_code != 0 or any(keyword in output_lower for keyword in [
            "error", "failed", "exception", "✗", "❌", "fatal", "compilation failed"
        ]):
            return {
                "type": "ERROR", 
                "stage": "auto_detected",
                "ok": False,
                "auto": True,
                "detail": f"Command failed with exit code {exit_code}"
            }
            
        # Default to success if exit code is 0
        return {
            "type": "STATUS",
            "stage": "auto_detected", 
            "ok": exit_code == 0,
            "auto": True,
            "summary": f"Command exited with code {exit_code}"
        }
    
    def ensure_sentry_output(self, exit_code: int) -> None:
        """Ensure SENTRY output is present, add if missing."""
        output_text = "\n".join(self.output_lines)
        
        # Check if we have any valid SENTRY output
        has_valid_sentry = False
        for line in self.output_lines:
            if self.parse_sentry_line(line):
                has_valid_sentry = True
                break
                
        if not has_valid_sentry:
            # Generate automatic SENTRY output
            auto_sentry = self.detect_command_outcome(exit_code, output_text)
            sentry_line = f"### SENTRY {json.dumps(auto_sentry)}"
            print(sentry_line, flush=True)
            print(f"[wrapper] Auto-generated SENTRY output due to missing format", file=sys.stderr)


def run_command_with_guard(command: List[str]) -> int:
    """Run command with SENTRY output guard."""
    guard = SentryOutputGuard()
    
    try:
        # Start process
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Read output line by line
        while True:
            line = process.stdout.readline()
            if not line:
                break
                
            # Print line immediately (preserve real-time output)
            print(line.rstrip(), flush=True)
            guard.output_lines.append(line.rstrip())
            
            # Track SENTRY outputs
            sentry_data = guard.parse_sentry_line(line)
            if sentry_data:
                guard.last_sentry_seen = sentry_data
        
        # Wait for process completion
        exit_code = process.wait()
        
        # Ensure SENTRY output exists
        guard.ensure_sentry_output(exit_code)
        
        return exit_code
        
    except KeyboardInterrupt:
        print("\n[wrapper] Command interrupted by user", file=sys.stderr)
        if 'process' in locals():
            process.terminate()
        return 130
    except Exception as e:
        print(f"[wrapper] Error running command: {e}", file=sys.stderr)
        return 1


def main():
    """Main wrapper entry point."""
    if len(sys.argv) < 2:
        print("Usage: smart_wrapper.py <command> [args...]", file=sys.stderr)
        print("Example: smart_wrapper.py codex chat --project myapp", file=sys.stderr)
        return 1
    
    command = sys.argv[1:]
    print(f"[wrapper] Running: {' '.join(command)}", file=sys.stderr)
    
    exit_code = run_command_with_guard(command)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())