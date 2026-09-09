#!/usr/bin/env python3
"""Run one manifest-bound CPU analysis chain; never submit or retry a job.

Every scientific stage replays the maintained authority. A prior execution claim
requires explicit evidence reconciliation outside this worker, not an automatic
restart of development or held-out evaluation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from typing import Any, Callable, Mapping

import lvef_revalidation_authority as authority
import run_lvef_revalidation as runner


STAGES = ("validate", "seal", "development", "freeze", "release-test", "evaluate", "report", "render")
THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
PRIOR_EXECUTION_FILES = (
    "stage_chain.claim.restricted.json", "analysis_lock.restricted.json",
    "development.claim.restricted.json", "development_models.restricted.json",
    "development_receipt.restricted.json", "frozen_models.restricted.json",
    "model_freeze.restricted.json", "test_release.restricted.json",
    "test_evaluation.claim.restricted.json", "test_predictions.restricted.json",
    "test_evaluation.restricted.json", "paired_report.restricted.json",
    "aggregate_bundle.restricted.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def commands(ctx: Mapping[str, Any], manifest_path: Path) -> list[tuple[str, list[str]]]:
    repository = Path(ctx["run"]["repository_root"])
    root = ctx["root"]
    common = ["--run-manifest", str(manifest_path), "--run-manifest-sha256", ctx["manifest_sha256"]]
    result = []
    for stage in STAGES:
        if stage == "render":
            command = [sys.executable, str(repository / "scripts/render_lvef_revalidation_results.py"),
                       "--bundle", str(root / "aggregate_bundle.restricted.json"),
                       "--output", str(root / "figures")]
        else:
            command = [sys.executable, str(repository / "scripts/run_lvef_revalidation.py"),
                       "validate" if stage == "seal" else stage, *common]
            if stage == "seal":
                command.append("--seal")
        result.append((stage, command))
    return result


def execute(ctx: Mapping[str, Any], manifest_path: Path, *,
            environment: Mapping[str, str] | None = None,
            command_runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    env = dict(os.environ if environment is None else environment)
    authority.require(re.fullmatch(r"[1-9][0-9]*", env.get("JOB_ID", "")) is not None
                      and env.get("NSLOTS") == "4"
                      and all(env.get(key) == "4" for key in THREAD_KEYS),
                      "ANALYSIS_CPU_SCHEDULER_PROFILE_REQUIRED")
    root = Path(ctx["root"])
    authority.require(not any((root / name).exists() for name in PRIOR_EXECUTION_FILES)
                      and not (root / "stage_logs").exists() and not (root / "figures").exists(),
                      "ANALYSIS_PRIOR_EXECUTION_REQUIRES_RECONCILIATION")
    claim = {"artifact_type": "lvef_revalidation_stage_chain_claim_v1",
             "status": "CPU_STAGE_CHAIN_CLAIMED", "analysis_commit": ctx["run"]["analysis_commit"],
             "run_manifest_sha256": ctx["manifest_sha256"], "scheduler_job_id": env["JOB_ID"],
             "host": socket.gethostname(), "slots": 4, "claimed_at_utc": utc_now(),
             "automatic_retries_permitted": False}
    claim_sha = authority.publish(root / "stage_chain.claim.restricted.json", claim)
    log_root = root / "stage_logs"
    log_root.mkdir(mode=0o700)
    completed = []
    for stage, command in commands(ctx, manifest_path):
        start = {"stage": stage, "status": "STARTED", "started_at_utc": utc_now(),
                 "stage_chain_claim_sha256": claim_sha, "scheduler_job_id": env["JOB_ID"]}
        authority.publish(log_root / (stage + ".started.restricted.json"), start)
        stdout_path = log_root / (stage + ".stdout.restricted")
        stderr_path = log_root / (stage + ".stderr.restricted")
        with os.fdopen(os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as out:
            with os.fdopen(os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as err:
                process = command_runner(command, cwd=ctx["run"]["repository_root"],
                                         env=env, stdout=out, stderr=err, check=False)
        outcome = {**start, "status": "PASS" if process.returncode == 0 else "FAILED",
                   "finished_at_utc": utc_now(), "returncode": process.returncode,
                   "stdout_sha256": authority.digest(stdout_path.read_bytes()),
                   "stderr_sha256": authority.digest(stderr_path.read_bytes())}
        authority.publish(log_root / (stage + ".finished.restricted.json"), outcome)
        if process.returncode != 0:
            failure = {"status": "BLOCKED_ANALYSIS_STAGE_CHAIN", "failed_stage": stage,
                       "returncode": process.returncode, "completed_stages": completed,
                       "stage_chain_claim_sha256": claim_sha, "scheduler_job_id": env["JOB_ID"],
                       "automatic_retry_attempted": False}
            authority.publish(root / "stage_chain.failure.restricted.json", failure)
            return failure
        completed.append(stage)
    final = {"status": "PASS_ANALYSIS_STAGE_CHAIN", "completed_stages": completed,
             "stage_chain_claim_sha256": claim_sha, "scheduler_job_id": env["JOB_ID"],
             "completed_at_utc": utc_now(), "automatic_retry_attempted": False,
             "visual_quality_review_pending": True,
             "poster_uploaded": False, "messages_sent": False}
    sha = authority.publish(root / "stage_chain.complete.restricted.json", final)
    return {**final, "completion_sha256": sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--run-manifest-sha256", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        ctx = runner.context(args.run_manifest, args.run_manifest_sha256)
        result = execute(ctx, args.run_manifest)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result["status"] == "PASS_ANALYSIS_STAGE_CHAIN" else 78
    except Exception as exc:
        code = getattr(exc, "code", "ANALYSIS_STAGE_CHAIN_FAILURE")
        if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{3,100}", code) is None:
            code = "ANALYSIS_STAGE_CHAIN_FAILURE"
        print(json.dumps({"status": "BLOCKED_ANALYSIS_STAGE_CHAIN", "code": code}))
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
