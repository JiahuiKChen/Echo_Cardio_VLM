#!/usr/bin/env python3
"""Write one aggregate-safe ADC quota-project setup attempt receipt."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command-exit-code", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command_exit_code < 0 or args.command_exit_code > 255:
        parser.error("--command-exit-code must be between 0 and 255")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = parse_args(argv)
    try:
        output_invalid = (
            not args.output.is_absolute()
            or args.output.is_symlink()
            or not args.output.parent.is_dir()
            or args.output.parent.is_symlink()
            or args.output.parent.stat().st_uid != os.getuid()
            or args.output.exists()
        )
    except OSError:
        output_invalid = True
    if output_invalid:
        print('{"error_code":"QUOTA_STAGE_OUTPUT_INVALID","status":"FAIL"}')
        return 2
    passed = args.command_exit_code == 0
    payload = {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "quota_project_setup_command_succeeded": passed,
        "authority_audit_started": False,
        "identifiers_emitted": False,
        "tokens_emitted": False,
        "credential_paths_emitted": False,
    }
    descriptor: int | None = None
    created = False
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(args.output, open_flags, 0o600)
        created = True
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise OSError("unsafe quota-stage output")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        print('{"error_code":"QUOTA_STAGE_WRITE_FAILED","status":"FAIL"}')
        return 2
    print(
        json.dumps(
            {
                "status": payload["status"],
                "quota_project_setup_command_succeeded": passed,
                "identifiers_emitted": False,
                "tokens_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
