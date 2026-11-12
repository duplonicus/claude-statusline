#!/usr/bin/env python3
"""Refresh Claude Code's usage cache, so the per-model meter in the status line stays current.

The status line reads the per-model weekly limit (the "fable" meter) from the reading Claude
Code keeps in ~/.claude.json, and Claude Code only renews that reading when its usage screen
is opened. This asks a headless Claude Code for the same reading through its `get_usage`
control request: no prompt, no model call, and Claude Code signs the request itself, so this
script never touches the login. Meant for a timer (see systemd/ and the README).

It does nothing when no status line was drawn in the last few minutes (no session is open to
show the meter to) or when the reading is already fresh.

Exit status: 0 for refreshed, skipped, or a failed try while the reading is still under an
hour old (the next run tries again); 1 when a try failed and the reading is old enough that
the status line is showing its age.

Usage: python3 scripts/refresh_usage.py [--force] [--claude PATH]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import statusline as sl

ACTIVE = 300  # seconds; a status line drawn this recently means a session is open
MIN_AGE = 300  # seconds; a reading younger than this is left alone (Claude Code's own floor is 60)
TIMEOUT = 60  # seconds for the headless Claude Code to answer; a normal run takes about 3
REQUEST_ID = "statusline-usage"
REQUEST = {"type": "control_request", "request_id": REQUEST_ID,
           "request": {"subtype": "get_usage", "skip_behaviors": True}}
# --safe-mode: no hooks, MCP servers or CLAUDE.md, so a run starts nothing of the user's.
ARGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--safe-mode", "--no-session-persistence"]


def session_open(now, folder=None, within=ACTIVE):
    """True when a status line run left its per-session file in the last `within` seconds."""
    try:
        with os.scandir(folder or sl.SESSION_DIR) as entries:
            return any(e.name.endswith(".json") and now - e.stat().st_mtime <= within for e in entries)
    except OSError:
        return False


def fetched_at(path=None):
    """When Claude Code last fetched the reading, in epoch seconds; None when there is none."""
    try:
        with open(path or sl.USAGE_CACHE) as f:
            return float(json.load(f)["cachedUsageUtilization"]["fetchedAtMs"]) / 1000
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_answer(stdout):
    """The reply to our request out of Claude Code's output stream: (ok, error text or None)."""
    for line in stdout.splitlines():
        try:
            msg = json.loads(line)
            reply = msg["response"] if msg.get("type") == "control_response" else None
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if not isinstance(reply, dict) or reply.get("request_id") != REQUEST_ID:
            continue
        if reply.get("subtype") != "success":
            return False, str(reply.get("error") or "Claude Code refused the request")
        body = reply.get("response")
        if not (isinstance(body, dict) and body.get("rate_limits_available")):
            return False, "Claude Code answered without plan limits (not signed in with a subscription?)"
        return True, None
    return False, "no reply to get_usage in Claude Code's output"


def ask(claude, timeout=TIMEOUT, cwd=None):
    """Run a headless Claude Code with the one request: (ok, error text or None)."""
    try:
        done = subprocess.run([claude] + ARGS, input=json.dumps(REQUEST) + "\n", capture_output=True,
                              text=True, timeout=timeout, cwd=cwd or sl.CACHE_DIR)
    except subprocess.TimeoutExpired:
        return False, f"Claude Code did not answer within {timeout} s"
    except OSError as e:
        return False, f"could not start {claude}: {e}"
    ok, error = read_answer(done.stdout)
    if not ok and done.returncode:
        error = f"{error} (exit {done.returncode}: {done.stderr.strip()[-200:]})"
    return ok, error


def describe(path=None):
    """One line with what the status line will now show, e.g. 'fable 13%, 5h 52%, 7d 56%'."""
    try:
        with open(path or sl.USAGE_CACHE) as f:
            util = json.load(f)["cachedUsageUtilization"]["utilization"]
        parts = [f"{(lim['scope']['model']['display_name']).lower()} {lim['percent']:g}%"
                 for lim in util.get("limits") or []
                 if lim.get("kind") == "weekly_scoped" and lim.get("percent") is not None
                 and ((lim.get("scope") or {}).get("model") or {}).get("display_name")]
        parts += [f"{label} {util[key]['utilization']:g}%" for key, label in (("five_hour", "5h"), ("seven_day", "7d"))
                  if (util.get(key) or {}).get("utilization") is not None]
        return ", ".join(parts) or "no limits in the reading"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "reading not readable"


def run(now=None, force=False, claude=None, sessions=None, cache=None, timeout=TIMEOUT, cwd=None):
    """One timer tick: (exit status, message)."""
    now = now or time.time()
    before = fetched_at(cache)
    age = None if before is None else now - before
    if not force:
        if not session_open(now, sessions):
            return 0, "skipped: no status line drawn in the last 5 minutes"
        if age is not None and 0 <= age < MIN_AGE:
            return 0, f"skipped: reading is {age:.0f} s old"
    claude = claude or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    os.makedirs(cwd or sl.CACHE_DIR, exist_ok=True)
    ok, error = ask(claude, timeout, cwd)
    after = fetched_at(cache)
    # The status line reads the cache file, so a reply only counts once the file has moved.
    if ok and after is not None and (before is None or after > before):
        return 0, f"refreshed: {describe(cache)}"
    if ok:
        error = "Claude Code answered but did not renew its cached reading"
    stale = age is None or age < 0 or age >= sl.USAGE_OLD  # a reading dated in the future can't be trusted as fresh
    return (1 if stale else 0), f"failed: {error}" + ("" if stale else "; reading still under an hour old, next run retries")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="ask even when no session is open or the reading is fresh")
    ap.add_argument("--claude", help="path to the claude executable (default: the one on PATH)")
    args = ap.parse_args()
    status, message = run(force=args.force, claude=args.claude)
    print(message, file=sys.stderr if message.startswith("failed") else sys.stdout)
    return status


if __name__ == "__main__":
    sys.exit(main())
