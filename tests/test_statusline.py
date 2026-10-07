"""pytest for statusline.py. Run: uvx pytest"""
import json
import os
import subprocess
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)
import statusline as sl

NOW = 1_800_000_000
SID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
GIT = {"branch": "main", "ahead": 2, "behind": 0, "staged": 1, "modified": 3, "untracked": 0}

FULL = {
    "session_id": SID,
    "session_name": "statusline design",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
    "workspace": {"current_dir": os.path.expanduser("~/dev/myproject")},
    "effort": {"level": "xhigh"},
    "cost": {"total_cost_usd": 1.234, "total_duration_ms": 754000, "total_lines_added": 156, "total_lines_removed": 23},
    "context_window": {"total_input_tokens": 76000, "context_window_size": 200000, "used_percentage": 38},
    "rate_limits": {
        "five_hour": {"used_percentage": 42, "resets_at": NOW + 9000},  # half the window gone
        "seven_day": {"used_percentage": 8, "resets_at": NOW + 302400},
    },
    "prompt_cache": {"caching_observed": True, "warm": True, "expires_at": NOW + 2580},
    "pr": {"number": 12, "url": "https://github.com/x/y/pull/12", "review_state": "approved"},
}


def plain(lines):
    return [sl.ANSI_RE.sub("", l) for l in lines]


def build(data, cols=200, scoped=lambda now: []):
    return plain(sl.build(data, cols, now=NOW, git=lambda cwd: GIT, scoped=scoped))


def test_full_render_wide():
    top, bottom = build(FULL)
    assert top == f"Opus 5.5 xhigh │ ~/dev/myproject main +1 ~3 ↑2 │ PR #12 │ statusline design │ id {SID}"
    assert bottom == (
        "ctx ━━━━━━━━━━━━ 38% 76k/200k │ 5h ━━━━━━ 42% ▼ 8 ↻ 2h30m │ 7d ━━━━━━ 8% ▼ 42 ↻ 3d12h"
        " │ $1.23 │ 12m │ +156 -23 │ cache 43m"
    )


def test_always_two_rows_and_session_id_survives_narrow():
    for cols in (200, 120, 90, 70, 50):
        lines = build(FULL, cols)
        assert len(lines) == 2
        assert all(len(l) <= max(20, cols - 4) for l in lines), (cols, lines)
        assert SID[:8] in lines[0]
        assert "ctx" in lines[1] and "38%" in lines[1]


def test_pace_delta_is_used_minus_elapsed():
    win = {"used_percentage": 70, "resets_at": NOW + 9000}  # 50% elapsed -> 20 over
    assert "▲ 20" in sl.ANSI_RE.sub("", sl.limit_variants("5h", win, 18000, NOW)[0])
    win = {"used_percentage": 50, "resets_at": NOW + 9000}  # exactly on pace -> no marker
    text = sl.ANSI_RE.sub("", sl.limit_variants("5h", win, 18000, NOW)[0])
    assert "▲" not in text and "▼" not in text


def test_empty_and_null_input_does_not_crash():
    assert build({}) == ["Claude", "ctx ━━━━━━━━━━━━ 0%"]
    nulls = {"context_window": {"used_percentage": None, "current_usage": None}, "rate_limits": None, "cost": None}
    assert build(nulls)[1] == "ctx ━━━━━━━━━━━━ 0%"


def test_expired_limit_window_has_no_reset_or_pace():
    d = {"rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": NOW - 5}}}
    assert build(d)[1].endswith("5h ━━━━━━ 42%")


def test_cold_cache_and_colour_thresholds():
    d = dict(FULL, prompt_cache={"caching_observed": True, "warm": False, "expires_at": None})
    assert build(d)[1].endswith("cache cold")
    assert sl.level(49, 50, 80) == sl.GREEN and sl.level(50, 50, 80) == sl.YELLOW and sl.level(80, 50, 80) == sl.RED


@pytest.mark.parametrize("pct,filled", [(0, 0), (1, 1), (50, 6), (100, 12), (140, 12)])
def test_bar_is_always_exactly_n_cells(pct, filled):
    raw = sl.bar(pct, 12, sl.GREEN)
    assert sl.width(raw) == 12
    assert raw.count("━") == 12
    assert raw.startswith(f"\x1b[38;5;{sl.GREEN}m" + "━" * filled + sl.RESET)


def test_parse_git_porcelain_v2():
    text = "\n".join([
        "# branch.oid 1234567890abcdef",
        "# branch.head main",
        "# branch.ab +2 -1",
        "1 M. N... 100644 100644 100644 a b staged.py",
        "1 .M N... 100644 100644 100644 a b modified.py",
        "1 MM N... 100644 100644 100644 a b both.py",
        "? new.py",
    ])
    assert sl.parse_git(text) == {"branch": "main", "ahead": 2, "behind": 1, "staged": 2, "modified": 2, "untracked": 1}
    assert sl.parse_git("# branch.oid abcdef1234\n# branch.head (detached)")["branch"] == "@abcdef1"


