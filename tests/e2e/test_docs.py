"""AC-27: README.md and docs/ are complete and every code example in them runs as written.

- Relative links in README, docs/, design/, CONTRIBUTING, AGENTS, CHANGELOG and examples/README resolve.

- Every ```python block of README.md and docs/*.md runs. The blocks of one file run in order in one fresh
  module namespace (later blocks use names from earlier ones), registered in ``sys.modules`` and
  ``linecache`` so Pydantic models load back and ``inspect.getsource`` works on the steps they define, in
  a fresh temporary working folder, with the environment restored afterwards.
- The ```bash blocks of docs/cli.md are one shell session; they run in a fresh temporary folder with this
  environment's ``hone-flow`` script on ``PATH``. Other ```bash blocks (``pip install``) are not run.
- Blocks that must not run (S3 URLs, output samples) use a ```text or ```toml fence.
"""

import linecache
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
PAGES = {
    "index.md",
    "concepts.md",
    "run-folders.md",
    "run-format.md",
    "resume-and-fork.md",
    "gates.md",
    "notifications.md",
    "measurements.md",
    "storage.md",
    "records.md",
    "cli.md",
    "adapters.md",
    "read-api.md",
    "across-items.md",
}
NO_PYTHON = {"index.md", "run-format.md", "cli.md"}  # maps, JSON references and the shell session
PROJECT_DOCS = [
    *(ROOT / name for name in ("CONTRIBUTING.md", "AGENTS.md", "CHANGELOG.md")),
    ROOT / "examples" / "README.md",
    *sorted((ROOT / "design").rglob("*.md")),
]
HOME_PATH = re.compile(r"/home/(?!me/)\w+")  # a real home folder; /home/me/ is the docs' example
WORKSPACE_ONLY = ("project-design", "KICKOFF", "STATUS.md", "DECISIONS.md", "SPEC.md")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def blocks(doc: Path, language: str) -> list[str]:
    pattern = re.compile(rf"^```{language}\n(.*?)^```", re.S | re.M)
    return pattern.findall(doc.read_text(encoding="utf-8"))


def test_ac27_docs_pages_exist() -> None:
    assert {doc.name for doc in DOCS} >= PAGES
    for doc in DOCS:
        if doc.name in PAGES - NO_PYTHON:
            assert blocks(doc, "python"), f"{doc.name} has no runnable example"


def test_ac27_docs_readme_quickstart_is_the_example() -> None:
    source = (ROOT / "examples" / "quickstart.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # everything after the docstring
    assert blocks(ROOT / "README.md", "python")[0].strip() == body.strip()


def test_ac27_docs_relative_links_resolve() -> None:
    for doc in [*DOCS, *PROJECT_DOCS]:
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("mailto:"):
                assert (doc.parent / target).exists(), f"{doc.name} links to missing {target}"


def test_ac27_docs_design_pages_exist() -> None:
    names = {doc.relative_to(ROOT / "design").as_posix() for doc in PROJECT_DOCS if "design" in doc.parts}
    assert names >= {
        "README.md",
        "current.md",
        "decisions.md",
        "changes/0001-initial-design.md",
        "changes/0002-run-folders.md",
        "history/0000-research.md",
        "history/v0.1-design.md",
    }


def test_ac27_docs_mention_no_build_workspace_files() -> None:
    for doc in [*DOCS, *PROJECT_DOCS]:
        text = doc.read_text(encoding="utf-8")
        for word in WORKSPACE_ONLY:
            assert word not in text, f"{doc.name} mentions {word}"
        assert not HOME_PATH.search(text), f"{doc.name} has a local home path"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_ac27_docs_python_blocks_run(doc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = dict(os.environ)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HONE_CAPTURE_CONTENT", raising=False)
    monkeypatch.delenv("HONE_FLOW_WORKDIR", raising=False)
    module = types.ModuleType(f"hone_flow_docs_{doc.stem.replace('-', '_').lower()}")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    names: list[str] = []
    try:
        for number, block in enumerate(blocks(doc, "python"), start=1):
            filename = f"<{doc.name} block {number}>"
            names.append(filename)
            linecache.cache[filename] = (len(block), None, block.splitlines(keepends=True), filename)
            exec(compile(block, filename, "exec"), module.__dict__)  # noqa: S102 - running our own docs
    finally:
        for filename in names:
            linecache.cache.pop(filename, None)
        os.environ.clear()
        os.environ.update(saved)


def test_ac27_docs_cli_session_runs(tmp_path: Path) -> None:
    session = "\n".join(blocks(ROOT / "docs" / "cli.md", "bash"))
    assert "hone-flow run" in session
    env = {k: v for k, v in os.environ.items() if k not in ("EDITOR", "HONE_CAPTURE_CONTENT")}
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    bash = shutil.which("bash")
    assert bash, "the CLI session needs bash"
    result = subprocess.run(
        [bash, "-euo", "pipefail", "-c", session],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert result.returncode == 0, result.stdout + result.stderr
