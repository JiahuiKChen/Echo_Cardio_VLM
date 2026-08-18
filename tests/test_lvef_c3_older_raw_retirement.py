#!/usr/bin/env python3
"""Dependency-light R5E raw-retirement and capacity-binding regressions."""
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

import capture_lvef_c3_post_reallocation_capacity as capacity
import lvef_c3_full_sequential as sequential
import retire_lvef_c3_older_raw_duplicates as raw_retirement


def _expect(code: str, function) -> None:
    try:
        function()
    except Exception as exc:
        assert getattr(exc, "code", str(exc)) == code
    else:  # pragma: no cover
        raise AssertionError(f"expected {code}")


def _private_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    os.chmod(path, 0o600)


def test_r5e_retirement_scope_and_constants_are_exact() -> None:
    assert raw_retirement.TARGET_BATCHES == (
        "c3_batch_000", "c3_batch_001"
    )
    assert raw_retirement.EXPECTED_DELETE_FILES == 37_068
    assert raw_retirement.EXPECTED_DELETE_BYTES == 133_822_359_346
    assert raw_retirement.EXPECTED_RETAINED_FILES == 121_220
    assert raw_retirement.EXPECTED_RETAINED_BYTES == 18_131_756_871
    assert raw_retirement.EXPECTED_R4_METADATA_SHA256 == (
        "c820806c26ba9644061e7d1c92e79de48d69b7b91fa3d0d09763d028284fc4c6"
    )
    leaves = raw_retirement._target_leaves(Path("/fixed/older"))
    assert leaves == (
        Path("/fixed/older/raw/c3_batch_000/objects"),
        Path("/fixed/older/raw/c3_batch_001/objects"),
    )


def test_r5e_destructive_reachability_has_no_caller_target() -> None:
    source = (SCRIPTS / "retire_lvef_c3_older_raw_duplicates.py").read_text()
    tree = ast.parse(source)
    rmtree_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rmtree"
    ]
    assert len(rmtree_calls) == 1
    assert isinstance(rmtree_calls[0].args[0], ast.Name)
    parser_source = source[source.index("def main("):]
    assert "--target" not in parser_source
    assert "--root" not in parser_source
    assert "--glob" not in parser_source
    assert "Path(args" not in parser_source


def test_r5e_leaf_membership_is_exact_and_rejects_unsafe_entries() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        older = Path(temporary).resolve() / "older"
        leaf = older / "raw" / "c3_batch_000" / "objects"
        leaf.mkdir(parents=True)
        _private_file(leaf / "a.dcm", b"abc")
        _private_file(leaf / "b.dcm", b"defg")
        entries = (
            {"relative_path": "raw/c3_batch_000/objects/a.dcm", "size_bytes": 3},
            {"relative_path": "raw/c3_batch_000/objects/b.dcm", "size_bytes": 4},
        )
        raw_retirement._validate_leaf_from_manifest(older, leaf, entries)

        unsafe_cases = (
            ("unexpected.dcm", lambda path: _private_file(path, b"x")),
            (".nfs0001", lambda path: _private_file(path, b"x")),
            ("x.partial", lambda path: _private_file(path, b"x")),
            ("link.dcm", lambda path: path.symlink_to("a.dcm")),
        )
        for name, create in unsafe_cases:
            path = leaf / name
            create(path)
            _expect(
                "OLDER_RAW_LEAF_AUTHORITY_INVALID",
                lambda: raw_retirement._validate_leaf_from_manifest(
                    older, leaf, entries
                ),
            )
            path.unlink()

        hardlink = leaf / "hard.dcm"
        os.link(leaf / "a.dcm", hardlink)
        _expect(
            "OLDER_RAW_LEAF_AUTHORITY_INVALID",
            lambda: raw_retirement._validate_leaf_from_manifest(
                older, leaf, entries
            ),
        )
        hardlink.unlink()
        fifo = leaf / "fifo.dcm"
        os.mkfifo(fifo, 0o600)
        _expect(
            "OLDER_RAW_LEAF_AUTHORITY_INVALID",
            lambda: raw_retirement._validate_leaf_from_manifest(
                older, leaf, entries
            ),
        )


