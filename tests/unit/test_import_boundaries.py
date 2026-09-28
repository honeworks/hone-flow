import subprocess
import sys


def test_core_import_pulls_no_adapters_or_extras() -> None:
    code = (
        "import sys, hone_flow\n"
        "bad = [m for m in sys.modules if m.startswith(('hone_flow.adapters', 'hone_models', 'hone_select',"
        " 'hone_taste', 'hone_lens', 'fsspec', 's3fs', 'psutil', 'pynvml', 'typer', 'rich'))]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_testing_is_reachable_from_the_package() -> None:
    import hone_flow as fk

    assert fk.testing.FakeGpuLease is not None