def test_formatters():
    assert [sl.tokens(n) for n in (950, 76000, 1_000_000, 1_250_000)] == ["950", "76k", "1M", "1.2M"]
    assert [sl.span(s) for s in (45, 300, 9000, 86400, 302400)] == ["45s", "5m", "2h30m", "1d", "3d12h"]


def test_cli_end_to_end(tmp_path):
    script = os.path.join(SRC, "statusline.py")
    env = dict(os.environ, COLUMNS="60", HOME=str(tmp_path))  # its cache goes to a throwaway home
    env.pop("CLAUDE_CONFIG_DIR", None)
    out = subprocess.run([sys.executable, script], input=json.dumps(FULL), capture_output=True, text=True, env=env)
    lines = out.stdout.splitlines()
    assert out.returncode == 0 and len(lines) == 2
    assert all(sl.width(l) <= 56 for l in lines)
    out = subprocess.run([sys.executable, script], input="not json", capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "Claude" in out.stdout
    saved = tmp_path / ".cache" / "claude-statusline" / "sessions" / f"{SID}.json"
    assert json.loads(saved.read_text())["pct"] == 38


def test_context_usage_is_saved_per_session_for_hooks(tmp_path):
    sl.record_context(FULL, now=NOW, folder=str(tmp_path))
    assert json.loads((tmp_path / f"{SID}.json").read_text()) == {"pct": 38, "t": NOW}
    assert [p.name for p in tmp_path.iterdir()] == [f"{SID}.json"]  # no temp file left behind


def test_context_usage_is_not_saved_without_a_reading_or_with_an_unsafe_id(tmp_path):
    sl.record_context({"session_id": SID}, folder=str(tmp_path))
    sl.record_context({"session_id": "../escape", "context_window": {"used_percentage": 5}}, folder=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


# --- per-model weekly limits, read from Claude Code's cached usage data ---------

def usage_cache(tmp_path, limits, fetched=NOW - 60):
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"cachedUsageUtilization": {"fetchedAtMs": fetched * 1000, "utilization": {"limits": limits}}}))
    return str(path)


def iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


FABLE = {"kind": "weekly_scoped", "group": "weekly", "percent": 48, "resets_at": iso(NOW + 302400),
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}}
WEEKLY_ALL = {"kind": "weekly_all", "group": "weekly", "percent": 49, "resets_at": iso(NOW + 302400), "scope": None}


def test_scoped_limits_picks_only_the_per_model_weekly_meter(tmp_path):
    found = sl.scoped_limits(NOW, usage_cache(tmp_path, [WEEKLY_ALL, FABLE, {"kind": "session", "percent": 6, "scope": None}]))
    assert found == [{"label": "fable", "used_percentage": 48, "resets_at": NOW + 302400, "age": 60}]


def test_a_reading_from_a_window_that_has_reset_is_dropped(tmp_path):
    last_week = dict(FABLE, resets_at=iso(NOW - 10))
    assert sl.scoped_limits(NOW, usage_cache(tmp_path, [last_week])) == []


@pytest.mark.parametrize("content", ["", "not json", "{}", '{"cachedUsageUtilization": null}',
                                     '{"cachedUsageUtilization": {"fetchedAtMs": 1, "utilization": {"limits": [{"kind": "weekly_scoped", "percent": 5, "resets_at": 7, "scope": {"model": {"display_name": "X"}}}]}}}'])
def test_a_missing_or_unexpected_cache_shows_nothing(tmp_path, content):
    path = tmp_path / ".claude.json"
    path.write_text(content)
    assert sl.scoped_limits(NOW, str(path)) == []
    assert sl.scoped_limits(NOW, str(tmp_path / "absent.json")) == []


def test_fable_meter_renders_between_the_weekly_limit_and_the_cost(tmp_path):
    fresh = lambda now: sl.scoped_limits(now, usage_cache(tmp_path, [WEEKLY_ALL, FABLE]))
    bottom = build(FULL, scoped=fresh)[1]
    # half the week gone, 48% used: 2 under pace
    assert "│ 7d ━━━━━━ 8% ▼ 42 ↻ 3d12h │ fable ━━━━━━ 48% ▼ 2 ↻ 3d12h │ $1.23 │" in bottom


def test_an_old_reading_says_how_old_it_is(tmp_path):
    stale = lambda now: sl.scoped_limits(now, usage_cache(tmp_path, [FABLE], fetched=NOW - 3 * 3600))
    assert "fable ━━━━━━ 48% ▼ 2 ↻ 3d12h (3h00m old) │" in build(FULL, scoped=stale)[1]


def test_fable_meter_needs_rate_limit_data_and_never_breaks_the_two_rows(tmp_path):
    fresh = lambda now: sl.scoped_limits(now, usage_cache(tmp_path, [FABLE]))
    assert "fable" not in build({}, scoped=fresh)[1]
    for cols in (200, 120, 90, 70, 50):
        lines = build(FULL, cols, scoped=fresh)
        assert len(lines) == 2 and all(len(l) <= max(20, cols - 4) for l in lines), (cols, lines)
        assert "ctx" in lines[1] and "38%" in lines[1]
