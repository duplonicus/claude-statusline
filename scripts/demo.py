#!/usr/bin/env python3
"""Print the status line for a made-up session, to preview it without Claude Code.

Usage: python3 scripts/demo.py [columns]
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import statusline as sl

GIT = {"branch": "main", "ahead": 2, "behind": 0, "staged": 1, "modified": 3, "untracked": 0}


def sample(now):
    return {
        "session_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        "session_name": "fix login redirect",
        "model": {"display_name": "Opus 5.5"},
        "effort": {"level": "high"},
        "workspace": {"current_dir": os.path.expanduser("~/dev/myproject")},
        "cost": {"total_cost_usd": 1.23, "total_duration_ms": 754000, "total_lines_added": 156, "total_lines_removed": 23},
        "context_window": {"total_input_tokens": 76000, "context_window_size": 200000, "used_percentage": 38},
        "rate_limits": {
            "five_hour": {"used_percentage": 62, "resets_at": now + 9000},  # half gone, 62% used: over pace
            "seven_day": {"used_percentage": 30, "resets_at": now + 302400},  # half gone, 30% used: under
        },
        "prompt_cache": {"caching_observed": True, "warm": True, "expires_at": now + 2580},
    }


def render(cols=160):
    now = time.time()
    fable = [{"label": "fable", "used_percentage": 41, "resets_at": now + 302400, "age": 120}]
    return sl.build(sample(now), cols, now=now, git=lambda cwd: GIT, scoped=lambda now: fable)


if __name__ == "__main__":
    print("\n".join(render(int(sys.argv[1]) if len(sys.argv) > 1 else 160)))
