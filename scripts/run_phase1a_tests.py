#!/usr/bin/env python3
"""Run dependency-light Phase 1A tests without requiring pytest on SCC."""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import traceback


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    test_files = sorted((root / "tests").glob("test_*.py"))
    passed = 0
    failed = 0
    skipped = 0
    for index, test_file in enumerate(test_files):
        spec = importlib.util.spec_from_file_location(f"phase1a_test_{index}", test_file)
        if spec is None or spec.loader is None:
            print(f"FAIL {test_file.name}: import specification unavailable")
            failed += 1
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            print(f"FAIL {test_file.name}: module import failed")
            traceback.print_exc()
            failed += 1
            continue
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            if inspect.signature(function).parameters:
                print(f"SKIP {test_file.name}::{name}: requires a fixture")
                skipped += 1
                continue
            try:
                function()
            except Exception:
                print(f"FAIL {test_file.name}::{name}")
                traceback.print_exc()
                failed += 1
            else:
                print(f"PASS {test_file.name}::{name}")
                passed += 1
    print(f"SUMMARY passed={passed} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
