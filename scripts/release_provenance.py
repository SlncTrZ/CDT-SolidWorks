"""Release provenance — Build and verify an exact-source wheel manifest."""
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


def _wheel_payload(wheel: Path) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
    forbidden = [name for name in names if name.startswith("_private/") or "/_private/" in name or name.endswith("AGENTS.md") or name.endswith("CLAUDE.md")]
    if forbidden:
        raise SystemExit(f"forbidden release payload: {forbidden}")
    return {"filename": wheel.name, "sha256": _sha256(wheel), "bytes": wheel.stat().st_size, "file_count": len(names)}


def build(output: Path) -> dict[str, object]:
    source = _source_identity()
    with tempfile.TemporaryDirectory(prefix="cdt-sw-wheel-") as tmp:
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", tmp, "."], cwd=ROOT, check=True)
        wheels = list(Path(tmp).glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"expected exactly one wheel, got {len(wheels)}")
        output.parent.mkdir(parents=True, exist_ok=True)
        final_wheel = output.parent / wheels[0].name
        final_wheel.write_bytes(wheels[0].read_bytes())
    manifest = {
        "schema_version": 1,
        "source": source,
        "wheel": _wheel_payload(final_wheel),
        "build_runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(), "platform": platform.platform()},
    }
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = _source_identity()
    if manifest.get("source") != source:
        raise SystemExit("manifest source identity does not match current clean HEAD")
    wheel = manifest_path.parent / manifest["wheel"]["filename"]
    actual = _wheel_payload(wheel)
    if actual != manifest["wheel"]:
        raise SystemExit("wheel identity does not match manifest")
    with tempfile.TemporaryDirectory(prefix="cdt-sw-install-") as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)], check=True, stdout=subprocess.DEVNULL)
        code = "import importlib.metadata as m; import cdt_solidworks.cli; assert m.version('cdt-solidworks') == '0.1.0'"
        subprocess.run([str(python), "-c", code], cwd=tmp, check=True)


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
