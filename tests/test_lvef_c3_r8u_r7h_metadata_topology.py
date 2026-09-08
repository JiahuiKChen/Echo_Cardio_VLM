#!/usr/bin/env python3
"""Producer-shaped, body-free R7H retained metadata topology regressions."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import traceback
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import lvef_c3_full_sequential as sequential
import lvef_c3_r8r_recovery_continuation as historical
import test_lvef_c3_full_sequential as existing_topology
import test_lvef_c3_production_stages_and_finalizer as existing_receipts

SHA = "a" * 64


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for parent in (path, *path.parents):
        if parent.name == "attempts":
            parent.chmod(0o700)
            break
        if parent.name in {"", "tmp", "private"}:
            break
        parent.chmod(0o700)


def _write(path: Path, value: Any) -> str:
    _private_directory(path.parent)
    payload = value if type(value) is bytes else (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode()
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def _runtime(attempt: Path) -> dict[str, str]:
    value = {key: SHA for key in sequential.core.RUNTIME_AUTHORITY_KEYS}
    if attempt.name == sequential.R8U_R7H_SCIENTIFIC_ATTEMPT_ID:
        value.update(git_commit=sequential.R8U_R7H_SCIENTIFIC_COMMIT,
                     batch_plan_sha256=sequential.R8U_R7H_PLAN_SHA256)
    else:
        value.update(git_commit=(sequential.R8U_R7H_INITIAL_EXTRACTION_PRODUCER_COMMIT
                     if attempt.name.endswith("_b805fd1a") else "b" * 40),
                     batch_plan_sha256="c" * 64)
    return value


def _retired_metadata(attempt: Path, ordinal: int, *, legacy: bool = False,
                      transitions: bool = True, initial_legacy: bool = False) -> str:
    batch_id = f"c3_batch_{ordinal:03d}"
    parent = attempt / "extracted_cache" / batch_id / "dicom_extraction"
    runtime = _runtime(attempt)
    final = existing_receipts._batch_receipt(ordinal)
    final.update(attempt_id=attempt.name, governing_commit=runtime["git_commit"],
                 source_commit=runtime["git_commit"], split_version=f"split_map_sha256:{SHA}")
    for key in ("batch_plan_sha256", "checkpoint_sha256", "environment_receipt_sha256",
                "orchestration_contract_sha256"):
        final[key] = runtime[key]
    final["checkpoint_checksum"] = runtime["checkpoint_sha256"]
    if legacy:
        # Reuse the maintained producer receipt schema without a second schema.
        final = {key: value for key, value in final.items()
                 if key in sequential.finalizer.LEGACY_BATCH_RECEIPT_KEYS_V2}
        final.update(schema_version=1, artifact_type="lvef_c3_batch_finalization_receipt_v2")
        for key in ("n_multiframe_cines", "n_extracted_clips", "n_unique_clip_keys", "n_clip_embeddings"):
            final[key] = final["n_expected_objects"] - 1
        if initial_legacy:
            final["production_stage_wrapper_sha256"] = sequential.R8U_R7H_INITIAL_EXTRACTION_WRAPPER_SHA256
    artifacts = {}
    for name, field in (
        ("dicom_audit.restricted.csv", "dicom_audit_sha256"),
        ("extraction_manifest.restricted.csv", "extraction_manifest_sha256"),
        ("technical_disposition_manifest.restricted.csv", "technical_disposition_manifest_sha256"),
    ):
        if legacy and name.startswith("technical_"):
            continue
        artifacts[name] = _write(parent / name, b"synthetic_metadata_header\nsynthetic_metadata_value\n")
        final[field] = artifacts[name]
    summary_keys = sequential.R8U_R7H_LEGACY_EXTRACTION_SUMMARY_KEYS if legacy else sequential.stages.DICOM_EXTRACTION_SUMMARY_KEYS_V2
    if initial_legacy:
        summary_keys = sequential.R8U_R7H_INITIAL_EXTRACTION_SUMMARY_KEYS
    summary = {key: True if key.startswith("all_") or key in {"clip_keys_unique", "physical_source_keys_unique"} else 0 for key in summary_keys}
    summary.update(schema_version=1 if legacy else 2,
                   artifact_type=f"lvef_c3_batch_dicom_extraction_summary_v{1 if legacy else 2}",
                   status="PASS_DICOM_EXTRACTION" if legacy else "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
                   identifiers_emitted=False, paths_emitted=False)
    if not legacy:
        summary["technical_disposition_manifest_sha256"] = final["technical_disposition_manifest_sha256"]
    artifacts["dicom_extraction.summary.json"] = _write(parent / "dicom_extraction.summary.json", summary)
    _write(parent / "stage_completion_receipt.restricted.json", {
        "schema_version": 1, "artifact_type": "lvef_c3_stage_completion_receipt_v1",
        "status": "PASS_STAGE_OUTPUT_ATOMICALLY_FINALIZABLE", "stage": "DICOM_EXTRACTION",
        "batch_id": batch_id, "attempt_id": attempt.name, "runtime_authority": runtime,
        "input_manifest_sha256": SHA, "artifacts": artifacts,
    })
    if transitions:
        predecessor = SHA
        for from_state, to_state, artifact in (
            ("DOWNLOAD_VERIFIED", "DICOM_AUDIT_COMPLETE", "dicom_audit.restricted.csv"),
            ("DICOM_AUDIT_COMPLETE", "EXTRACTION_COMPLETE", "extraction_manifest.restricted.csv"),
        ):
            transition = {
                "schema_version": 2, "receipt_type": "lvef_c3_state_transition_v2",
                "attempt_id": attempt.name, "batch_id": batch_id, "from_state": from_state,
                "to_state": to_state, "status": "PASS", "authority": runtime,
                "input_receipt_sha256": [predecessor], "output_manifest_sha256": artifacts[artifact],
            }
            _write(parent / "transition_receipts" / f"{to_state.lower()}.restricted.json", transition)
            predecessor = sequential.core.canonical_json_sha256(transition)
    return _write(attempt / "batches" / batch_id / "preservation" / "batch_finalization_receipt.restricted.json", final)


def _publication_controls(attempt: Path) -> dict[str, tuple[str, ...]]:
    bindings = {}
    for name, (family, producer, _claim_sha, evidence_name, _evidence_sha) in sequential.R8U_R7H_RETAINED_PUBLICATION_CLAIMS.items():
        keys = getattr(historical, f"R8U_{family.upper()}_PUBLICATION_CLAIM_KEYS")
        claim = {key: SHA for key in keys}
        claim.update(getattr(historical, f"_r8u_{family}_common")(
            artifact_type=f"lvef_c3_r8u_{family}_publication_claim_v1",
            status=f"AUTHORIZED_EXCLUSIVE_R8U_{family.upper()}_BATCH16_PUBLICATION",
            implementation_commit=producer))
        claim.update(target_role="extracted_cache/c3_batch_015/dicom_extraction", target_absent=True)
        for key in ("competing_active_jobs", "competing_active_processes", "cloud_requests",
                    "downloads", "dicom_body_reads", "dicom_extraction_executions", "npz_body_reads"):
            claim[key] = 0
        claim_sha = _write(attempt / "extracted_cache/c3_batch_015" / name / "claim.restricted.json", claim)
        evidence_sha = _write(attempt / evidence_name, {"publication_claim_sha256": claim_sha})
        bindings[name] = (family, producer, claim_sha, evidence_name, evidence_sha)
    return bindings


@contextmanager
def synthetic_retained_metadata_topology(root: Path, *, current_batches: int = 16,
                                        foreign: bool = True):
    """Real files/readers/classifier; only immutable synthetic authority is bound."""
    root = root.resolve()
    expected_partial, observation = existing_topology._r7h_expected_partial_fixture(root)
    attempt = root / "attempts" / sequential.R8U_R7H_SCIENTIFIC_ATTEMPT_ID
    hashes = [_retired_metadata(attempt, index, transitions=index != 15)
              for index in range(current_batches)]
    bindings = _publication_controls(attempt)
    foreign_attempt = root / "attempts" / ("lvef_c3_full_" + "c" * 16 + "_bbbbbbbb")
    initial_foreign_attempt = root / "attempts" / ("lvef_c3_full_" + "c" * 16 + "_b805fd1a")
    if foreign:
        for index in range(2):
            _retired_metadata(foreign_attempt, index, legacy=True)
        _retired_metadata(initial_foreign_attempt, 0, legacy=True, initial_legacy=True)
        for index in (3, 4):
            _write(foreign_attempt / "extracted_cache" / f"c3_batch_{index:03d}" /
                   "dicom_extraction.partial/failure.summary.json", {
                "status": "FAIL_EXTRACTION_GATE", "error_code": "SYNTHETIC_FAILURE",
                "identifiers_emitted": False, "paths_emitted": False,
            })
    with ExitStack() as stack:
        stack.enter_context(existing_topology._r7h_small_observation_constants(observation))
        stack.enter_context(mock.patch.object(sequential.finalizer,
            "R8U_R7D_FINALIZED_PREFIX_RECEIPT_SHA256", tuple(hashes[:16])))
        stack.enter_context(mock.patch.object(sequential, "R8U_R7H_RETAINED_PUBLICATION_CLAIMS", bindings))
        fixture = SimpleNamespace(root=root, current_attempt=attempt, foreign_attempt=foreign_attempt,
            initial_foreign_attempt=initial_foreign_attempt,
            sealed_history=existing_topology._r7h_sealed_history, prefix_hashes=tuple(hashes[:16]),
            metadata_roots=current_batches + (3 if foreign else 0), expected_partial=expected_partial)
        fixture.classify = lambda: sequential.validate_r8u_r7h_extraction_cache_topology(
            root, current_attempt_id=attempt.name, sealed_history_validator=fixture.sealed_history)
        yield fixture


def test_current_legacy_and_consumed_claim_metadata_coexist_with_sealed_partials() -> None:
    with tempfile.TemporaryDirectory() as directory:
        with synthetic_retained_metadata_topology(Path(directory)) as fixture:
            topology = fixture.classify()
            assert topology.retained_metadata_roots == 19
            assert topology.retained_metadata_files == 16 * 7 - 2 + 3 * 6 + 2
            assert topology.retained_metadata_bytes > 0
            assert topology.active_scientific_caches == 0
            assert topology.unknown_cache_like_roots == 0
            assert topology.sealed_cross_attempt_terminal_failed_caches == 2
            assert topology.sealed_current_attempt_batch16_failed_partials == 1
            assert topology.historical_partial_adoptable is False


def test_initial_legacy_summary_requires_exact_producer_schema_and_wrapper() -> None:
    assert len(sequential.R8U_R7H_INITIAL_EXTRACTION_SUMMARY_KEYS) == 18
    assert len(sequential.R8U_R7H_LEGACY_EXTRACTION_SUMMARY_KEYS) == 29
    for mutation in ("extra_field", "missing_field", "wrapper", "producer"):
        with tempfile.TemporaryDirectory() as directory:
            with synthetic_retained_metadata_topology(Path(directory)) as fixture:
                if mutation == "producer":
                    _retired_metadata(fixture.foreign_attempt, 5, legacy=True, initial_legacy=True)
                elif mutation == "wrapper":
                    path = fixture.initial_foreign_attempt / "batches/c3_batch_000/preservation/batch_finalization_receipt.restricted.json"
                    value = json.loads(path.read_bytes())
                    value["production_stage_wrapper_sha256"] = "f" * 64
                    _write(path, value)
                else:
                    parent = fixture.initial_foreign_attempt / "extracted_cache/c3_batch_000/dicom_extraction"
                    path = parent / "dicom_extraction.summary.json"
                    value = json.loads(path.read_bytes())
                    if mutation == "extra_field":
                        value["unexplained_content"] = "not producer output"
                    else:
                        del value["n_extracted_clips"]
                    digest = _write(path, value)
                    stage_path = parent / "stage_completion_receipt.restricted.json"
                    stage = json.loads(stage_path.read_bytes())
                    stage["artifacts"][path.name] = digest
                    _write(stage_path, stage)
                _fails("R7H_METADATA_RECEIPT_MISMATCH", fixture.classify)


def _fails(code: str, action: Any) -> None:
    try:
        action()
    except sequential.FullSequentialError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def test_known_metadata_cannot_hide_payloads_or_unexplained_children() -> None:
    for relative, code in (
        ("dicom_extraction/.hidden.npz", "R7H_UNEXPECTED_PAYLOAD"),
        ("dicom_extraction/unknown.json", "R7H_METADATA_ROLE_UNKNOWN"),
        ("dicom_extraction/nested/payload.npz", "R7H_METADATA_ROLE_UNKNOWN"),
        ("dicom_extraction/transition_receipts/unexpected.json", "R7H_METADATA_ROLE_UNKNOWN"),
        ("dicom_extraction/clips/payload.npz", "R7H_FINALIZED_EXTRACTION_CACHE_PRESENT"),
        ("payload.npz", "R7H_UNEXPECTED_PAYLOAD"),
        ("dicom_extraction.partial/failure.summary.json", "R7H_UNKNOWN_EXTRACTION_CACHE_PRESENT"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            with synthetic_retained_metadata_topology(Path(directory)) as fixture:
                batch = fixture.current_attempt / "extracted_cache/c3_batch_000"
                _write(batch / relative, b"never open a scientific payload")
                _fails(code, fixture.classify)


def test_metadata_receipt_and_manifest_authorities_remain_strict() -> None:
    for mutation in ("manifest", "stage_runtime", "summary_extra", "final_not_retired",
                     "final_prefix_hash", "transition", "missing_transition", "claim", "claim_reference"):
        with tempfile.TemporaryDirectory() as directory:
            with synthetic_retained_metadata_topology(Path(directory)) as fixture:
                parent = fixture.current_attempt / "extracted_cache/c3_batch_000/dicom_extraction"
                code = "R7H_METADATA_RECEIPT_MISMATCH"
                if mutation == "manifest":
                    _write(parent / "dicom_audit.restricted.csv", b"changed metadata\n")
                elif mutation == "missing_transition":
                    (parent / "transition_receipts/extraction_complete.restricted.json").unlink()
                    code = "R7H_METADATA_ROLE_UNKNOWN"
                elif mutation == "claim_reference":
                    reference = next(iter(sequential.R8U_R7H_RETAINED_PUBLICATION_CLAIMS.values()))[3]
                    _write(fixture.current_attempt / reference, {"publication_claim_sha256": "f" * 64})
                else:
                    if mutation == "stage_runtime":
                        path = parent / "stage_completion_receipt.restricted.json"
                    elif mutation == "summary_extra":
                        path = fixture.foreign_attempt / "extracted_cache/c3_batch_000/dicom_extraction/dicom_extraction.summary.json"
                    elif mutation.startswith("final_"):
                        path = fixture.current_attempt / "batches/c3_batch_000/preservation/batch_finalization_receipt.restricted.json"
                    elif mutation == "claim":
                        path = fixture.current_attempt / "extracted_cache/c3_batch_015/.r8u_r5_publication_claim/claim.restricted.json"
                    else:
                        path = parent / "transition_receipts/extraction_complete.restricted.json"
                    value = json.loads(path.read_bytes())
                    if mutation == "stage_runtime":
                        value["runtime_authority"]["batch_plan_sha256"] = "f" * 64
                    elif mutation == "summary_extra":
                        value["unexplained_content"] = "not producer output"
                    elif mutation == "final_not_retired":
                        value["extracted_cache_retired"] = False
                    elif mutation == "final_prefix_hash":
                        value["scheduler_job_identity"] = "999999"
                    elif mutation == "claim":
                        value["target_absent"] = False
                    else:
                        value["input_receipt_sha256"] = ["f" * 64]
                    _write(path, value)
                _fails(code, fixture.classify)


def test_metadata_file_directory_and_foreign_attempt_security_remain_strict() -> None:
    for mutation in ("symlink", "hardlink", "file_mode", "directory_mode", "foreign_attempt_mode", "owner"):
        with tempfile.TemporaryDirectory() as directory:
            with synthetic_retained_metadata_topology(Path(directory)) as fixture:
                parent = fixture.current_attempt / "extracted_cache/c3_batch_000/dicom_extraction"
                target = parent / "dicom_audit.restricted.csv"
                code = "R7H_EXTRACTION_CACHE_OWNER_OR_MODE_INVALID"
                with ExitStack() as stack:
                    if mutation == "symlink":
                        source = fixture.root / "synthetic-metadata.csv"
                        target.rename(source)
                        target.symlink_to(source)
                        code = "R7H_EXTRACTION_CACHE_SYMLINK_OR_NONREGULAR"
                    elif mutation == "hardlink":
                        os.link(target, fixture.root / "synthetic-metadata.csv")
                        code = "R7H_EXTRACTION_CACHE_SYMLINK_OR_NONREGULAR"
                    elif mutation == "file_mode":
                        target.chmod(0o644)
                    elif mutation == "directory_mode":
                        parent.chmod(0o755)
                    elif mutation == "foreign_attempt_mode":
                        fixture.foreign_attempt.chmod(0o755)
                    else:
                        stack.enter_context(mock.patch.object(sequential.os, "geteuid", return_value=os.geteuid() + 1))
                    _fails(code, fixture.classify)


def test_failure_transition_is_metadata_and_never_final_scientific_success() -> None:
    with tempfile.TemporaryDirectory() as directory:
        with synthetic_retained_metadata_topology(Path(directory)) as fixture:
            attempt = fixture.foreign_attempt
            batch = attempt / "extracted_cache/c3_batch_003"
            value = {
                "schema_version": 2, "receipt_type": "lvef_c3_state_transition_v2",
                "attempt_id": attempt.name, "batch_id": batch.name,
                "from_state": "DOWNLOAD_VERIFIED", "to_state": "FAILED_NONRETRYABLE",
                "status": "PASS", "authority": _runtime(attempt),
                "input_receipt_sha256": [SHA],
                "output_manifest_sha256": hashlib.sha256(b"DICOM_EXTRACTION:SYNTHETIC_FAILURE").hexdigest(),
            }
            path = batch / "dicom_extraction_failure_receipt/stage_failure.restricted.json"
            _write(path, value)
            topology = fixture.classify()
            assert topology.retained_metadata_roots == 20
            assert topology.active_scientific_caches == 0
            assert topology.sealed_cross_attempt_terminal_failed_caches == 2
            try:
                sequential.finalizer._validate_receipt(value)
            except sequential.finalizer.ProductionFinalizationError:
                pass
            else:
                raise AssertionError("failure transition satisfied scientific success")
            for key, changed in (
                ("to_state", "FINALIZED"),
                ("output_manifest_sha256", "f" * 64),
                ("authority", {**value["authority"], "batch_plan_sha256": "f" * 64}),
            ):
                _write(path, {**value, key: changed})
                _fails("R7H_METADATA_RECEIPT_MISMATCH", fixture.classify)
            _write(path, value)


def test_topology_reads_only_bound_control_and_manifest_roles() -> None:
    with tempfile.TemporaryDirectory() as directory:
        with synthetic_retained_metadata_topology(Path(directory)) as fixture:
            reader = sequential._read_owner_private_regular
            observed = []
            def read(path: Path, **kwargs: Any) -> bytes:
                assert path.suffix not in {".npz", ".dcm", ".npy"}
                observed.append(path.name)
                return reader(path, **kwargs)
            with mock.patch.object(sequential, "_read_owner_private_regular", side_effect=read):
                topology = fixture.classify()
            assert observed
            assert topology.npz_body_reads == 0
            assert fixture.expected_partial.joinpath("clips/synthetic.npz").read_bytes() == b"x"


def _run() -> int:
    passed = failed = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS {name}")
    print(f"SUMMARY passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
