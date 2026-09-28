"""Pinning and cleanup: keep the runs that matter, delete the rest.

What: ``run.pin()`` copies a finished run to ``pinned_runs/<id>/`` and verifies every file's sha256.
``wf.cleanup(keep_last=..., older_than=...)`` deletes run folders under ``runs/`` only: never a pinned
copy and never a run another process is working on. Listings show one logical run per id.

How: pin the runs you ship or compare against; call cleanup from a cron job or by hand
(``dry_run=True`` first). A cleaned pinned run still opens (from the archive) and can be forked, but it
can no longer be resumed or reviewed: an archive never changes.

Why: run folders hold full copies of inputs and outputs, so they grow; cleanup keeps storage in check
without risking the runs you decided to keep.
"""

import tempfile
from datetime import timedelta

import hone_flow as fk

wf = fk.Workflow("pin_demo", storage=tempfile.mkdtemp())


@wf.step()
def mix(track: str) -> str:
    return f"mixed {track}"


runs = [wf.run([fk.Item("01", {"track": f"take {n}"})]) for n in (1, 2, 3)]
keeper = runs[0]
keeper.pin()
print("pinned:", keeper.run_id, keeper.pinned)

report = wf.cleanup(keep_last=1, dry_run=True)
print("would delete:", report.deleted, "| kept:", report.kept)
report = wf.cleanup(keep_last=1, older_than=timedelta(0))
print("deleted:", report.deleted)
listed = {s.run_id: s.pinned for s in wf.runs()}
print("listed:", listed)

assert set(report.deleted) == {runs[0].run_id, runs[1].run_id}  # the pinned run's runs/ copy too ...
assert listed == {runs[2].run_id: False, keeper.run_id: True}  # ... but its archive stays listed
archived = wf.open_run(keeper.run_id)
assert archived.location.endswith(f"/pinned_runs/{keeper.run_id}/")
assert archived.output("mix", "01") == "mixed take 1"
try:
    archived.resume()
except fk.HoneFlowError as exc:
    print("resume on an archive:", exc)
else:
    raise AssertionError("an archive cannot be resumed")
again = archived.fork(refresh=("mix",))  # but it can be forked
assert again.status == "completed"
