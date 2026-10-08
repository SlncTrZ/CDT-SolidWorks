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


def test_current_platform_release_lock_is_hash_pinned() -> None:
    lock = MODULE._release_lock_path()
    text = lock.read_text(encoding="utf-8")
    requirement_blocks = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not raw_line[:1].isspace():
            if current:
                requirement_blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        requirement_blocks.append(current)

    assert requirement_blocks
    for block in requirement_blocks:
        assert "==" in block[0], block[0]
        assert any("--hash=sha256:" in line for line in block), block[0]


def test_dependency_lock_payload_binds_exact_file() -> None:
    lock = MODULE._release_lock_path()
    payload = MODULE._dependency_lock_payload(lock)
    assert payload["filename"] == lock.name
    assert payload["sha256"] == MODULE._sha256(lock)
    assert payload["bytes"] == lock.stat().st_size


def test_clean_venv_does_not_inherit_system_packages(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append([str(arg) for arg in args])

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    MODULE._create_clean_venv(tmp_path / "venv")

    assert calls
    assert calls[0][:3] == [MODULE.sys.executable, "-m", "venv"]
    assert "--system-site-packages" not in calls[0]


def test_locked_environment_install_requires_hashes(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append([str(arg) for arg in args])

    lock = tmp_path / "release.lock"
    lock.write_text(
        "example==1.0 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    MODULE._install_locked_environment(tmp_path / "python", lock)

    command = calls[0]
    assert "--require-hashes" in command
    assert "--only-binary=:all:" in command
    assert "-r" in command


def test_all_platform_release_locks_cover_windows_marker_dependencies() -> None:
    for lock in (MODULE.LOCK_LINUX, MODULE.LOCK_WINDOWS):
        assert MODULE._lock_requirements(lock)

    windows = MODULE.LOCK_WINDOWS.read_text(encoding="utf-8").lower()
    assert "colorama==" in windows
    assert "pywin32==" in windows
