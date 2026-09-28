import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import hone_flow as fk
from hone_flow.cli import app, load_flow, load_items, parse_params


def test_help_lists_the_package() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "self-contained run folders" in result.output


def test_load_flow_imports_module_attribute(tmp_path: Path) -> None:
    (tmp_path / "myflows_cli.py").write_text(
        "import hone_flow as fk\nwf = fk.Workflow('w', storage='flows')\nnot_a_flow = 3\n"
    )
    assert load_flow("myflows_cli:wf").name == "w"
    with pytest.raises(typer.BadParameter, match="module:attribute"):
        load_flow("myflows_cli")
    with pytest.raises(typer.BadParameter, match="cannot load"):
        load_flow("myflows_cli:missing")
    with pytest.raises(typer.BadParameter, match=r"not a hone_flow\.Workflow"):
        load_flow("myflows_cli:not_a_flow")


def test_load_items_resolves_files_relative_to_the_items_file(tmp_path: Path) -> None:
    path = tmp_path / "items.json"
    path.write_text(json.dumps([{"id": "01", "inputs": {"lyrics": {"$file": "01.md"}, "mood": "calm"}}]))
    (item,) = load_items(path)
    assert item == fk.Item("01", {"lyrics": fk.File(tmp_path / "01.md"), "mood": "calm"})
    path.write_text('{"id": "01"}')
    with pytest.raises(typer.BadParameter, match="expected a JSON list"):
        load_items(path)
    path.write_text("not json")
    with pytest.raises(typer.BadParameter):
        load_items(path)


def test_parse_params_reads_json_or_strings() -> None:
    assert parse_params(["n=3", "style=noir", "tags=[1, 2]"]) == {"n": 3, "style": "noir", "tags": [1, 2]}
    with pytest.raises(typer.BadParameter, match="name=value"):
        parse_params(["oops"])
