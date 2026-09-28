"""S3 and other fsspec storage: the same workflow, with run folders in a bucket.

What: ``storage=`` takes a local path or any fsspec URL: ``s3://bucket/prefix`` in production,
``memory://...`` here (no network, no credentials). Everything works the same: runs, gates, resume,
fork (a server-side copy of reused results), pin, cleanup and the read API.

How: ``pip install "hone-flow[s3]"`` and pass ``storage="s3://bucket/prefix"`` (credentials come from the
usual AWS environment; extra fsspec options via ``fk.FsspecStorage(url, **options)``). Steps still see
local files: inputs are downloaded into a temporary work folder and outputs uploaded when committed.
``run.output(..., local_dir=...)`` downloads a file output.

Why: large media belongs in object storage, and a run folder on S3 is as readable as a local one:
``s3://bucket/prefix/<name>/runs/<run_id>/manifest.json`` and friends.
"""

import tempfile
import uuid
from pathlib import Path

import hone_flow as fk

url = f"memory://bucket-{uuid.uuid4().hex[:6]}/projects/demo"  # use "s3://your-bucket/projects/demo"
wf = fk.Workflow("clips", storage=url)


@wf.step()
def encode(name: str, ctx: fk.Context) -> fk.File:
    path = ctx.new_file(f"{name}.mp4")
    path.write_bytes(b"\x00fake video " + name.encode())
    return fk.File(path)


@wf.gate()
def check(encode: fk.File) -> fk.File:
    return encode


run = wf.run([fk.Item("01", {"name": "intro"})])
print(run.status, run.location)
run.approve(step="check", item="01")
run.resume()
local = Path(tempfile.mkdtemp())
video = run.output("encode", "01", local_dir=local)
print("downloaded:", video.path, video.path.read_bytes())
new = run.fork(refresh=("check",))
run.pin()

assert run.location.startswith(url) and run.status == "completed"
assert video.path == local / "intro.mp4"
assert new.steps("encode", "01")[0].reused_from == run.run_id
assert [s.pinned for s in fk.open_runs(url, "clips").runs()] == [False, True]
