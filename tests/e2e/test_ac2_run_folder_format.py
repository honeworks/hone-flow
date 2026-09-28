"""AC-2: the run folder layout and format v1."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow import run_format
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def files_of(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_ac2_run_folder_format(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "silhouette"})
    assert run.status == "awaiting_review"
    folder = tmp_path / "flows" / "song_video" / "runs" / run.run_id
    assert run.location == f"{folder}/"
    per_item = [
        "timeline/item_{i}/inputs/lyrics.md",
        "timeline/item_{i}/metadata.json",
        "timeline/item_{i}/output/timeline.json",
        "shotlist/item_{i}/inputs/style_guide.json",
        "shotlist/item_{i}/inputs/timeline.json",
        "shotlist/item_{i}/metadata.json",
        "shotlist/item_{i}/output/shotlist.json",
        "review_shotlist/item_{i}/inputs/shotlist.json",
        "review_shotlist/item_{i}/metadata.json",
        "review_shotlist/item_{i}/output/review_shotlist.json",
    ]
    expected = {
        "manifest.json",
        "spans.jsonl",
        "style_guide/metadata.json",
        "style_guide/output/style_guide.json",
        "reports/timing.json",
        "reports/output_sizes.json",
        "reports/summary.md",
    }
    expected |= {p.format(i=i) for p in per_item for i in ("01", "02")}
    assert set(files_of(folder)) == expected
    assert not (folder / "lease.json").exists()  # released at the end of the call

    manifest = run_format.parse(run_format.Manifest, (folder / "manifest.json").read_bytes(), "manifest.json")
    assert manifest.format_version == "1"
    assert (manifest.run_id, manifest.workflow, manifest.storage) == (
        run.run_id,
        "song_video",
        str(tmp_path / "flows"),
    )
    assert manifest.items[0].inputs["lyrics"].path == str(tmp_path / "songs" / "01.md")
    assert (
        manifest.items[0].inputs["lyrics"].sha256 == hashlib.sha256(b"first line\nsecond line\n").hexdigest()
    )
    assert [s.name for s in manifest.steps] == [
        "style_guide",
        "timeline",
        "shotlist",
        "review_shotlist",
        "render",
    ]
    assert manifest.state["render/01"] == "pending"
    for path in folder.rglob("metadata.json"):
        meta = run_format.parse(run_format.StepMetadata, path.read_bytes(), str(path))
        assert json.loads(path.read_text())["format_version"] == "1"
        step_folder = path.parent
        for name, info in meta.inputs.items():  # inputs/ holds exactly what the step received
            for file_name, file in info.files.items():
                data = (step_folder / "inputs" / file_name).read_bytes()
                assert hashlib.sha256(data).hexdigest() == file.sha256, (path, name)
        for info in meta.outputs.values():
            for file_name, file in info.files.items():
                assert (
                    hashlib.sha256((step_folder / "output" / file_name).read_bytes()).hexdigest()
                    == file.sha256
                )
    shotlist = json.loads((folder / "shotlist/item_01/metadata.json").read_text())
    assert shotlist["inputs"]["timeline"]["from"] == "step:timeline"
    assert shotlist["inputs"]["style_guide"]["from"] == "global:style_guide"
    assert shotlist["deterministic"] is False
    assert (
        json.loads((folder / "timeline/item_01/metadata.json").read_text())["inputs"]["lyrics"]["from"]
        == "item_input"
    )
    received = json.loads((folder / "shotlist/item_01/inputs/timeline.json").read_text())
    assert received == {"duration": 8.0, "lines": ["first line", "second line"]}

    elsewhere = tmp_path / "copied" / "song_video" / "runs" / run.run_id
    shutil.copytree(folder, elsewhere)  # a copy of the folder is readable on its own
    statuses = [r.status for r in run.steps()]
    timeline = run.output("timeline", "01")
    shutil.move(folder, tmp_path / "moved-away")
    copied = fk.open_runs(tmp_path / "copied", "song_video").open_run(run.run_id)
    assert copied.location == f"{elsewhere}/"
    assert copied.status == "awaiting_review"
    assert copied.output("timeline", "01") == timeline
    assert [r.status for r in copied.steps()] == statuses
    assert copied.output("review_shotlist", "01")["shots"][0]["line"] == "first line"
    shutil.move(tmp_path / "moved-away", folder)

    second = wf.run(write_songs(tmp_path), params={"style": "noir"})
    assert second.run_id != run.run_id
    assert (
        run.run_id.split("-")[0] <= second.run_id.split("-")[0]
    )  # sortable by creation time (1 s resolution)


def test_ac2_run_folder_format_every_file_is_read_only_and_leases_are_released(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "silhouette"})
    folder = Path(run.location)
    assert all((p.stat().st_mode & 0o222) == 0 for p in folder.rglob("*") if p.is_file())
    with pytest.raises(fk.RunNotFound):
        wf.open_run("20000101T000000Z-000000")
    assert wf.open_run(run.run_id).status == "awaiting_review"