def test_r5e_quiescence_fails_closed_when_process_inventory_fails() -> None:
    completed_qstat = SimpleNamespace(
        returncode=0, stderr=b"", stdout=b"<job_info></job_info>"
    )
    failed_ps = SimpleNamespace(returncode=1, stderr=b"failed", stdout=b"")
    with (
        mock.patch.object(
            raw_retirement.subprocess,
            "run",
            side_effect=(completed_qstat, failed_ps),
        ),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        _expect(
            "OLDER_RAW_QUIESCENCE_AUTHORITY_INVALID",
            raw_retirement._quiescent,
        )


def test_r5e_quiescence_rejects_generic_process_cwd_in_attempt() -> None:
    completed_qstat = SimpleNamespace(
        returncode=0, stderr=b"", stdout=b"<job_info></job_info>"
    )
    completed_ps = SimpleNamespace(
        returncode=0, stderr=b"", stdout=b"4242 /bin/zsh\n"
    )
    cwd = (
        raw_retirement.PRODUCTION_ROOT
        / "attempts"
        / raw_retirement.OLDER_ATTEMPT_ID
        / "raw"
    )
    with (
        mock.patch.object(
            raw_retirement.subprocess,
            "run",
            side_effect=(completed_qstat, completed_ps),
        ),
        mock.patch.object(raw_retirement.os, "readlink", return_value=str(cwd)),
        mock.patch.dict(os.environ, {"USER": "synthetic"}),
    ):
        _expect("OLDER_RAW_ACTIVE_PROCESS_EXISTS", raw_retirement._quiescent)


def test_r5e_retained_role_classifier_is_closed_and_body_roles_are_exact() -> None:
    cases = {
        "raw/c3_batch_000/objects/a.dcm": "RAW_DICOM_PAYLOAD",
        "extracted_cache/c3_batch_001/dicom_extraction.partial/clips/aa/a.npz": (
            "EXTRACTED_NPZ_CACHE"
        ),
        "batches/c3_batch_000/echoprime/clip_embeddings.restricted.npz": (
            "CLIP_EMBEDDINGS"
        ),
        "batches/c3_batch_000/echoprime/study_embeddings.restricted.npz": (
            "STUDY_EMBEDDINGS"
        ),
        "raw/c3_batch_000/receipts/a.verification.json": (
            "DOWNLOAD_VERIFICATION_RECEIPTS"
        ),
        "raw/c3_batch_000/verified_download_manifest.restricted.csv": (
            "SOURCE_AND_BATCH_MANIFESTS"
        ),
        "extracted_cache/c3_batch_001/dicom_extraction.partial/dicom_audit.restricted.csv": (
            "DICOM_AUDIT_AND_EXTRACTION_MANIFESTS"
        ),
        "batches/c3_batch_000/download_resume_ledger.restricted.json": (
            "POOLING_PRESERVATION_RETIREMENT_FINALIZATION_RECEIPTS"
        ),
        "full_submission_claim.restricted.json": (
            "CLAIM_PLAN_SUBMISSION_AND_ENVIRONMENT_AUTHORITIES"
        ),
        "scheduler/job.o7183952.1": "SCHEDULER_LOGS",
        "batches/c3_batch_001/failure.summary.json": "OTHER_CONTROL_EVIDENCE",
        "unknown.bin": "UNCLASSIFIED",
    }
    assert {
        path: raw_retirement._retained_role(path) for path in cases
    } == cases
    assert len(raw_retirement.RETAINED_ROLE_NAMES) == 13


def test_r5e_receipt_and_aggregate_summary_are_closed_and_bound() -> None:
    retained = {
        "file_count": raw_retirement.EXPECTED_RETAINED_FILES,
        "total_bytes": raw_retirement.EXPECTED_RETAINED_BYTES,
        "file_metadata_sha256": "a" * 64,
        "directory_topology_sha256": "b" * 64,
        "role_inventory_sha256": "d" * 64,
        "control_content_sha256": "e" * 64,
    }
    diagnostic_sha = "f" * 64
    manifest = {
        "governing_commit": "c" * 40,
        "retained_role_inventory_sha256": retained[
            "role_inventory_sha256"
        ],
        "retained_control_content_sha256": retained[
            "control_content_sha256"
        ],
        "diagnostic_evidence_authority_sha256": diagnostic_sha,
    }
    manifest_payload = raw_retirement._canonical(manifest)
    receipt = raw_retirement._post_receipt(
        manifest_payload=manifest_payload,
        manifest=manifest,
        deleted_files=raw_retirement.EXPECTED_DELETE_FILES,
        deleted_bytes=raw_retirement.EXPECTED_DELETE_BYTES,
        retained=retained,
        r4_authority={
            "metadata_stat_sha256": (
                raw_retirement.EXPECTED_R4_METADATA_SHA256
            )
        },
        status="PASS_OLDER_RAW_DUPLICATES_RETIRED",
    )
    raw_retirement._validate_receipt(
        receipt, manifest_payload=manifest_payload
    )
    receipt_payload = raw_retirement._canonical(receipt)
    summary = raw_retirement._summary_from_receipt(
        receipt, receipt_payload=receipt_payload
    )
    assert set(summary) == raw_retirement.SUMMARY_KEYS
    assert summary["receipt_sha256"] == hashlib.sha256(
        receipt_payload
    ).hexdigest()
    raw_retirement._validate_safe_export(raw_retirement._canonical(summary))
    for key in ("actual_deleted_files", "actual_deleted_bytes", "receipt_sha256"):
        changed = copy.deepcopy(summary)
        changed[key] = -1 if key != "receipt_sha256" else "0" * 64
        assert changed != raw_retirement._summary_from_receipt(
            receipt, receipt_payload=receipt_payload
        )


def test_r5e_capacity_gain_classification_is_exact() -> None:
    historical = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    base = {
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_PASS,
        "live_research_quota_bytes": historical,
        "live_research_usage_bytes": 1,
        "live_research_filesystem_available_bytes": (
            sequential.SUCCESSOR_INCREMENT_BYTES
            + sequential.SUCCESSOR_REQUIRED_RESERVE_BYTES
        ),
        "remaining_file_slots": sequential.SUCCESSOR_REQUIRED_FILE_SLOTS,
    }
    assert sequential._capacity_gain_source(
        base, evidence_role="R5E_PRE_CLEANUP"
    ) == "EXISTING_HEADROOM"
    allocation = {**base, "live_research_quota_bytes": historical + 10**12}
    assert sequential._capacity_gain_source(
        allocation, evidence_role="R5E_PRE_CLEANUP"
    ) == "ALLOCATION"
    blocked_pre = {
        **base,
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        "live_research_usage_bytes": 527_008_808_960,
    }
    cleanup = {
        **base,
        "live_research_usage_bytes": 393_186_449_614,
    }
    assert sequential._capacity_gain_source(
        cleanup,
        evidence_role="R5E_POST_CLEANUP",
        pre_cleanup_observation=blocked_pre,
    ) == "CLEANUP"
    assert sequential._capacity_gain_source(
        blocked_pre, evidence_role="R5E_PRE_CLEANUP"
    ) == "NONE"


def test_r5e_claim_v3_binds_capacity_role_and_retirement_receipt() -> None:
    assert sequential.FULL_SUBMISSION_CLAIM_KEYS.isdisjoint(
        {
            "dynamic_capacity_receipt_sha256",
            "raw_retirement_receipt_sha256",
            "capacity_gain_source",
        }
    )
    assert sequential.FRESH_FULL_SUBMISSION_CLAIM_V3_KEYS == frozenset({
        *sequential.FRESH_FULL_SUBMISSION_CLAIM_V2_KEYS,
        "capacity_evidence_role", "capacity_gain_source",
        "raw_retirement_status", "raw_retirement_receipt_sha256",
    })
    run = SimpleNamespace(
        authority=SimpleNamespace(governing_commit="a" * 40),
        attempt_id="lvef_c3_full_" + "b" * 16 + "_" + "a" * 8,
        plan_sha256="c" * 64,
        plan={"authority": {"git_commit": "a" * 40}},
        runtime_authority={"git_commit": "a" * 40},
        launch_authority_sha256="d" * 64,
    )
    claim = sequential._expected_submission_claim(
        run,
        capacity_receipt_sha256="e" * 64,
        dynamic_capacity_receipt_sha256="f" * 64,
        capacity_evidence_role="R5E_POST_CLEANUP",
        capacity_gain_source="CLEANUP",
        raw_retirement_status="PASS_OLDER_RAW_DUPLICATES_RETIRED",
        raw_retirement_receipt_sha256="1" * 64,
        qsub_environment_sha256="2" * 64,
    )
    assert claim["schema_version"] == 3
    assert claim["raw_retirement_receipt_sha256"] == "1" * 64
    assert claim["capacity_gain_source"] == "CLEANUP"
    _expect(
        "FULL_SEQUENTIAL_PREPARED_CAPACITY_INVALID",
        lambda: sequential._expected_submission_claim(
            run,
            capacity_receipt_sha256="e" * 64,
            dynamic_capacity_receipt_sha256="f" * 64,
            capacity_evidence_role="R5E_POST_CLEANUP",
            capacity_gain_source="CLEANUP",
            raw_retirement_status="PASS_OLDER_RAW_DUPLICATES_RETIRED",
            raw_retirement_receipt_sha256=(
                "NOT_APPLICABLE_CLEANUP_SKIPPED"
            ),
            qsub_environment_sha256="2" * 64,
        ),
    )


def test_r5e_capacity_publisher_allows_only_fixed_pre_and_post_pairs() -> None:
    allowed = capacity.DYNAMIC_SUCCESSOR_ALLOWED_EVIDENCE_BASENAME_PAIRS
    assert (
        capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
        capacity.R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
    ) in allowed
    assert (
        capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
        capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
    ) in allowed
    assert len(allowed) == 3


def test_r5e_cleanup_requires_the_exact_failed_pre_cleanup_capacity_pair() -> None:
    blocked = capacity.DynamicSuccessorCapacityCapture(
        receipt={},
        receipt_payload=b"sealed-pre-capacity\n",
        observation={
            "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
        },
    )
    with (
        mock.patch.object(
            capacity,
            "load_dynamic_successor_capacity_capture",
            return_value=blocked,
        ) as loader,
        mock.patch.object(
            capacity,
            "validate_production_dynamic_successor_capacity_capture",
            return_value=blocked,
        ),
    ):
        authority = raw_retirement._pre_cleanup_capacity_authority("c" * 40)
    assert authority["status"] == capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING
    assert authority["receipt_sha256"] == hashlib.sha256(
        blocked.receipt_payload
    ).hexdigest()
    assert loader.call_args.kwargs["restricted_receipt_path"].name == (
        capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME
    )

    passed = copy.deepcopy(blocked.observation)
    passed["status"] = capacity.DYNAMIC_SUCCESSOR_STATUS_PASS
    pass_capture = capacity.DynamicSuccessorCapacityCapture(
        receipt={}, receipt_payload=b"pass\n", observation=passed
    )
    with (
        mock.patch.object(
            capacity,
            "load_dynamic_successor_capacity_capture",
            return_value=pass_capture,
        ),
        mock.patch.object(
            capacity,
            "validate_production_dynamic_successor_capacity_capture",
            return_value=pass_capture,
        ),
    ):
        _expect(
            "OLDER_RAW_UNAUTHORIZED_CLEANUP_AFTER_CAPACITY_PASS",
            lambda: raw_retirement._pre_cleanup_capacity_authority("c" * 40),
        )


def test_r5e_post_capacity_admission_requires_pre_failure_and_cleanup_receipt() -> None:
    assert "require_pass=False" in inspect.getsource(
        sequential.run_dynamic_successor_capacity_seal
    )
    historical = capacity.EXPECTED_RESEARCH_QUOTA_KIB * 1024
    blocked = {
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_ALLOCATION_PENDING,
        "live_research_quota_bytes": historical,
        "live_research_usage_bytes": 527_008_808_960,
        "live_research_filesystem_available_bytes": 1_674_160_635_904,
        "remaining_file_slots": 33_052_951,
    }
    passed = {
        **blocked,
        "status": capacity.DYNAMIC_SUCCESSOR_STATUS_PASS,
        "live_research_usage_bytes": 393_186_449_614,
    }
    pre = capacity.DynamicSuccessorCapacityCapture({}, b"pre", blocked)
    post = capacity.DynamicSuccessorCapacityCapture({}, b"post", passed)
    pre_authority = {
        "status": blocked["status"],
        "receipt_basename": (
            capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME
        ),
        "receipt_bytes": len(pre.receipt_payload),
        "receipt_sha256": hashlib.sha256(pre.receipt_payload).hexdigest(),
        "summary_basename": (
            capacity.R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME
        ),
        "summary_bytes": len(capacity._canonical(pre.observation)),
        "summary_sha256": hashlib.sha256(
            capacity._canonical(pre.observation)
        ).hexdigest(),
    }
    with tempfile.TemporaryDirectory() as temporary:
        production = Path(temporary).resolve()
        owner = production / "owner_private"
        owner.mkdir(mode=0o700)
        for basename in (
            capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_PRE_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
            capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
            capacity.R5E_POST_CLEANUP_AGGREGATE_SUMMARY_BASENAME,
        ):
            _private_file(owner / basename, b"sealed")
        run = SimpleNamespace(
            production_root=production,
            authority=SimpleNamespace(governing_commit="c" * 40),
        )

        pass_requirements: list[tuple[str, bool]] = []

        def load(
            _run, *, restricted_basename, summary_basename, require_pass=True
        ):
            del _run, summary_basename
            pass_requirements.append((restricted_basename, require_pass))
            return post if "post_cleanup" in restricted_basename else pre

        with (
            mock.patch.object(sequential, "_load_capacity_pair", side_effect=load),
            mock.patch.object(
                raw_retirement,
                "validate_retired_state",
                return_value={
                    "status": "PASS_OLDER_RAW_DUPLICATES_RETIRED",
                    "receipt_sha256": "d" * 64,
                    "pre_cleanup_capacity_authority": pre_authority,
                },
            ),
        ):
            admission = sequential._load_fixed_capacity_admission(run)
        assert admission.capture is post
        assert admission.evidence_role == "R5E_POST_CLEANUP"
        assert admission.capacity_gain_source == "CLEANUP"
        assert admission.raw_retirement_receipt_sha256 == "d" * 64
        assert pass_requirements == [
            (
                capacity.R5E_PRE_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
                False,
            ),
            (
                capacity.R5E_POST_CLEANUP_RESTRICTED_RECEIPT_BASENAME,
                True,
            ),
        ]

        pre_pass = capacity.DynamicSuccessorCapacityCapture({}, b"pre", passed)
        with (
            mock.patch.object(
                sequential,
                "_load_capacity_pair",
                side_effect=lambda _run, *, restricted_basename,
                summary_basename, require_pass=True: (
                    post if "post_cleanup" in restricted_basename else pre_pass
                ),
            ),
            mock.patch.object(
                raw_retirement,
                "validate_retired_state",
                return_value={
                    "status": "PASS_OLDER_RAW_DUPLICATES_RETIRED",
                    "receipt_sha256": "d" * 64,
                    "pre_cleanup_capacity_authority": pre_authority,
                },
            ),
        ):
            _expect(
                "FULL_SEQUENTIAL_UNAUTHORIZED_CLEANUP_AFTER_CAPACITY_PASS",
                lambda: sequential._load_fixed_capacity_admission(run),
            )

        wrong_pre = {**pre_authority, "receipt_sha256": "0" * 64}
        with (
            mock.patch.object(sequential, "_load_capacity_pair", side_effect=load),
            mock.patch.object(
                raw_retirement,
                "validate_retired_state",
                return_value={
                    "status": "PASS_OLDER_RAW_DUPLICATES_RETIRED",
                    "receipt_sha256": "d" * 64,
                    "pre_cleanup_capacity_authority": wrong_pre,
                },
            ),
        ):
            _expect(
                "FULL_SEQUENTIAL_OLDER_RAW_RETIREMENT_INVALID",
                lambda: sequential._load_fixed_capacity_admission(run),
            )


def test_r5e_retirement_module_never_opens_dicom_or_npz_payloads() -> None:
    source = (SCRIPTS / "retire_lvef_c3_older_raw_duplicates.py").read_text()
    tree = ast.parse(source)
    forbidden_names = {
        "sha256_file", "validate_extracted_npz", "np.load",
        "materialize_verified_download_manifest",
    }
    observed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                observed.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                observed.add(node.func.attr)
    assert forbidden_names.isdisjoint(observed)
