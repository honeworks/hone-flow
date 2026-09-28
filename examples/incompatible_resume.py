"""Incompatible resume: resume never mixes step versions; fork instead.

What: after a run fails, the code of the failed step changes. If its ``version`` was bumped, resume
raises ``fk.IncompatibleRun`` (nothing runs) and tells you to fork. If only the source changed (same
version), resume continues but records a ``source_changed_version_unchanged`` warning.

How: bump ``@wf.step(version=...)`` when a step's behaviour changes; ``run.resume()`` compares the
workflow in memory with the run's manifest. Use ``run.fork()`` to rerun with the new version beside the
old run (see ``fork_diff.py``).

Why: a run whose items were made by two different versions of a step cannot be trusted or compared. The
version is the explicit statement "this produces different results"; the source hash only catches
edits you forgot to version.
"""

import logging
import tempfile

import hone_flow as fk

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
storage = tempfile.mkdtemp()


def make(version: str, *, fixed: bool) -> fk.Workflow:
    """The same workflow as it looks in two releases of our code."""
    wf = fk.Workflow("versions_demo", storage=storage)

    @wf.step()
    def draft(text: str) -> str:
        return text

    if fixed:

        @wf.step(version=version)
        def polish(draft: str) -> str:  # the edited source
            return draft + "!"

    else:

        @wf.step(version=version)
        def polish(draft: str) -> str:
            raise RuntimeError("polish is broken")

    return wf


run_id = make("1", fixed=False).run([fk.Item("01", {"text": "hello"})]).run_id

try:
    make("2", fixed=True).open_run(run_id).resume()  # a new version of a step with work to do
except fk.IncompatibleRun as exc:
    print("IncompatibleRun:", exc)
else:
    raise AssertionError("resume should have refused")

fixed = make("1", fixed=True).open_run(run_id)  # same version, edited source
fixed.resume()
print(fixed.status, fixed.output("polish", "01"), fixed.manifest["warnings"][0]["kind"])
assert fixed.status == "completed"
assert fixed.manifest["warnings"][0]["kind"] == "source_changed_version_unchanged"

forked = make("2", fixed=True).open_run(run_id).fork()  # the new version, beside the old run
print(forked.output("polish", "01"), "|", forked.manifest["fork_of"]["plan"][1]["reason"])
assert forked.output("polish", "01") == "hello!"
assert forked.steps("draft", "01")[0].labels == ["reused"]
