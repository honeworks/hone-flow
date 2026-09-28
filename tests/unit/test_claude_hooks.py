"""The Claude Code hooks in .claude/hooks/ do what CLAUDE.md says, offline.

Each hook runs as a subprocess with the JSON Claude Code sends on stdin, in a throwaway git repository;
`gh` is replaced by a small fake script on PATH.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"
GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True)


def run_hook(
    name: str, project: Path, tool_input: dict[str, str], **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HOOKS / name)],
        input=json.dumps({"tool_input": tool_input}),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project), **env},
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository on main with one commit and an ignored file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("local.json\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


# guard-main.sh


def test_guard_blocks_edits_on_main(repo: Path) -> None:
    result = run_hook("guard-main.sh", repo, {"file_path": str(repo / "src" / "new.py")})
    assert result.returncode == 2
    assert "start-task" in result.stderr


def test_guard_allows_edits_on_a_branch(repo: Path) -> None:
    git(repo, "switch", "-q", "-c", "feat/x")
    assert run_hook("guard-main.sh", repo, {"file_path": str(repo / "a.py")}).returncode == 0


def test_guard_allows_the_override(repo: Path) -> None:
    result = run_hook("guard-main.sh", repo, {"file_path": str(repo / "a.py")}, HONE_ALLOW_MAIN="1")
    assert result.returncode == 0


def test_guard_allows_files_outside_the_repository(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "memory" / "note.md"
    assert run_hook("guard-main.sh", repo, {"file_path": str(outside)}).returncode == 0


def test_guard_allows_ignored_files(repo: Path) -> None:
    assert run_hook("guard-main.sh", repo, {"file_path": str(repo / "local.json")}).returncode == 0


def test_guard_reads_the_branch_of_a_worktree(repo: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(worktree))
    assert run_hook("guard-main.sh", repo, {"file_path": str(worktree / "a.py")}).returncode == 0


def test_guard_checks_notebooks(repo: Path) -> None:
    assert run_hook("guard-main.sh", repo, {"notebook_path": str(repo / "n.ipynb")}).returncode == 2


# format-python.sh (runs ruff from this repository's environment)


def test_format_ignores_other_files(tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("import  os\n")
    assert run_hook("format-python.sh", ROOT, {"file_path": str(notes)}).returncode == 0
    assert notes.read_text() == "import  os\n"


def test_format_is_silent_for_a_clean_file(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("X = 1\n")
    result = run_hook("format-python.sh", ROOT, {"file_path": str(clean)})
    assert (result.returncode, result.stderr) == (0, "")


def test_format_fixes_the_file_and_tells_claude_to_reread_it(tmp_path: Path) -> None:
    messy = tmp_path / "messy.py"
    messy.write_text("import hashlib\nX=1\n")
    result = run_hook("format-python.sh", ROOT, {"file_path": str(messy)})
    assert messy.read_text() == "X = 1\n"  # formatted, and the unused import is gone
    assert result.returncode == 2
    assert "Re-read the file" in result.stderr


# after-push.sh (with a fake gh)

FAKE_GH = """#!/usr/bin/env bash
case "$1 $2" in
  "pr view")
    case "$FAKE_PR" in
      none) echo 'no pull requests found for branch "feat/x"' >&2; exit 1 ;;
      error) echo "error connecting to api.github.com" >&2; exit 1 ;;
      *) echo "7 abc123" ;;
    esac ;;
  "api "*)
    [ "$FAKE_REVIEWS" = error ] && exit 1
    [ "$FAKE_REVIEWS" = marked ] && echo "<!-- hone-review sha=abc123 -->"
    exit 0 ;;
esac
"""


def run_after_push(repo: Path, tmp_path: Path, pr: str, reviews: str = "") -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    git(repo, "switch", "-q", "-c", "feat/x")
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    result = run_hook("after-push.sh", repo, {}, PATH=path, FAKE_PR=pr, FAKE_REVIEWS=reviews)
    assert result.returncode == 0
    return result.stdout


def test_after_push_asks_for_a_pull_request(repo: Path, tmp_path: Path) -> None:
    out = json.loads(run_after_push(repo, tmp_path, pr="none"))
    assert "open-pr" in out["hookSpecificOutput"]["additionalContext"]


def test_after_push_asks_for_a_review_of_a_new_head(repo: Path, tmp_path: Path) -> None:
    out = json.loads(run_after_push(repo, tmp_path, pr="open"))
    assert "review-pr" in out["hookSpecificOutput"]["additionalContext"]


def test_after_push_is_silent_when_the_head_is_reviewed(repo: Path, tmp_path: Path) -> None:
    assert run_after_push(repo, tmp_path, pr="open", reviews="marked") == ""


@pytest.mark.parametrize(("pr", "reviews"), [("error", ""), ("open", "error")])
def test_after_push_is_silent_when_github_fails(repo: Path, tmp_path: Path, pr: str, reviews: str) -> None:
    assert run_after_push(repo, tmp_path, pr=pr, reviews=reviews) == ""
