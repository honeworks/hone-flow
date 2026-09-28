import json

from hone_flow.spans import json_attribute, run_error, step_error


def test_json_attribute_is_sorted_and_tolerant() -> None:
    assert json_attribute({"b": 1, "a": [2]}) == '{"a": [2], "b": 1}'
    assert json.loads(json_attribute({"x": object})) == {"x": str(object)}


def test_run_error_only_for_failed_and_interrupted_runs() -> None:
    assert run_error("failed") == "run failed"
    assert run_error("interrupted") == "run interrupted"
    assert run_error("completed") is None
    assert run_error("awaiting_review") is None


def test_step_error_uses_the_last_traceback_line_and_an_exception_event() -> None:
    traceback = "Traceback (most recent call last):\n  File 'x'\nValueError: boom\n"
    message, events = step_error(traceback, "2026-09-27T14:03:11.120Z")
    assert message == "ValueError: boom"
    assert events == [
        {
            "name": "exception",
            "time": "2026-09-27T14:03:11.120Z",
            "attributes": {"exception.stacktrace": traceback},
        }
    ]
    assert step_error(None, "t") == (None, [])
    assert step_error("", "t") == (None, [])
