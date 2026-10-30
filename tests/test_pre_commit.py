import os
import subprocess

import pytest

HOOK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".githooks", "pre-commit"))
TOKEN = "sk-ant-" + "A1b2" * 6  # built here so no key-shaped text is committed
BODY = "".join(f"line {i}\n" for i in range(20))  # long enough for git to call a moved copy a rename


def git(repo, *args):
    cfg = ["-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", *cfg, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "notes.md").write_text(BODY)
    git(tmp_path, "add", "notes.md")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def hook(repo):
    return subprocess.run(["bash", HOOK], cwd=repo, capture_output=True, text=True).returncode


def test_a_clean_change_passes(repo):
    (repo / "notes.md").write_text(BODY + "world\n")
    git(repo, "add", "notes.md")
    assert hook(repo) == 0


def test_a_secret_in_a_renamed_file_is_caught(repo):
    (repo / "docs").mkdir()
    git(repo, "mv", "notes.md", "docs/notes.md")
    (repo / "docs" / "notes.md").write_text(f"{BODY}{TOKEN}\n")
    git(repo, "add", "docs/notes.md")
    status = subprocess.run(["git", "diff", "--cached", "--name-status"], cwd=repo, capture_output=True, text=True).stdout
    assert status.startswith("R")  # git sees a rename, the case a rebuilt ACM list drops
    assert hook(repo) == 1


@pytest.mark.parametrize("name", ["my notes.md", "café.md"])
def test_a_secret_in_a_path_git_quotes_or_the_shell_splits_is_caught(repo, name):
    (repo / name).write_text(f"{TOKEN}\n")
    git(repo, "add", name)
    assert hook(repo) == 1


def test_a_secret_before_a_large_diff_is_caught(repo):
    # grep -q exits at the match while printf is still writing; under pipefail that read as no match.
    (repo / "big.txt").write_text(f"{TOKEN}\n" + "padding line\n" * 200000)
    git(repo, "add", "big.txt")
    assert hook(repo) == 1
