#!/bin/bash
"""
Bash wrapper for smart_wrapper.py - ensures SENTRY format output
"""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/smart_wrapper.py" "$@"