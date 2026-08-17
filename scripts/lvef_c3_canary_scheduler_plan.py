#!/usr/bin/env python3
"""Validate and bind the exact five-stage C3 canary scheduler authority.

This dependency-light module contains no scheduler client or execution path.
It freezes the future stage topology, verifies that every stage points to a
tracked production implementation, and models no-clobber stage-submission
claims for synthetic integration tests and later use by an owner-authorized
dispatcher.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


DEFAULT_PLAN_PATH = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "lvef_c3_canary_scheduler_plan_v1.json"
)
MANIFEST_HASH_PLACEHOLDER = "OWNER_PRIVATE_CANARY_MANIFEST_SHA256_REQUIRED"
ORDERED_STAGE_IDS = (
    "DOWNLOAD",
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "CANARY_FINALIZATION",
)
EXPECTED_ENTRYPOINTS = {
    "DOWNLOAD": (
        "scripts/lvef_c3_orchestration_core.py",
        "execute_exact_batch_download",
    ),
    "DICOM_EXTRACTION": (
        "scripts/lvef_c3_production_stages.py",
        "run_production_dicom_extraction",
    ),
    "ECHOPRIME_EMBEDDING": (
        "scripts/lvef_c3_production_stages.py",
        "run_production_echoprime",
    ),
    "BATCH_PRESERVATION": (
        "scripts/preserve_lvef_c3_production_batch.py",
        "preserve_batch",
    ),
    "CANARY_FINALIZATION": (
        "scripts/finalize_lvef_c3_production.py",
        "finalize_canary_preservation_receipt",
    ),
}
EXPECTED_PREDECESSORS = {
    stage_id: (() if index == 0 else (ORDERED_STAGE_IDS[index - 1],))
    for index, stage_id in enumerate(ORDERED_STAGE_IDS)
}
EXPECTED_REQUIRED_RECEIPTS = {
    "DOWNLOAD": (),
    "DICOM_EXTRACTION": ("download_verified_transition_receipt",),
    "ECHOPRIME_EMBEDDING": ("extraction_complete_transition_receipt",),
    "BATCH_PRESERVATION": ("study_pooling_complete_transition_receipt",),
    "CANARY_FINALIZATION": ("batch_preservation_receipt",),
}
EXPECTED_OUTPUTS = {
    "DOWNLOAD": (
        "download_resume_ledger",
        "verified_download_manifest",
        "download_verified_transition_receipt",
    ),
    "DICOM_EXTRACTION": (
        "dicom_audit",
        "extraction_manifest",
        "technical_disposition_manifest",
        "extraction_complete_transition_receipt",
    ),
    "ECHOPRIME_EMBEDDING": (
        "clip_embeddings",
        "clip_manifest",
        "study_embeddings",
        "study_manifest",
        "study_pooling_complete_transition_receipt",
    ),
    "BATCH_PRESERVATION": (
        "batch_preservation_manifest",
        "batch_preservation_receipt",
        "cache_retirement_eligible_transition_receipt",
    ),
    "CANARY_FINALIZATION": (
        "canary_finalization_receipt",
        "aggregate_safe_canary_summary",
    ),
}
EXPECTED_RESOURCES = {
    "DOWNLOAD": ("CPU", "24:00:00", 16, 0, None, 0),
    "DICOM_EXTRACTION": ("CPU", "48:00:00", 64, 0, None, 0),
    "ECHOPRIME_EMBEDDING": ("GPU", "24:00:00", 64, 1, "8.0", 48),
    "BATCH_PRESERVATION": ("CPU", "12:00:00", 32, 0, None, 0),
    "CANARY_FINALIZATION": ("CPU", "12:00:00", 32, 0, None, 0),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TIME_RE = re.compile(r"^[0-9]{2,3}:[0-5][0-9]:[0-5][0-9]$")
IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

PLAN_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "plan_id",
        "scheduler_system",
        "canary_manifest_sha256",
        "ordered_stage_ids",
        "scheduler_submission_count",
        "maximum_scheduler_submission_count",
        "gpu_stage_count",
        "stage_retry_count",
        "array_expansion_permitted",
        "automatic_resubmission_permitted",
        "production_continuation",
        "stages",
    }
)
STAGE_KEYS = frozenset(
    {
        "stage_id",
        "ordinal",
        "predecessors",
        "resource_class",
        "resources",
        "entrypoint",
        "required_predecessor_receipts",
        "outputs",
        "failure_boundary",
        "scheduler_submission_required",
        "array_task_count",
        "retry_count",
    }
)
RESOURCE_KEYS = frozenset(
    {
        "h_rt",
        "mem_total_gb",
        "gpu_count",
        "gpu_compute_capability_minimum",
        "gpu_memory_gb",
    }
)
ENTRYPOINT_KEYS = frozenset({"path", "sha256", "callable"})
CLAIM_LEDGER_KEYS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "scheduler_plan_sha256",
        "canary_manifest_sha256",
        "declared_submission_count",
        "submission_count",
        "active_stage_id",
        "failed_stage_id",
        "claims",
        "production_continuation_triggered",
    }
)
CLAIM_KEYS = frozenset(
    {
        "stage_id",
        "ordinal",
        "status",
        "predecessor_receipt_sha256s",
        "result_receipt_sha256",
    }
)


class CanarySchedulerPlanError(ValueError):
    """A fixed-code fail-closed scheduler-plan validation failure."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_SCHEDULER_PLAN_INVALID"
        super().__init__(code)
        self.code = code


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    result: MutableMapping[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CanarySchedulerPlanError("CANARY_STAGE_ENTRYPOINT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_exact_keys(value: Any, keys: frozenset[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise CanarySchedulerPlanError(code)
    return value


def _plain_positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CanarySchedulerPlanError(code)
    return value


def _require_sha256(value: Any, code: str) -> str:
    text = str(value)
    if SHA256_RE.fullmatch(text) is None:
        raise CanarySchedulerPlanError(code)
    return text


def _safe_repo_relative_path(value: Any) -> str:
    text = str(value)
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CanarySchedulerPlanError("CANARY_STAGE_ENTRYPOINT_PATH_UNSAFE")
    return text


def load_scheduler_plan(path: Path = DEFAULT_PLAN_PATH) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_PLAN_NOT_REGULAR")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs
        )
    except CanarySchedulerPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_PLAN_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_PLAN_NOT_OBJECT")
    return value


def validate_scheduler_plan(
    value: Mapping[str, Any],
    *,
    repository_root: Path | None = None,
    require_bound_manifest: bool = False,
) -> str:
    plan = _require_exact_keys(
        value, PLAN_KEYS, "CANARY_SCHEDULER_PLAN_SCHEMA_NOT_CLOSED"
    )
    manifest_hash = plan.get("canary_manifest_sha256")
    bound = SHA256_RE.fullmatch(str(manifest_hash)) is not None
    expected_status = "FROZEN_BOUND_UNAUTHORIZED" if bound else "FROZEN_UNBOUND_UNAUTHORIZED"
    if (
        plan.get("schema_version") != 1
        or plan.get("artifact_type") != "lvef_c3_canary_scheduler_plan_v1"
        or plan.get("status") != expected_status
        or plan.get("plan_id") != "lvef_c3_exact_five_production_faithful_v1"
        or plan.get("scheduler_system") != "SGE"
        or (not bound and manifest_hash != MANIFEST_HASH_PLACEHOLDER)
    ):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_PLAN_IDENTITY_INVALID")
    if require_bound_manifest and not bound:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_MANIFEST_NOT_BOUND")
    if (
        tuple(plan.get("ordered_stage_ids", ())) != ORDERED_STAGE_IDS
        or plan.get("scheduler_submission_count") != len(ORDERED_STAGE_IDS)
        or plan.get("maximum_scheduler_submission_count") != 5
        or plan.get("gpu_stage_count") != 1
        or plan.get("stage_retry_count") != 0
        or plan.get("array_expansion_permitted") is not False
        or plan.get("automatic_resubmission_permitted") is not False
        or plan.get("production_continuation") is not False
    ):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_TOPOLOGY_INVALID")
    stages = plan.get("stages")
    if not isinstance(stages, list) or len(stages) != len(ORDERED_STAGE_IDS):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_STAGE_COUNT_INVALID")
    gpu_stages = 0
    seen_outputs: set[str] = set()
    for index, stage_value in enumerate(stages):
        stage = _require_exact_keys(
            stage_value, STAGE_KEYS, "CANARY_SCHEDULER_STAGE_SCHEMA_NOT_CLOSED"
        )
        stage_id = ORDERED_STAGE_IDS[index]
        if (
            stage.get("stage_id") != stage_id
            or stage.get("ordinal") != index + 1
            or tuple(stage.get("predecessors", ()))
            != EXPECTED_PREDECESSORS[stage_id]
            or stage.get("scheduler_submission_required") is not True
            or stage.get("array_task_count") != 1
            or stage.get("retry_count") != 0
            or stage.get("failure_boundary")
            not in {
                "BLOCK_ALL_SUCCESSORS_NO_RESUBMISSION",
                "TERMINAL_CANARY_FAILURE_NO_RESUBMISSION",
            }
        ):
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_STAGE_SEMANTICS_INVALID")
        resources = _require_exact_keys(
            stage.get("resources"),
            RESOURCE_KEYS,
            "CANARY_SCHEDULER_RESOURCE_SCHEMA_NOT_CLOSED",
        )
        expected_resource = EXPECTED_RESOURCES[stage_id]
        observed_resource = (
            stage.get("resource_class"),
            resources.get("h_rt"),
            resources.get("mem_total_gb"),
            resources.get("gpu_count"),
            resources.get("gpu_compute_capability_minimum"),
            resources.get("gpu_memory_gb"),
        )
        if observed_resource != expected_resource or TIME_RE.fullmatch(
            str(resources.get("h_rt"))
        ) is None:
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_RESOURCE_INVALID")
        gpu_stages += int(stage.get("resource_class") == "GPU")
        entrypoint = _require_exact_keys(
            stage.get("entrypoint"),
            ENTRYPOINT_KEYS,
            "CANARY_SCHEDULER_ENTRYPOINT_SCHEMA_NOT_CLOSED",
        )
        entrypoint_path = _safe_repo_relative_path(entrypoint.get("path"))
        expected_path, expected_callable = EXPECTED_ENTRYPOINTS[stage_id]
        if (
            entrypoint_path != expected_path
            or entrypoint.get("callable") != expected_callable
        ):
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_ENTRYPOINT_CHANGED")
        expected_entrypoint_hash = _require_sha256(
            entrypoint.get("sha256"), "CANARY_SCHEDULER_ENTRYPOINT_HASH_INVALID"
        )
        if repository_root is not None:
            root = repository_root.resolve(strict=True)
            candidate = root.joinpath(*PurePosixPath(entrypoint_path).parts)
            try:
                candidate.resolve(strict=True).relative_to(root)
            except (OSError, ValueError) as exc:
                raise CanarySchedulerPlanError(
                    "CANARY_STAGE_ENTRYPOINT_OUTSIDE_REPOSITORY"
                ) from exc
            if sha256_file(candidate) != expected_entrypoint_hash:
                raise CanarySchedulerPlanError("CANARY_STAGE_ENTRYPOINT_HASH_MISMATCH")
        predecessors = stage.get("predecessors")
        required_receipts = stage.get("required_predecessor_receipts")
        outputs = stage.get("outputs")
        if (
            not isinstance(predecessors, list)
            or not isinstance(required_receipts, list)
            or not isinstance(outputs, list)
            or any(
                not isinstance(item, str) or not item
                for item in [*required_receipts, *outputs]
            )
            or len(outputs) != len(set(outputs))
            or seen_outputs.intersection(outputs)
            or tuple(required_receipts) != EXPECTED_REQUIRED_RECEIPTS[stage_id]
            or tuple(outputs) != EXPECTED_OUTPUTS[stage_id]
        ):
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_RECEIPT_GRAPH_INVALID")
        if index == 0 and required_receipts:
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_RECEIPT_GRAPH_INVALID")
        if index > 0 and not set(required_receipts).issubset(seen_outputs):
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_RECEIPT_GRAPH_INVALID")
        seen_outputs.update(outputs)
    if gpu_stages != 1:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_GPU_STAGE_COUNT_INVALID")
    return canonical_json_sha256(plan)


def bind_scheduler_plan(
    template: Mapping[str, Any], manifest_sha256: str, *, repository_root: Path | None = None
) -> dict[str, Any]:
    validate_scheduler_plan(template, repository_root=repository_root)
    if template.get("status") != "FROZEN_UNBOUND_UNAUTHORIZED":
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_TEMPLATE_ALREADY_BOUND")
    bound = copy.deepcopy(dict(template))
    bound["status"] = "FROZEN_BOUND_UNAUTHORIZED"
    bound["canary_manifest_sha256"] = _require_sha256(
        manifest_sha256, "CANARY_SCHEDULER_MANIFEST_HASH_INVALID"
    )
    validate_scheduler_plan(
        bound, repository_root=repository_root, require_bound_manifest=True
    )
    return bound


def initialize_claim_ledger(bound_plan: Mapping[str, Any]) -> dict[str, Any]:
    plan_sha = validate_scheduler_plan(bound_plan, require_bound_manifest=True)
    return {
        "schema_version": 1,
        "artifact_type": "lvef_c3_canary_scheduler_claim_ledger_v1",
        "status": "READY",
        "scheduler_plan_sha256": plan_sha,
        "canary_manifest_sha256": str(bound_plan["canary_manifest_sha256"]),
        "declared_submission_count": int(bound_plan["scheduler_submission_count"]),
        "submission_count": 0,
        "active_stage_id": None,
        "failed_stage_id": None,
        "claims": [],
        "production_continuation_triggered": False,
    }


def validate_claim_ledger(
    ledger: Mapping[str, Any], bound_plan: Mapping[str, Any]
) -> None:
    plan_sha = validate_scheduler_plan(bound_plan, require_bound_manifest=True)
    value = _require_exact_keys(
        ledger, CLAIM_LEDGER_KEYS, "CANARY_SCHEDULER_CLAIM_LEDGER_SCHEMA_NOT_CLOSED"
    )
    if (
        value.get("schema_version") != 1
        or value.get("artifact_type")
        != "lvef_c3_canary_scheduler_claim_ledger_v1"
        or value.get("scheduler_plan_sha256") != plan_sha
        or value.get("canary_manifest_sha256")
        != bound_plan.get("canary_manifest_sha256")
        or value.get("declared_submission_count") != 5
        or value.get("production_continuation_triggered") is not False
    ):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_CLAIM_AUTHORITY_MISMATCH")
    claims = value.get("claims")
    if not isinstance(claims, list) or value.get("submission_count") != len(claims):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_CLAIM_COUNT_MISMATCH")
    if len(claims) > int(bound_plan["scheduler_submission_count"]):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_SUBMISSION_LIMIT_EXCEEDED")
    active: list[str] = []
    failed: list[str] = []
    for index, claim_value in enumerate(claims):
        claim = _require_exact_keys(
            claim_value, CLAIM_KEYS, "CANARY_SCHEDULER_CLAIM_SCHEMA_NOT_CLOSED"
        )
        stage_id = ORDERED_STAGE_IDS[index]
        status = claim.get("status")
        if (
            claim.get("stage_id") != stage_id
            or claim.get("ordinal") != index + 1
            or status not in {"SUBMITTED", "PASS", "FAIL"}
        ):
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_CLAIM_ORDER_INVALID")
        predecessor_hashes = claim.get("predecessor_receipt_sha256s")
        expected_predecessor_count = 0 if index == 0 else 1
        if (
            not isinstance(predecessor_hashes, list)
            or len(predecessor_hashes) != expected_predecessor_count
            or any(SHA256_RE.fullmatch(str(item)) is None for item in predecessor_hashes)
        ):
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_PREDECESSOR_RECEIPT_INVALID")
        if index and predecessor_hashes != [claims[index - 1].get("result_receipt_sha256")]:
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_PREDECESSOR_RECEIPT_MISMATCH")
        result_hash = claim.get("result_receipt_sha256")
        if status == "SUBMITTED":
            if result_hash is not None:
                raise CanarySchedulerPlanError("CANARY_SCHEDULER_ACTIVE_RESULT_PRESENT")
            active.append(stage_id)
        elif SHA256_RE.fullmatch(str(result_hash)) is None:
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_RESULT_RECEIPT_INVALID")
        if status == "FAIL":
            failed.append(stage_id)
        if index and claims[index - 1].get("status") != "PASS":
            raise CanarySchedulerPlanError("CANARY_SCHEDULER_SUCCESSOR_WITHOUT_PASS")
    if len(active) > 1 or (active and claims[-1].get("status") != "SUBMITTED"):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_ACTIVE_STAGE_INVALID")
    if len(failed) > 1 or (failed and claims[-1].get("status") != "FAIL"):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_FAILURE_BOUNDARY_INVALID")
    expected_active = active[0] if active else None
    expected_failed = failed[0] if failed else None
    if value.get("active_stage_id") != expected_active or value.get(
        "failed_stage_id"
    ) != expected_failed:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_CLAIM_STATE_MISMATCH")
    expected_status = (
        "FAILED"
        if failed
        else "STAGE_ACTIVE"
        if active
        else "COMPLETE"
        if len(claims) == len(ORDERED_STAGE_IDS)
        else "READY"
    )
    if value.get("status") != expected_status:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_LEDGER_STATUS_INVALID")


def claim_stage_submission(
    ledger: Mapping[str, Any],
    bound_plan: Mapping[str, Any],
    stage_id: str,
    *,
    predecessor_receipt_sha256s: Sequence[str] = (),
) -> dict[str, Any]:
    validate_claim_ledger(ledger, bound_plan)
    if stage_id not in ORDERED_STAGE_IDS:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_UNDECLARED_STAGE")
    if ledger["status"] == "FAILED":
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_SUCCESSOR_AFTER_FAILURE")
    if ledger["status"] == "STAGE_ACTIVE":
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_STAGE_ALREADY_ACTIVE")
    if ledger["status"] == "COMPLETE":
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_SUBMISSION_LIMIT_EXCEEDED")
    claims = list(ledger["claims"])
    if any(claim["stage_id"] == stage_id for claim in claims):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_DUPLICATE_STAGE_SUBMISSION")
    if len(claims) >= int(bound_plan["scheduler_submission_count"]):
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_SUBMISSION_LIMIT_EXCEEDED")
    expected_stage = ORDERED_STAGE_IDS[len(claims)]
    if stage_id != expected_stage:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_DYNAMIC_OR_OUT_OF_ORDER_STAGE")
    expected_predecessor_count = 0 if not claims else 1
    predecessor_hashes = [
        _require_sha256(item, "CANARY_SCHEDULER_PREDECESSOR_RECEIPT_INVALID")
        for item in predecessor_receipt_sha256s
    ]
    if len(predecessor_hashes) != expected_predecessor_count:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_PREDECESSOR_RECEIPT_INVALID")
    if claims and claims[-1]["status"] != "PASS":
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_SUCCESSOR_WITHOUT_PASS")
    if claims and predecessor_hashes != [claims[-1]["result_receipt_sha256"]]:
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_PREDECESSOR_RECEIPT_MISMATCH")
    updated = copy.deepcopy(dict(ledger))
    updated["claims"].append(
        {
            "stage_id": stage_id,
            "ordinal": len(claims) + 1,
            "status": "SUBMITTED",
            "predecessor_receipt_sha256s": predecessor_hashes,
            "result_receipt_sha256": None,
        }
    )
    updated["submission_count"] += 1
    updated["active_stage_id"] = stage_id
    updated["status"] = "STAGE_ACTIVE"
    validate_claim_ledger(updated, bound_plan)
    return updated


def record_stage_result(
    ledger: Mapping[str, Any],
    bound_plan: Mapping[str, Any],
    stage_id: str,
    *,
    passed: bool,
    result_receipt_sha256: str,
) -> dict[str, Any]:
    validate_claim_ledger(ledger, bound_plan)
    if ledger.get("active_stage_id") != stage_id or ledger.get("status") != "STAGE_ACTIVE":
        raise CanarySchedulerPlanError("CANARY_SCHEDULER_RESULT_STAGE_NOT_ACTIVE")
    result_hash = _require_sha256(
        result_receipt_sha256, "CANARY_SCHEDULER_RESULT_RECEIPT_INVALID"
    )
    updated = copy.deepcopy(dict(ledger))
    claim = updated["claims"][-1]
    claim["status"] = "PASS" if passed else "FAIL"
    claim["result_receipt_sha256"] = result_hash
    updated["active_stage_id"] = None
    if passed:
        updated["status"] = (
            "COMPLETE"
            if updated["submission_count"] == updated["declared_submission_count"]
            else "READY"
        )
    else:
        updated["status"] = "FAILED"
        updated["failed_stage_id"] = stage_id
    validate_claim_ledger(updated, bound_plan)
    return updated


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN_PATH)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest-sha256")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = load_scheduler_plan(args.plan)
        if args.manifest_sha256 is not None:
            plan = bind_scheduler_plan(
                plan, args.manifest_sha256, repository_root=args.repository_root
            )
        plan_sha = validate_scheduler_plan(
            plan,
            repository_root=args.repository_root,
            require_bound_manifest=args.manifest_sha256 is not None,
        )
    except CanarySchedulerPlanError as exc:
        print(f"LVEF_C3_CANARY_SCHEDULER_PLAN=BLOCKED_{exc.code}")
        return 78
    print("LVEF_C3_CANARY_SCHEDULER_PLAN=PASS")
    print(f"LVEF_C3_CANARY_SCHEDULER_PLAN_SHA256={plan_sha}")
    print("FUTURE_SCHEDULER_SUBMISSIONS=5")
    print("SCHEDULER_SUBMISSION_PERFORMED=NO")
    print("PRODUCTION_CONTINUATION=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
