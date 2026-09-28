import pytest

import hone_flow as fk
from hone_flow.dag import Arg
from hone_flow.testing import MemoryStorage


def make() -> fk.Workflow:
    wf = fk.Workflow("w", storage=MemoryStorage())

    @wf.global_step()
    def catalog(ctx: fk.Context) -> list[str]:
        return ["x"]

    @wf.step(outputs=("left", "right"))
    def split(item: fk.Item, text: str, catalog: list[str], sep: fk.Param[str]) -> tuple[str, str]:
        a, _, b = text.partition(sep)
        return a, b

    @wf.step(version="2", resources="gpu:ollama", deterministic=False)
    def join(left: str, right: str, review_note: str | None = None, extra: int = 0) -> str:
        return left + right

    @wf.gate()
    def review(join: str) -> str:
        return join

    return wf


def test_args_are_classified() -> None:
    graph = make().graph()
    assert list(graph.steps) == ["catalog", "split", "join", "review"]
    kinds = {a.name: (a.kind, a.source) for a in graph.steps["split"].args}
    assert kinds == {
        "item": ("item", ""),
        "text": ("input", ""),
        "catalog": ("step", "catalog"),
        "sep": ("param", ""),
    }
    join = graph.steps["join"]
    assert Arg("review_note", "review_note", True, None) in join.args
    assert join.resources == "gpu:ollama"
    assert not join.deterministic
    assert join.version == "2"
    assert join.outputs == ("join",)
    assert graph.steps["split"].outputs == ("left", "right")
    assert graph.steps["catalog"].args[0].kind == "ctx"


def test_graph_navigation() -> None:
    graph = make().graph()
    assert graph.deps("join") == ["split"]
    assert graph.downstream(["split"]) == {"split", "join", "review"}
    assert graph.upstream(["join"]) == {"join", "split", "catalog"}


def test_item_input_with_default_is_optional() -> None:
    wf = make()
    wf.validate([fk.Item("1", {"text": "a|b"})])  # 'extra' has a default


def test_duplicate_item_ids_rejected() -> None:
    with pytest.raises(fk.WorkflowDefinitionError, match="unique"):
        make().validate([fk.Item("1", {"text": "a"}), fk.Item("1", {"text": "b"})])


def test_output_name_clash_rejected() -> None:
    wf = make()
    with pytest.raises(fk.WorkflowDefinitionError, match="already used"):

        @wf.step()
        def left(x: int) -> int:
            return x


def test_gate_must_review_a_step() -> None:
    wf = fk.Workflow("g", storage=MemoryStorage())

    @wf.gate()
    def lonely(x: int) -> int:
        return x

    with pytest.raises(fk.WorkflowDefinitionError, match="gate 'lonely'"):
        wf.validate()


def test_empty_workflow_rejected() -> None:
    with pytest.raises(fk.WorkflowDefinitionError, match="no steps"):
        fk.Workflow("empty", storage=MemoryStorage()).validate()


def test_varargs_rejected() -> None:
    wf = fk.Workflow("v", storage=MemoryStorage())
    with pytest.raises(fk.WorkflowDefinitionError, match=r"\*args"):

        @wf.step()
        def f(*xs: int) -> int:
            return 0


def test_unresolvable_type_hint_is_a_definition_error() -> None:
    wf = fk.Workflow("hints", storage=MemoryStorage())

    def f(x: "Missing") -> int:  # type: ignore[name-defined]  # noqa: F821
        return 0

    with pytest.raises(fk.WorkflowDefinitionError, match="type hints of step 'f'"):
        wf.step()(f)


def test_decorators_return_the_function() -> None:
    wf = fk.Workflow("r", storage=MemoryStorage())

    @wf.step()
    def f(x: int) -> int:
        return x + 1

    assert f(1) == 2


def test_graph_is_rebuilt_after_new_steps() -> None:
    wf = make()
    first = wf.graph()
    assert wf.graph() is first

    @wf.step()
    def after(review: str) -> str:
        return review

    assert "after" in wf.graph().steps


def test_global_step_can_declare_several_outputs() -> None:
    wf = fk.Workflow("g2", storage=MemoryStorage())

    @wf.global_step(outputs=("styles", "palette"))
    def catalog() -> tuple[list[str], str]:
        return ["noir"], "dark"

    @wf.step()
    def shot(styles: list[str], palette: str) -> str:
        return styles[0] + palette

    graph = wf.graph()
    assert graph.steps["catalog"].outputs == ("styles", "palette")
    assert graph.deps("shot") == ["catalog"]
