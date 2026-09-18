"""Release provenance contract tests."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_provenance", ROOT / "scripts" / "release_provenance.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_wheel_payload_rejects_private_or_agent_files(tmp_path: Path) -> None:
    import zipfile
    wheel = tmp_path / "bad.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("cdt_solidworks/__init__.py", "")
        archive.writestr("_private/secret.md", "no")
    try:
        MODULE._wheel_payload(wheel)
    except SystemExit as exc:
        assert "forbidden release payload" in str(exc)
    else:
        raise AssertionError("private payload must fail closed")


def test_source_identity_rejects_dirty_tracked_tree(monkeypatch) -> None:
    values = iter(("abc123", " M src/example.py"))
    monkeypatch.setattr(MODULE, "_run", lambda *args, **kwargs: next(values))
    try:
        MODULE._source_identity()
    except SystemExit as exc:
        assert "tracked source tree must be clean" in str(exc)
    else:
        raise AssertionError("dirty tracked source must fail closed")
