"""pytest for scripts/refresh_usage.py. Claude Code is a stand-in script; nothing here reaches the real one."""
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "refresh_usage.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import refresh_usage as ru

NOW = 1_800_000_000

# Stands in for `claude`: records how it was called, then behaves as FAKE_MODE says.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ["FAKE_MODE"]
sent = sys.stdin.read()
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": sent, "cwd": os.getcwd()}) + "\n")
if mode == "hang":
    time.sleep(30)
rid = json.loads(sent)["request_id"]
def reply(body):
    print(json.dumps({"type": "system", "subtype": "hook_started"}))
    print("not json at all")
    print(json.dumps({"type": "control_response", "response": {"request_id": "someone-else", "subtype": "error", "error": "not ours"}}))
    print(json.dumps({"type": "control_response", "response": {"request_id": rid, **body}}))
if mode in ("ok", "ok_no_write", "no_limits"):
    if mode == "ok":
        with open(os.environ["FAKE_CACHE"], "w") as f:
            json.dump({"other": "kept", "cachedUsageUtilization": {"fetchedAtMs": int(os.environ["FAKE_FETCHED_MS"]), "utilization": {
                "five_hour": {"utilization": 55, "resets_at": "2033-01-15T10:00:00+00:00"},
                "seven_day": {"utilization": 56.5, "resets_at": "2033-01-18T10:00:00+00:00"},
                "limits": [{"kind": "weekly_all", "percent": 56, "scope": None},
                           {"kind": "weekly_scoped", "percent": 14, "resets_at": "2033-01-18T10:00:00+00:00",
                            "scope": {"model": {"display_name": "Fable"}}}]}}}, f)
    reply({"subtype": "success", "response": {"rate_limits_available": mode != "no_limits"}})
elif mode == "error":
    reply({"subtype": "error", "error": "get_usage is not supported in this context"})
elif mode == "crash":
    sys.stderr.write("boom: not logged in\n")
    sys.exit(3)
