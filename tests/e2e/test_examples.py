"""AC-27: every example runs, explains itself (What / How / Why) and is listed in examples/README.md."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

EXAMPLES = Path(__file__).parents[2] / "examples"
FILES = sorted(p for p in EXAMPLES.glob("*.py"))


def test_ac27_examples_are_the_spec_set() -> None:
    expected = {
        "quickstart",
        "run_folder",
        "params_and_context",
        "global_steps",
        "serializers",
        "failure_and_resume",
        "crash_recovery",
        "incompatible_resume",
        "partial_runs",
        "fork_refresh",
        "fork_diff",
        "review_gates",
        "pin_and_cleanup",
        "notifications",
        "measurements",
        "records_and_traces",
        "read_api",
        "s3_storage",
        "gpu_batching",
        "cli_session",
        "video_pipeline",
        "run_labels",
        "progress",
        "revise_incrementally",
        "fan_in",
        "select_items",
        "produced_items",
    }
    assert {p.stem for p in FILES} == expected


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_ac27_examples_explain_themselves(path: Path) -> None:
    docstring = ast.get_docstring(ast.parse(path.read_text())) or ""
    paragraphs = [p.strip() for p in docstring.split("\n\n")]
    assert any(p.startswith("What:") for p in paragraphs), "a 'What:' paragraph"
    assert any(p.startswith("How:") for p in paragraphs), "a 'How:' paragraph"
    assert any(p.startswith("Why:") for p in paragraphs), "a 'Why:' paragraph"
    assert f"[`{path.name}`]({path.name})" in (EXAMPLES / "README.md").read_text(), "listed in the README"


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_ac27_examples_run(path: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(path)], cwd=tmp_path, capture_output=True, text=True, timeout=90
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "prints what it shows"
