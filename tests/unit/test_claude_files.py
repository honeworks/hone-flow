"""The Claude Code files (CLAUDE.md, .claude/) stay in step with the repository.

Skills and agents name files and design sections; when those move, the instructions go stale without
anyone noticing. These tests fail instead.
"""

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
SKILLS = sorted(CLAUDE.glob("skills/*/SKILL.md"))
AGENTS = sorted(CLAUDE.glob("agents/*.md"))
PAGES = [ROOT / "CLAUDE.md", *SKILLS, *AGENTS]

# A backticked path we can check: starts at a known top-level folder or is a known top-level file.
PATH_PREFIXES = ("src/", "tests/", "docs/", "design/", "examples/", "scripts/", ".claude/", ".github/")
TOP_FILES = {"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "CHANGELOG.md", "README.md", "pyproject.toml"}
PLACEHOLDER = re.compile(r"[<>*{}]|NNNN")
NOT_COMMITTED = {".claude/settings.local.json"}  # each machine's own settings


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    block = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)


def named_paths(text: str) -> set[str]:
    found = set()
    for raw in re.findall(r"`([^`\s]+)`", text):
        path = raw.split("::")[0].split("#")[0].rstrip(".,:;")
        if PLACEHOLDER.search(path) or path in NOT_COMMITTED:
            continue
        if path.startswith(PATH_PREFIXES) or path in TOP_FILES:
            found.add(path)
    return found


def test_there_are_skills_and_agents() -> None:
    assert SKILLS and AGENTS


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_frontmatter(path: Path) -> None:
    meta = frontmatter(path)
    assert meta.get("name") == path.parent.name
    assert 40 <= len(meta.get("description", "")) <= 1536


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agent_frontmatter(path: Path) -> None:
    meta = frontmatter(path)
    assert meta.get("name") == path.stem
    assert meta.get("description") and meta.get("tools")


@pytest.mark.parametrize("path", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_named_files_exist(path: Path) -> None:
    missing = sorted(p for p in named_paths(path.read_text()) if not (ROOT / p).exists())
    assert not missing, f"{path.relative_to(ROOT)} names files that don't exist: {missing}"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_named_design_sections_exist(path: Path) -> None:
    headings = (ROOT / "design/current.md").read_text()
    refs = re.findall(r"`design/current\.md` §(\d+(?:\.\d+)?)", path.read_text())
    missing = [ref for ref in refs if not re.search(rf"^#+ {re.escape(ref)}[. ]", headings, re.MULTILINE)]
    assert not missing, f"{path.relative_to(ROOT)} names sections missing from design/current.md: {missing}"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skills_named_in_skills_exist(path: Path) -> None:
    names = {p.parent.name for p in SKILLS}
    mentioned = set(re.findall(r"`([a-z]+(?:-[a-z]+)+)`", path.read_text()))
    skill_like = {m for m in mentioned if m.split("-")[0] in {n.split("-")[0] for n in names}}
    agents = {p.stem for p in AGENTS} | {"silent-failure-hunter", "type-design-analyzer", "pr-review-toolkit"}
    unknown = sorted(skill_like - names - agents)
    assert not unknown, f"{path.parent.name} names skills that don't exist: {unknown}"


def test_settings_hooks_exist_and_are_executable() -> None:
    settings = json.loads((CLAUDE / "settings.json").read_text())
    commands = [
        hook["command"]
        for groups in settings["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    ]
    assert commands
    for command in commands:
        script = ROOT / command.replace('"$CLAUDE_PROJECT_DIR"/', "")
        assert script.is_file(), command
        assert os.access(script, os.X_OK), f"{script} is not executable"