'''


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A throwaway sessions folder, usage cache and stand-in claude."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    fake = tmp_path / "claude"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    cache, calls = tmp_path / ".claude.json", tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CACHE", str(cache))
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    monkeypatch.setenv("FAKE_FETCHED_MS", str((NOW + 1) * 1000))

    class World:
        def session(self, age, name="abc.json"):
            path = sessions / name
            path.write_text("{}")
            os.utime(path, (NOW - age, NOW - age))

        def reading(self, age):
            cache.write_text(json.dumps({"cachedUsageUtilization": {"fetchedAtMs": (NOW - age) * 1000, "utilization": {}}}))

        def tick(self, mode, **kw):
            monkeypatch.setenv("FAKE_MODE", mode)
            kw.setdefault("claude", str(fake))
            return ru.run(now=NOW, sessions=str(sessions), cache=str(cache), cwd=str(tmp_path), **kw)

        def calls(self):
            return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    w = World()
    w.cache, w.tmp = cache, tmp_path
    return w


def test_refreshes_when_a_session_is_open_and_the_reading_is_old(world):
    world.session(age=20)
    world.reading(age=1200)
    assert world.tick("ok") == (0, "refreshed: fable 14%, 5h 55%, 7d 56.5%")
    assert ru.fetched_at(str(world.cache)) == NOW + 1
    (call,) = world.calls()
    assert call["argv"] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                            "--safe-mode", "--no-session-persistence"]
    assert call["stdin"].endswith("\n") and json.loads(call["stdin"]) == {
        "type": "control_request", "request_id": "statusline-usage",
        "request": {"subtype": "get_usage", "skip_behaviors": True}}
    assert call["cwd"] == str(world.tmp)


def test_refreshes_when_there_is_no_reading_yet(world):
    world.session(age=0)
    assert world.tick("ok") == (0, "refreshed: fable 14%, 5h 55%, 7d 56.5%")


def test_skips_without_asking_when_no_session_is_open(world):
    world.session(age=ru.ACTIVE + 1)
    world.reading(age=7200)
    assert world.tick("ok") == (0, "skipped: no status line drawn in the last 5 minutes")
    assert world.calls() == []


def test_skips_without_asking_when_the_reading_is_fresh(world):
    world.session(age=0)
    world.reading(age=ru.MIN_AGE - 1)
    assert world.tick("ok") == (0, f"skipped: reading is {ru.MIN_AGE - 1} s old")
    assert world.calls() == []


def test_asks_at_the_exact_thresholds(world):
    world.session(age=ru.ACTIVE)
    world.reading(age=ru.MIN_AGE)
    assert world.tick("ok")[1].startswith("refreshed")
    assert len(world.calls()) == 1


def test_force_asks_with_no_session_and_a_fresh_reading(world):
    world.reading(age=10)
    assert world.tick("ok", force=True)[1].startswith("refreshed")
    assert len(world.calls()) == 1


def test_a_reading_dated_in_the_future_is_not_called_fresh(world):
    world.session(age=0)
    world.reading(age=-500)
    status, message = world.tick("ok_no_write")
    assert len(world.calls()) == 1 and message.startswith("failed")


@pytest.mark.parametrize("mode, text", [
    ("error", "failed: get_usage is not supported in this context"),
    ("ok_no_write", "failed: Claude Code answered but did not renew its cached reading"),
    ("no_limits", "failed: Claude Code answered without plan limits (not signed in with a subscription?)"),
    ("crash", "failed: no reply to get_usage in Claude Code's output (exit 3: boom: not logged in)"),
])
def test_a_failed_try_is_an_error_only_once_the_reading_is_an_hour_old(world, mode, text):
    world.session(age=0)
    world.reading(age=3599)
    assert world.tick(mode) == (0, text + "; reading still under an hour old, next run retries")
    world.reading(age=3600)
    assert world.tick(mode) == (1, text)
    assert ru.fetched_at(str(world.cache)) == NOW - 3600  # a failed try leaves the reading alone


def test_a_failed_try_with_no_reading_at_all_is_an_error(world):
    world.session(age=0)
    assert world.tick("error") == (1, "failed: get_usage is not supported in this context")


def test_a_claude_that_never_answers_is_cut_off(world):
    world.session(age=0)
    world.reading(age=7200)
    assert world.tick("hang", timeout=1) == (1, "failed: Claude Code did not answer within 1 s")


def test_a_missing_claude_is_reported(world):
    world.session(age=0)
    world.reading(age=7200)
    status, message = world.tick("ok", claude=str(world.tmp / "nope"))
    assert status == 1 and message.startswith(f"failed: could not start {world.tmp / 'nope'}: ")


def test_session_open_counts_only_session_files(tmp_path):
    assert ru.session_open(NOW, str(tmp_path / "missing")) is False
    (tmp_path / "abc.json.tmp").write_text("{}")
    os.utime(tmp_path / "abc.json.tmp", (NOW, NOW))
    assert ru.session_open(NOW, str(tmp_path)) is False
    (tmp_path / "abc.json").write_text("{}")
    os.utime(tmp_path / "abc.json", (NOW - 10, NOW - 10))
    assert ru.session_open(NOW, str(tmp_path)) is True
    assert ru.session_open(NOW, str(tmp_path), within=9) is False


@pytest.mark.parametrize("text", ["", "not json", "[]", "{}", '{"cachedUsageUtilization": null}',
                                  '{"cachedUsageUtilization": {"fetchedAtMs": "soon"}}'])
def test_fetched_at_is_none_for_anything_unreadable(tmp_path, text):
    path = tmp_path / ".claude.json"
    assert ru.fetched_at(str(path)) is None
    path.write_text(text)
    assert ru.fetched_at(str(path)) is None


def test_read_answer_takes_only_the_reply_to_its_own_request():
    other = json.dumps({"type": "control_response", "response": {"request_id": "x", "subtype": "success", "response": {"rate_limits_available": True}}})
    assert ru.read_answer(other) == (False, "no reply to get_usage in Claude Code's output")
    assert ru.read_answer('null\n[1]\n"text"\n{"type": "control_response"}\n' + other) == (False, "no reply to get_usage in Claude Code's output")
    mine = json.dumps({"type": "control_response", "response": {"request_id": ru.REQUEST_ID, "subtype": "success", "response": {"rate_limits_available": True}}})
    assert ru.read_answer(other + "\n" + mine) == (True, None)


def test_the_script_run_as_a_program_uses_the_paths_the_status_line_uses(world):
    """End to end as the timer runs it, with HOME pointed at a throwaway folder."""
    home = world.tmp
    sessions = home / ".cache" / "claude-statusline" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "abc.json").write_text("{}")
    env = {**os.environ, "HOME": str(home), "FAKE_MODE": "ok", "FAKE_FETCHED_MS": "99999999999999"}
    env.pop("CLAUDE_CONFIG_DIR", None)
    done = subprocess.run([sys.executable, SCRIPT, "--claude", str(home / "claude")], env=env, capture_output=True, text=True)
    assert (done.returncode, done.stdout, done.stderr) == (0, "refreshed: fable 14%, 5h 55%, 7d 56.5%\n", "")
    (call,) = world.calls()
    assert call["cwd"] == str(home / ".cache" / "claude-statusline")

    env["FAKE_MODE"] = "error"
    (home / ".claude.json").unlink()
    done = subprocess.run([sys.executable, SCRIPT, "--claude", str(home / "claude")], env=env, capture_output=True, text=True)
    assert (done.returncode, done.stdout, done.stderr) == (1, "", "failed: get_usage is not supported in this context\n")


def test_the_status_line_shows_what_a_refresh_wrote(world):
    """The point of the refresh: the per-model meter reads the renewed cache."""
    import statusline as sl
    world.session(age=0)
    world.reading(age=7200)
    world.tick("ok")
    assert [(m["label"], m["used_percentage"], m["age"]) for m in sl.scoped_limits(NOW + 2, str(world.cache))] == [("fable", 14, 1)]


def test_the_unit_files_run_this_script_every_ten_minutes():
    service = open(os.path.join(ROOT, "systemd", "claude-statusline-usage.service")).read()
    timer = open(os.path.join(ROOT, "systemd", "claude-statusline-usage.timer")).read()
    (script,) = re.findall(r"^ExecStart=\S+ %h/dev/claude-statusline/(\S+)$", service, re.M)
    assert os.path.samefile(os.path.join(ROOT, script), SCRIPT)
    assert re.findall(r"^(OnCalendar|Persistent)=(.*)$", timer, re.M) == [("OnCalendar", "*:0/10"), ("Persistent", "true")]
    assert "/home/" not in service + timer
