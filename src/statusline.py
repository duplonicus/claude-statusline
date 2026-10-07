#!/usr/bin/env python3
"""Claude Code status line: two rows, identity on top, gauges below.

Row 1: model + effort | dir + git | worktree | PR | session name | session id
Row 2: context bar | 5h and 7d limits with pace + reset | cost | time | lines | cache

Reads the session JSON on stdin (https://code.claude.com/docs/en/statusline).
Segments shrink, then drop, lowest priority first, to fit $COLUMNS.
Stdlib only, so it starts fast and has nothing to install.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

RESET = "\x1b[0m"
SEP = " \x1b[38;5;238m│\x1b[0m "
GIT_TTL = 5  # seconds; git status can be slow (network or Windows-mounted drives), and this runs on every message
CACHE_DIR = os.path.expanduser("~/.cache/claude-statusline")
SESSION_DIR = os.path.join(CACHE_DIR, "sessions")
WINDOWS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;.*?\x07")


def c(code, text):
    return f"\x1b[38;5;{code}m{text}{RESET}"


DIM, GREY, WHITE = 240, 245, 252
GREEN, YELLOW, RED, BLUE, CYAN, MAGENTA, ORANGE = 114, 221, 203, 75, 80, 176, 215


def width(s):
    return len(ANSI_RE.sub("", s))


def level(pct, warn, crit):
    return RED if pct >= crit else YELLOW if pct >= warn else GREEN


def bar(pct, cells, color):
    filled = max(0, min(cells, round(pct / 100 * cells)))
    if pct > 0 and filled == 0:
        filled = 1
    return c(color, "━" * filled) + c(238, "━" * (cells - filled))


def tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def span(seconds):
    s = max(0, int(seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return f"{d}d{h}h" if h else f"{d}d"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m" if m else f"{s}s"


def short_path(path, limit=32):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + "/"):
        path = "~" + path[len(home):]
    if len(path) <= limit:
        return path
    parts = path.split("/")
    return "/".join([parts[0], "…"] + parts[-2:]) if len(parts) > 3 else path


def git_info(cwd):
    """Branch + counts for cwd, or None outside a repo. Cached for GIT_TTL."""
    cache = os.path.join(CACHE_DIR, hashlib.md5(cwd.encode()).hexdigest() + ".json")
    cached = None
    try:
        with open(cache) as f:
            cached = json.load(f)
        if time.time() - cached["t"] < GIT_TTL:
            return cached["info"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain=v2", "--branch"],
            capture_output=True, text=True, timeout=2,
        )
        info = parse_git(out.stdout) if out.returncode == 0 else None
    except subprocess.TimeoutExpired:
        return cached["info"] if cached else None  # stale beats blank
    except OSError:
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache + ".tmp", "w") as f:
            json.dump({"t": time.time(), "info": info}, f)
        os.replace(cache + ".tmp", cache)
    except OSError:
        pass
    return info


def parse_git(text):
    info = {"branch": "", "ahead": 0, "behind": 0, "staged": 0, "modified": 0, "untracked": 0}
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            info["branch"] = line[14:]
        elif line.startswith("# branch.oid ") and info["branch"] in ("", "(detached)"):
            info["oid"] = line[13:20]
        elif line.startswith("# branch.ab "):
            a, b = line[12:].split()
            info["ahead"], info["behind"] = int(a), -int(b)
        elif line[:2] in ("1 ", "2 "):
            info["staged"] += line[2] != "."
            info["modified"] += line[3] != "."
        elif line.startswith("u "):
            info["modified"] += 1
        elif line.startswith("? "):
            info["untracked"] += 1
    oid = info.pop("oid", "detached")
    if info["branch"] == "(detached)":
        info["branch"] = "@" + oid
    return info


def git_segment(g):
    s = c(MAGENTA, g["branch"])
    marks = []
    if g["staged"]:
        marks.append(c(GREEN, f"+{g['staged']}"))
    if g["modified"]:
        marks.append(c(YELLOW, f"~{g['modified']}"))
    if g["untracked"]:
        marks.append(c(GREY, f"?{g['untracked']}"))
    if g["ahead"]:
        marks.append(c(CYAN, f"↑{g['ahead']}"))
    if g["behind"]:
        marks.append(c(ORANGE, f"↓{g['behind']}"))
    return s + (" " + " ".join(marks) if marks else "")


def limit_variants(label, win, length, now):
    """Rate-limit window: bar, used %, pace delta (used minus time elapsed), reset."""
    pct = win.get("used_percentage")
    if pct is None:
        return None
    color = level(pct, 60, 85)
    head = c(GREY, label) + " "
    num = c(color, f"{round(pct)}%")
    resets = win.get("resets_at")
    if not resets or resets <= now:
        return [head + bar(pct, 6, color) + " " + num, head + num]
    left = resets - now
    elapsed = max(0.0, min(100.0, (1 - left / length) * 100))
    delta = round(pct - elapsed)
    pace = ""
    if elapsed >= 3 and delta:
        # over pace = on track to hit the limit before the window resets
        pace = " " + (c(RED if delta >= 10 else YELLOW, f"▲ {delta}") if delta > 0 else c(GREEN, f"▼ {-delta}"))
    reset = " " + c(DIM, "↻ " + span(left))
    return [head + bar(pct, 6, color) + " " + num + pace + reset, head + num + pace + reset, head + num]


def fit(segments, limit):
    """Join segments; while too wide, step the most droppable one to its next variant."""
    state = [0] * len(segments)

    def render():
        return SEP.join(v[i] for (_, v), i in zip(segments, state) if v[i])

    while width(render()) > limit:
        cands = [k for k, (_, v) in enumerate(segments) if state[k] < len(v) - 1]
        if not cands:
            break
        state[max(cands, key=lambda k: segments[k][0])] += 1
    return render()


def build(d, cols, now=None, git=git_info):
    now = now or time.time()
    limit = max(20, cols - 4)
    top, bottom = [], []  # (priority, [variants]); higher priority number drops first

    model = c(WHITE, (d.get("model") or {}).get("display_name") or "Claude")
    extra = ""
    effort = (d.get("effort") or {}).get("level")
    if effort:
        extra += " " + c({"low": GREY, "medium": BLUE, "high": CYAN}.get(effort, MAGENTA), effort)
    if d.get("fast_mode"):
        extra += " " + c(ORANGE, "fast")
    agent = (d.get("agent") or {}).get("name")
    if agent:
        extra += " " + c(BLUE, "@" + agent)
    top.append((0, [model + extra, model]))

    ws = d.get("workspace") or {}
    cwd = ws.get("current_dir") or d.get("cwd") or ""
    if cwd:
        g = git(cwd)
        tail = " " + git_segment(g) if g and g["branch"] else ""
        full, base = short_path(cwd), os.path.basename(cwd.rstrip("/")) or cwd
        bare = " " + c(MAGENTA, g["branch"]) if tail else ""
        top.append((1, [c(BLUE, full) + tail, c(BLUE, base) + tail, c(BLUE, base) + bare]))

    wt = (d.get("worktree") or {}).get("name") or ws.get("git_worktree")
    if wt:
        top.append((4, [c(ORANGE, "wt:" + wt), ""]))

    pr = d.get("pr") or {}
    if pr.get("number"):
        state = pr.get("review_state")
        col = {"approved": GREEN, "changes_requested": RED, "draft": GREY}.get(state, YELLOW)
        text = c(col, f"PR #{pr['number']}")
        if pr.get("url"):
            text = f"\x1b]8;;{pr['url']}\x07{text}\x1b]8;;\x07"
        top.append((5, [text, ""]))

    name = d.get("session_name")
    if name:
        cut = lambda n: name if len(name) <= n else name[: n - 1] + "…"
        top.append((3, [c(GREY, cut(40)), c(GREY, cut(20)), ""]))

    sid = d.get("session_id")
    if sid:
        top.append((2, [c(DIM, "id ") + c(GREY, sid), c(DIM, "id ") + c(GREY, sid[:8])]))

    cw = d.get("context_window") or {}
    pct = cw.get("used_percentage") or 0
    size = cw.get("context_window_size") or 0
    used = cw.get("total_input_tokens") or round(size * pct / 100)
    col = level(pct, 50, 80)
    head, num = c(GREY, "ctx") + " ", c(col, f"{round(pct)}%")
    detail = " " + c(DIM, f"{tokens(used)}/{tokens(size)}") if size else ""
    bottom.append((0, [head + bar(pct, 12, col) + " " + num + detail, head + bar(pct, 12, col) + " " + num, head + num]))

    rl = d.get("rate_limits") or {}
    for prio, key, label in ((1, "five_hour", "5h"), (2, "seven_day", "7d")):
        v = limit_variants(label, rl.get(key) or {}, WINDOWS[key], now)
        if v:
            bottom.append((prio, v))
    spend = rl.get("spend_limit") or {}
    if spend.get("used_percentage") is not None:
        sp = spend["used_percentage"]
        bottom.append((3, [c(GREY, "spend ") + c(level(sp, 60, 85), f"{round(sp)}%"), ""]))

    cost = d.get("cost") or {}
    if cost.get("total_cost_usd"):
        bottom.append((5, [c(GREY, f"${cost['total_cost_usd']:.2f}"), ""]))
    if cost.get("total_duration_ms"):
        bottom.append((6, [c(GREY, span(cost["total_duration_ms"] / 1000)), ""]))
    added, removed = cost.get("total_lines_added") or 0, cost.get("total_lines_removed") or 0
    if added or removed:
        bottom.append((7, [c(GREEN, f"+{added}") + " " + c(RED, f"-{removed}"), ""]))

    pc = d.get("prompt_cache") or {}
    if pc.get("caching_observed"):
        exp = pc.get("expires_at")
        if pc.get("warm") and exp and exp > now:
            text = c(DIM, "cache " + span(exp - now))
        else:
            text = c(YELLOW, "cache cold")
        bottom.append((8, [text, ""]))

    return [fit(top, limit), fit(bottom, limit)]


def record_context(d, now=None, folder=None):
    """Save how full the context window is, per session, where a hook can read it.

    Hooks are not told the context usage; the status line is. Writing it here lets a
    UserPromptSubmit hook warn the session before auto-compact (see README).
    """
    sid = d.get("session_id")
    pct = (d.get("context_window") or {}).get("used_percentage")
    if not sid or pct is None or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        return
    folder = folder or SESSION_DIR
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, sid + ".json")
        with open(path + ".tmp", "w") as f:
            json.dump({"pct": pct, "t": now or time.time()}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    if isinstance(data, dict):
        record_context(data)
    try:
        cols = int(os.environ.get("COLUMNS") or 120)
    except ValueError:
        cols = 120
    try:
        print("\n".join(build(data, cols)))
    except Exception as e:  # a broken status line should say so, not go blank
        print(f"statusline error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
