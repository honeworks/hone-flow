import pytest

import hone_flow as fk
from hone_flow.invoke import failure_text, split_outputs, step_seed
from hone_flow.testing import MemoryStorage


def make() -> fk.Workflow:
    wf = fk.Workflow("w", storage=MemoryStorage())

    @wf.step()
    def one(x: int) -> int:
        return x

    @wf.step(outputs=("left", "right"))
    def two(one: int) -> tuple[int, int]:
        return one, one

    return wf


def test_seed_is_stable_and_differs_per_item_step_and_salt() -> None:
    seed = step_seed(0, "01", "shotlist")
    assert seed == step_seed(0, "01", "shotlist")
    assert 0 <= seed < 2**32
    others = {
        step_seed(0, "02", "shotlist"),
        step_seed(0, "01", "render"),
        step_seed(1, "01", "shotlist"),
        step_seed(0, "01", "shotlist", salt=":run-2"),
    }
    assert seed not in others
    assert len(others) == 4


def test_seed_formula_matches_the_spec() -> None:
    import hashlib

    expected = int(hashlib.sha256(b"7:01:shotlist").hexdigest()[:8], 16)
    assert step_seed(7, "01", "shotlist") == expected


def test_single_output_is_named_after_the_step() -> None:
    steps = make().graph().steps
    assert split_outputs(steps["one"], (1, 2)) == {"one": (1, 2)}


def test_several_outputs_come_from_a_tuple_or_list() -> None:
    steps = make().graph().steps
    assert split_outputs(steps["two"], (1, 2)) == {"left": 1, "right": 2}
    assert split_outputs(steps["two"], [3, 4]) == {"left": 3, "right": 4}


@pytest.mark.parametrize("wrong", [(1,), (1, 2, 3), 5, "ab"])
def test_wrong_number_of_outputs_says_what_to_return(wrong: object) -> None:
    steps = make().graph().steps
    with pytest.raises(fk.HoneFlowError, match=r"declares outputs \('left', 'right'\); return a tuple"):
        split_outputs(steps["two"], wrong)


def test_failure_text_is_the_traceback_without_secrets() -> None:
    try:
        raise RuntimeError("bad key sk-abcdefghijklmnop")
    except RuntimeError:
        text = failure_text()
    assert "Traceback" in text
    assert "RuntimeError: bad key ***" in text
    assert "sk-abcdefghijklmnop" not in text
