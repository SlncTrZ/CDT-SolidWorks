"""Release provenance — Build and verify an exact-source wheel manifest.
Wing: release | Topic: deterministic-provenance | Updated: 2026-09-19 13:45
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_GUIDE = ROOT / "docs" / "TOOL_GUIDE.md"
PACKAGED_GUIDE = ROOT / "src" / "cdt_solidworks" / "platform" / "PROVIDER_GUIDE.md"
LOCK_LINUX = ROOT / "requirements" / "release-py312-linux-x86_64.lock"
LOCK_WINDOWS = ROOT / "requirements" / "release-py312-windows-amd64.lock"


def _run(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_identity() -> dict[str, object]:
    head = _run("git", "rev-parse", "HEAD")
    status = _run("git", "status", "--porcelain", "--untracked-files=no")
    if status:
        raise SystemExit("tracked source tree must be clean before release provenance is emitted")
    return {"git_sha": head, "tracked_tree_clean": True}


def _release_lock_path() -> Path:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("release provenance requires CPython 3.12")
    machine = platform.machine().lower()
    if sys.platform == "linux" and machine in {"x86_64", "amd64"}:
        return LOCK_LINUX
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return LOCK_WINDOWS
    raise SystemExit(
        f"unsupported release-lock target: platform={sys.platform!r}, machine={machine!r}"
    )


def _lock_requirements(lock: Path) -> list[str]:
    if not lock.is_file():
        raise SystemExit(f"release dependency lock missing: {lock}")
    requirements: list[str] = []
    for raw_line in lock.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or "--hash=sha256:" not in line:
            raise SystemExit(f"release lock entry is not exact/hash-pinned: {line}")
        if " @ " in line or line.startswith(("-e ", "--editable ")):
            raise SystemExit(f"release lock contains non-index dependency: {line}")
        requirements.append(line)
    if not requirements:
        raise SystemExit(f"release dependency lock is empty: {lock}")
    return requirements


def _dependency_lock_payload(lock: Path) -> dict[str, object]:
    requirements = _lock_requirements(lock)
    return {
        "filename": lock.name,
        "sha256": _sha256(lock),
        "bytes": lock.stat().st_size,
        "requirement_count": len(requirements),
    }


def _guide_payload() -> dict[str, object]:
    public = PUBLIC_GUIDE.read_bytes()
    packaged = PACKAGED_GUIDE.read_bytes()
    if packaged != public:
        raise SystemExit(
            "packaged provider guide must be byte-identical to docs/TOOL_GUIDE.md"
        )
    digest = hashlib.sha256(public).hexdigest()
    return {
        "sha256": digest,
        "bytes": len(public),
        "public_path": str(PUBLIC_GUIDE.relative_to(ROOT)),
        "packaged_path": str(PACKAGED_GUIDE.relative_to(ROOT)),
        "byte_identical": True,
    }


def _wheel_payload(wheel: Path) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
    forbidden = [
        name
        for name in names
        if name.startswith("_private/")
        or "/_private/" in name
        or name.endswith("AGENTS.md")
        or name.endswith("CLAUDE.md")
    ]
    if forbidden:
        raise SystemExit(f"forbidden release payload: {forbidden}")
    return {
        "filename": wheel.name,
        "sha256": _sha256(wheel),
        "bytes": wheel.stat().st_size,
        "file_count": len(names),
    }


def _create_clean_venv(venv: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _install_locked_environment(python: Path, lock: Path) -> None:
    _lock_requirements(lock)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "-r",
            str(lock),
        ],
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)


def _runtime_payload(python: Path) -> dict[str, str]:
    code = (
        "import json, platform, sys; "
        "print(json.dumps({'python': platform.python_version(), "
        "'implementation': platform.python_implementation(), "
        "'executable': sys.executable, 'platform': platform.platform()}))"
    )
    runtime = json.loads(
        subprocess.check_output([str(python), "-c", code], text=True).strip()
    )
    pip_version = subprocess.check_output(
        [str(python), "-m", "pip", "--version"], text=True
    ).split()[1]
    return {
        "python": runtime["python"],
        "implementation": runtime["implementation"],
        "platform": runtime["platform"],
        "pip": pip_version,
    }


def _build_wheel(python: Path, wheel_dir: Path) -> Path:
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            ".",
        ],
        cwd=ROOT,
        check=True,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel, got {len(wheels)}")
    return wheels[0]


def build(output: Path) -> dict[str, object]:
    source = _source_identity()
    lock = _release_lock_path()
    dependency_lock = _dependency_lock_payload(lock)
    guide = _guide_payload()

    with tempfile.TemporaryDirectory(prefix="cdt-sw-build-") as tmp:
        temp_root = Path(tmp)
        venv = temp_root / "venv"
        wheel_dir = temp_root / "wheel"
        wheel_dir.mkdir()
        _create_clean_venv(venv)
        python = _venv_python(venv)
        _install_locked_environment(python, lock)
        built_wheel = _build_wheel(python, wheel_dir)

        output.parent.mkdir(parents=True, exist_ok=True)
        final_wheel = output.parent / built_wheel.name
        final_wheel.write_bytes(built_wheel.read_bytes())
        build_runtime = _runtime_payload(python)

    manifest = {
        "schema_version": 2,
        "source": source,
        "dependency_lock": dependency_lock,
        "provider_guide": guide,
        "wheel": _wheel_payload(final_wheel),
        "build_runtime": build_runtime,
    }
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2:
        raise SystemExit("unsupported release provenance manifest schema")

    source = _source_identity()
    if manifest.get("source") != source:
        raise SystemExit("manifest source identity does not match current clean HEAD")

    lock = _release_lock_path()
    dependency_lock = _dependency_lock_payload(lock)
    if manifest.get("dependency_lock") != dependency_lock:
        raise SystemExit("dependency lock identity does not match manifest")

    guide = _guide_payload()
    if manifest.get("provider_guide") != guide:
        raise SystemExit("provider guide identity does not match manifest")

    wheel = manifest_path.parent / manifest["wheel"]["filename"]
    actual = _wheel_payload(wheel)
    if actual != manifest["wheel"]:
        raise SystemExit("wheel identity does not match manifest")

    with tempfile.TemporaryDirectory(prefix="cdt-sw-verify-") as tmp:
        temp_root = Path(tmp)
        venv = temp_root / "venv"
        _create_clean_venv(venv)
        python = _venv_python(venv)
        _install_locked_environment(python, lock)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--force-reinstall",
                str(wheel),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run([str(python), "-m", "pip", "check"], check=True)

        expected_guide_sha = guide["sha256"]
        source_root = str(ROOT.resolve())
        code = (
            "import hashlib, importlib.metadata as m, importlib.resources as r, "
            "json, pathlib, cdt_solidworks.cli; "
            "module=pathlib.Path(cdt_solidworks.cli.__file__).resolve(); "
            f"assert not str(module).startswith({source_root!r}); "
            "assert m.version('cdt-solidworks') == '0.1.0'; "
            "guide=r.files('cdt_solidworks.platform').joinpath('PROVIDER_GUIDE.md').read_bytes(); "
            f"assert hashlib.sha256(guide).hexdigest() == {expected_guide_sha!r}; "
            "print(json.dumps({'module': str(module), 'guide_sha256': hashlib.sha256(guide).hexdigest()}))"
        )
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        subprocess.run([str(python), "-c", code], cwd=temp_root, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build")
    build_p.add_argument("--manifest", type=Path, required=True)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.manifest), sort_keys=True))
    else:
        verify(args.manifest)
        print("PROVENANCE_VERIFY_OK")


if __name__ == "__main__":
    main()
