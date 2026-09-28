"""AC-25: definition errors -> a clear WorkflowDefinitionError."""

from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.testing import MemoryStorage

pytestmark = pytest.mark.e2e


def test_ac25_definition_errors_unresolvable_parameter() -> None:
    wf = fk.Workflow("typo", storage=MemoryStorage())

    @wf.step()
    def timeline(lyrics: str) -> list[str]:
        return lyrics.splitlines()

    @wf.step()
    def shotlist(timelne: list[str]) -> int:  # typo: timelne
        return len(timelne)

    wf.validate()  # names alone could still be item inputs
    with pytest.raises(fk.WorkflowDefinitionError) as err:
        wf.validate([fk.Item("01", {"lyrics": "a\nb"})])
    message = str(err.value)
    assert "'shotlist'" in message
    assert "'timelne'" in message
    assert "timeline" in message  # lists the known step outputs


def test_ac25_definition_errors_cycle() -> None:
    wf = fk.Workflow("cycle", storage=MemoryStorage())

    @wf.step()
    def a(c: int) -> int:
        return c

    @wf.step()
    def b(a: int) -> int:
        return a

    @wf.step()
    def c(b: int) -> int:
        return b

    with pytest.raises(fk.WorkflowDefinitionError, match=r"cycle: a -> c -> b -> a"):
        wf.validate()


def test_ac25_definition_errors_global_step_needs_item_input() -> None:
    wf = fk.Workflow("global", storage=MemoryStorage())

    @wf.global_step()
    def catalog(missing: str) -> str:
        return missing

    with pytest.raises(fk.WorkflowDefinitionError, match=r"global step 'catalog' parameter 'missing'"):
        wf.validate()


def test_ac25_definition_errors_duplicate_step() -> None:
    wf = fk.Workflow("dup", storage=MemoryStorage())

    @wf.step()
    def a(x: int) -> int:
        return x

    with pytest.raises(fk.WorkflowDefinitionError, match="already has a step named 'a'"):

        @wf.step()
        def a(x: int) -> int:
            return x


def test_ac25_definition_errors_gate_without_step_input() -> None:
    wf = fk.Workflow("gate", storage=MemoryStorage())

    @wf.gate()
    def review(text: str) -> str:
        return text

    with pytest.raises(fk.WorkflowDefinitionError, match="gate 'review' must take the output of the step"):
        wf.validate()


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "_global"])
def test_ac25_definition_errors_unsafe_item_id(bad: str) -> None:
    wf = fk.Workflow("ids", storage=MemoryStorage())

    @wf.step()
    def echo(item: fk.Item) -> str:
        return item.id

    with pytest.raises(fk.WorkflowDefinitionError, match="not allowed"):
        wf.validate([fk.Item(bad)])


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a b", "../up", "naïve"])
def test_ac25_definition_errors_unsafe_workflow_name(bad: str) -> None:
    with pytest.raises(fk.WorkflowDefinitionError, match="not a safe folder name"):
        fk.Workflow(bad, storage=MemoryStorage())


def test_ac25_safe_workflow_names_are_accepted() -> None:
    assert fk.Workflow("song_video-2.v1", storage=MemoryStorage()).name == "song_video-2.v1"


def test_ac25_definition_errors_unknown_until_step(tmp_path: Path) -> None:
    wf = fk.Workflow("until", storage=tmp_path)

    @wf.step()
    def only(text: str) -> str:
        return text

    with pytest.raises(fk.WorkflowDefinitionError, match=r"until='onyl' is not a step; steps: \['only'\]"):
        wf.run([fk.Item("01", {"text": "a"})], until="onyl")
