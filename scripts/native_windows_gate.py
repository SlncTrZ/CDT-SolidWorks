"""Run SolidWorks qualification in the logged-on user's Windows session."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def source_hashes():
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    )
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in sorted(set(result.stdout.splitlines()))
        if (ROOT / name).is_file()
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--native-only", action="store_true")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if any(evidence.iterdir()):
        parser.error("evidence directory must be empty; preserve prior receipts")
    report = {"state": "running", "pytest_exit_code": None}
    started = time.monotonic()
    code = 1
    try:
        import pytest
        from cdt_solidworks.native.api import WindowsComApi
        from cdt_solidworks.native import session as session_module

        report["windows_session_id"] = WindowsComApi._process_session_id()
        if report["windows_session_id"] == 0:
            raise RuntimeError("native qualification requires an interactive Windows session")
        report["source_module"] = str(Path(session_module.__file__).resolve())
        assert Path(session_module.__file__).resolve().is_relative_to(ROOT / "src")
        report["source_before"] = source_hashes()
        targets = (
            ["tests/body/test_native_windows_fixture.py",
             "tests/surface/test_native_windows_fixture.py",
             "tests/sheetmetal/test_native_windows_fixture.py"]
            if args.native_only else ["tests"]
        )
        report["targets"] = targets
        (evidence / "runner-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        # Keep the provider unpatched; native fixtures perform geometry/reopen checks.
        with (evidence / "pytest.log").open("w", encoding="utf-8") as output:
            with redirect_stdout(output), redirect_stderr(output):
                code = int(pytest.main([
                    *targets, "-q", "--tb=short", "-p", "no:cacheprovider",
                    "--junitxml=" + str(evidence / "pytest.xml"),
                    "--basetemp=" + str(evidence / "artifacts"),
                ]))
        report["pytest_exit_code"] = code
        suites = list(ET.parse(evidence / "pytest.xml").getroot().iter("testsuite"))
        report["test_counts"] = {
            key: sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        if args.native_only and report["test_counts"] != {
            "tests": 6, "failures": 0, "errors": 0, "skipped": 0,
        }:
            code = 1
        report["source_after"] = source_hashes()
        if report["source_before"] != report["source_after"]:
            code = 1
            report["source_changed"] = True
        report["artifacts"] = [
            {"path": str(path.relative_to(evidence)), "bytes": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted((evidence / "artifacts").rglob("*.sldprt"))
            if not path.name.startswith("~$")
        ]
        report["state"] = "passed" if code == 0 else "failed"
    except BaseException as exc:
        report["state"] = "failed"
        report["error_kind"] = type(exc).__name__
        code = 1
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        (evidence / "runner-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
