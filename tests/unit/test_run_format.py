import json
import re
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow import run_format
from hone_flow.run_format import Manifest, RunFolder, StepMetadata, new_run_id
from hone_flow.testing import MemoryStorage

DOC = Path(__file__).parents[2] / "docs" / "run-format.md"


def documented_examples() -> list[tuple[str, dict[str, object]]]:
    text = DOC.read_text()
    found = re.findall(r"<!-- model: (\w+) -->\s*```json\n(.*?)```", text, flags=re.S)
    return [(name, json.loads(body)) for name, body in found]


def field_names(model: type[run_format.Model]) -> set[str]:
    return {info.serialization_alias or info.alias or name for name, info in model.model_fields.items()}


def test_every_documented_file_matches_its_model() -> None:
    examples = documented_examples()
    assert [name for name, _ in examples] == ["Manifest", "StepMetadata", "Lease"]
    for name, example in examples:
        model = getattr(run_format, name)
        model.model_validate(example)
        assert set(example) == field_names(model), name  # every key documented, no undocumented key


def test_documented_nested_objects_list_every_key() -> None:
    examples = dict(documented_examples())
    manifest, meta = examples["Manifest"], examples["StepMetadata"]
    assert set(manifest["items"][0]["inputs"]["lyrics"]) == field_names(run_format.ItemInput)  # type: ignore[index]
    assert set(manifest["steps"][0]) == field_names(run_format.StepInfo)  # type: ignore[index]
    assert set(manifest["calls"][0]) == field_names(run_format.CallInfo)  # type: ignore[index]
    notification = manifest["notifications"][0]  # type: ignore[index]
    assert set(notification) == field_names(run_format.NotificationInfo)
    assert set(notification["deliveries"]["ops"]) == field_names(run_format.DeliveryInfo)
    assert set(meta["attempts"][0]) == field_names(run_format.Attempt)  # type: ignore[index]
    assert set(meta["inputs"]["timeline"]) == field_names(run_format.InputInfo)  # type: ignore[index]


@pytest.mark.parametrize("model", [StepMetadata, Manifest])
def test_unknown_format_version_asks_to_upgrade(model: type[run_format.Model]) -> None:
    example = {"format_version": "2", "renamed_everything": True}  # a v2 file need not validate as v1
    with pytest.raises(fk.HoneFlowError, match="upgrade hone-flow"):
        run_format.parse(model, json.dumps(example).encode(), "x.json")


def test_dump_writes_the_documented_keys_and_round_trips() -> None:
    example = dict(documented_examples())["StepMetadata"]
    meta = StepMetadata.model_validate(example)
    written = json.loads(run_format.dump(meta))
    assert written == example  # "from" (not "source"), nulls kept as keys, seed kept
    assert run_format.dump(run_format.parse(StepMetadata, run_format.dump(meta), "k")) == run_format.dump(
        meta
    )


def test_readers_ignore_unknown_keys() -> None:
    example = dict(documented_examples())["StepMetadata"] | {"added_later": 1}
    meta = run_format.parse(StepMetadata, json.dumps(example).encode(), "x/metadata.json")
    assert meta.step == "shotlist"
    assert "added_later" not in run_format.dump(meta).decode()


def test_run_ids_are_unique_and_sortable() -> None:
    ids = [new_run_id() for _ in range(50)]
    assert len(set(ids)) == 50
    assert all(re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", i) for i in ids)


def test_run_ids_sort_by_creation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    class Clock(datetime):
        now_value = datetime(2026, 9, 27, 14, 3, 11, tzinfo=UTC)

        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return cls.now_value

    monkeypatch.setattr(run_format, "datetime", Clock)
    first = new_run_id()
    assert first.startswith("20260927T140311Z-")
    Clock.now_value = datetime(2026, 9, 27, 14, 3, 12, tzinfo=UTC)
    assert new_run_id() > first


def manifest(run_id: str = "r1") -> Manifest:
    return Manifest(
        run_id=run_id,
        workflow="w",
        workflow_version="1",
        storage="memory",
        location="memory/w/runs/r1/",
        hone_flow_version="0.1.0",
        created_at="t",
        updated_at="t",
        status="running",
        trace_id="0" * 32,
    )


def test_manifest_writes_are_conditional() -> None:
    storage = MemoryStorage()
    folder = RunFolder(storage, "w/runs/r1")
    folder.write_manifest(manifest(), create=True)
    with pytest.raises(fk.errors.WriteConflict):
        RunFolder(storage, "w/runs/r1").write_manifest(manifest(), create=True)
    other = RunFolder(storage, "w/runs/r1")
    theirs = other.read_manifest()
    theirs.status = "completed"
    other.write_manifest(theirs)  # the other writer changes the manifest ...
    mine = manifest()
    mine.status = "failed"
    with pytest.raises(fk.errors.WriteConflict):
        folder.write_manifest(mine)  # ... so this one's last read is stale
    assert folder.read_manifest().status == "completed"
    before = folder.read_manifest()
    folder.write_manifest(before)
    assert folder.read_manifest().updated_at != "t"


def test_a_manifest_write_needs_a_read_first() -> None:
    storage = MemoryStorage()
    RunFolder(storage, "w/runs/r1").write_manifest(manifest(), create=True)
    with pytest.raises(fk.HoneFlowError, match="read the manifest before writing it"):
        RunFolder(storage, "w/runs/r1").write_manifest(manifest())


def test_folder_keys_and_metadata_round_trip() -> None:
    storage = MemoryStorage()
    folder = RunFolder(storage, "w/runs/r1")
    assert folder.location == "memory/w/runs/r1/"
    assert (
        folder.step_key("timeline", "01", "output", "timeline.json")
        == "w/runs/r1/timeline/item_01/output/timeline.json"
    )
    assert folder.step_key("style", None) == "w/runs/r1/style"
    assert folder.read_meta("timeline", "01") is None
    meta = StepMetadata(
        run_id="r1", step="timeline", item="01", kind="step", status="done", version="1", source_hash=""
    )
    folder.write_meta(meta)
    assert folder.read_meta("timeline", "01") == meta
    with pytest.raises(fk.HoneFlowError, match=r"no manifest\.json"):
        folder.read_manifest()
