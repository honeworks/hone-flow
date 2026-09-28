"""Revising part of an output: a rejected producer receives its previous output.

What: when a gate rejects a result with a note that names only part of it ("scene 2 goes outside the
safe area"), the producer reruns with the note (``review_note``) *and* the output it produced last time
(``previous``), so it can keep the parts that passed and fix only the others.

How: declare ``previous: T | None = None`` on the producer (next to ``review_note``). It is ``None`` on a
first attempt and in a fork; after a rejection it is the rejected output, loaded like ``run.output``.
``ctx.previous_output()`` returns the same value on demand.

Why: rewriting every scene after one failed costs minutes of GPU time per revision and throws away work
that passed. Without ``previous`` an app has to dig the old output out of its own run folder.
"""

import tempfile

from pydantic import BaseModel

import hone_flow as fk


class Code(BaseModel):
    scenes: dict[str, str]


calls: list[str] = []
wf = fk.Workflow("shorts", storage=tempfile.mkdtemp())


@wf.step(deterministic=False)
def code(count: int, review_note: str | None = None, previous: Code | None = None) -> Code:
    if previous is None:
        calls.extend(str(n) for n in range(1, count + 1))
        return Code(scenes={str(n): f"scene {n}, draft" for n in range(1, count + 1)})
    failed = [n.strip() for n in (review_note or "").split(",")]
    calls.extend(failed)  # only the scenes the note names are written again
    return Code(scenes={n: f"scene {n}, fixed" if n in failed else c for n, c in previous.scenes.items()})


@wf.gate()
def render_check(code: Code) -> Code:
    return code


run = wf.run([fk.Item("episode-1", {"count": 4})])
run.reject(step="render_check", item="episode-1", note="2, 4")
run.resume()
print(run.output("code", "episode-1").scenes)
print("scenes written:", calls)

assert run.output("code", "episode-1").scenes["1"] == "scene 1, draft"  # kept
assert run.output("code", "episode-1").scenes["4"] == "scene 4, fixed"
assert calls == ["1", "2", "3", "4", "2", "4"]
