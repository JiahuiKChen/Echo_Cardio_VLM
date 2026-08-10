from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: every hash and path created here is a test fixture.

import ast
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock
import urllib.request

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lock_lvef_c3_production_orchestration as production_lock
import validate_lvef_c3_execution_contract as contract_validator


CONTRACT = ROOT / "configs" / "lvef_c3_execution_contract.yaml"
LOCK_SCRIPT = ROOT / "scripts" / "lock_lvef_c3_production_orchestration.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _distributed(total: int, groups: int) -> list[int]:
    base, remainder = divmod(total, groups)
    return [base + (1 if index < remainder else 0) for index in range(groups)]


def _write_validator_compatible_contract(root: Path) -> Path:
    """Isolate these tests from concurrently staged gate-set changes."""
    payload = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    staged = payload["preauthorization_gates"]
    payload["preauthorization_gates"] = {
        key: staged.get(key, False)
        for key in sorted(contract_validator.REQUIRED_PREAUTHORIZATION_GATES)
    }
    path = root / "execution_contract.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _write_synthetic_aggregate_authorities(
    root: Path,
) -> tuple[Path, Path, dict[str, tuple[int, str]], dict[str, tuple[int, str]]]:
    original_root = root / "original"
    supplemental_root = root / "supplemental"
    original_root.mkdir(parents=True)
    supplemental_root.mkdir()
    object_counts = _distributed(
        production_lock.EXPECTED_NORMALIZED_SOURCE_REQUESTS,
        production_lock.EXPECTED_BATCH_COUNT,
    )
    source_bytes = _distributed(
        production_lock.EXPECTED_SOURCE_BYTES,
        production_lock.EXPECTED_BATCH_COUNT,
    )
    batch = original_root / "c3_full_source_preflight_by_batch.csv"
    with batch.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(production_lock.EXPECTED_BATCH_HEADER),
        )
        writer.writeheader()
        for index, batch_id in enumerate(production_lock.EXPECTED_BATCH_IDS):
            studies = production_lock.EXPECTED_BATCH_STUDY_COUNTS[index]
            writer.writerow(
                {
                    "production_batch": batch_id,
                    "n_studies": studies,
                    "n_subjects": studies,
                    "n_requested_objects": object_counts[index],
                    "n_verified_objects": object_counts[index],
                    "n_unexpected_selected_objects": 0,
                    "total_source_bytes": source_bytes[index],
                    "status": "PASS",
                }
            )
    original_names = set(production_lock.ORIGINAL_AGGREGATE_AUTHORITIES)
    supplemental_names = set(production_lock.SUPPLEMENTAL_AGGREGATE_AUTHORITIES)
    for name in original_names - {batch.name}:
        (original_root / name).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PASS_SYNTHETIC_FIXTURE",
                    "aggregate_only": True,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    for name in supplemental_names - {production_lock.SUPPLEMENTAL_RECEIPT_FILENAME}:
        (supplemental_root / name).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PASS_SYNTHETIC_FIXTURE",
                    "aggregate_only": True,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    binding_hashes = {
        receipt_key: _sha256(supplemental_root / filename)
        for receipt_key, filename in (
            production_lock.SUPPLEMENTAL_RECEIPT_HASH_BINDINGS.items()
        )
    }
    combined_payload = {
        "schema_version": 1,
        "status": production_lock.SUPPLEMENTAL_RECEIPT_PASS_STATUS,
        "attempt_id": "synthetic_attempt_002",
        "governing_commit": "d" * 40,
        **{
            key: True for key in production_lock.SUPPLEMENTAL_RECEIPT_TRUE_GATES
        },
        **binding_hashes,
        "full_c3_authorized": False,
        "section5_run": False,
    }
    assert set(combined_payload) == production_lock.SUPPLEMENTAL_RECEIPT_KEYS
    (supplemental_root / production_lock.SUPPLEMENTAL_RECEIPT_FILENAME).write_text(
        json.dumps(combined_payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    original = {
        name: (
            (original_root / name).stat().st_size,
            _sha256(original_root / name),
        )
        for name in sorted(original_names)
    }
    supplemental = {
        name: (
            (supplemental_root / name).stat().st_size,
            _sha256(supplemental_root / name),
        )
        for name in sorted(supplemental_names)
    }
    return original_root, supplemental_root, original, supplemental


def _build_synthetic_lock(root: Path) -> tuple[dict, Path, Path]:
    aggregate_root = root / "aggregate"
    original_root, supplemental_root, original, supplemental = (
        _write_synthetic_aggregate_authorities(aggregate_root)
    )
    summary = production_lock.build_lock_summary(
        contract_path=_write_validator_compatible_contract(root),
        original_aggregate_root=original_root,
        supplemental_aggregate_root=supplemental_root,
        governing_commit="d" * 40,
        selected_source_manifest_sha256="a" * 64,
        environment_receipt_sha256="b" * 64,
        command_config_manifest_sha256="c" * 64,
        original_authorities=original,
        supplemental_authorities=supplemental,
    )
    return summary, original_root, supplemental_root


def test_pretransfer_lock_freezes_exact_19_batch_plan_and_remains_no_go() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, _, _ = _build_synthetic_lock(Path(directory))
    topology = summary["batch_topology"]
    rows = topology["scheduler_task_to_batch"]
    assert summary["status"] == "PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED"
    assert summary["execution_mode"] == "SPECIFICATION_ONLY"
    assert summary["execution_authorized"] is False
    assert summary["submission_authorized"] is False
    assert summary["source_body_download_authorized"] is False
    assert summary["scientific_execution_authorized"] is False
    assert summary["full_c3_status"] == "NO_GO"
    assert summary["authority_verification"][
        "all_authority_file_hashes_verified_from_actual_paths"
    ] is False
    assert summary["authority_verification"][
        "hash_verification_does_not_establish_semantic_authority"
    ] is True
    assert "AUTHORITY_FILES_PROVIDED_UNVERIFIED" in summary[
        "remaining_execution_blockers"
    ]
    assert (
        "PRODUCTION_AUTHORITY_SEMANTIC_AND_SOURCE_RECEIPT_VALIDATION_NOT_IMPLEMENTED"
        in summary["remaining_execution_blockers"]
    )
    assert summary["supplemental_validation_receipt"]["evidence_role"] == (
        "SUBORDINATE_HASH_BOUND_RECEIPT"
    )
    assert summary["supplemental_validation_receipt"][
        "closed_schema_and_dag_validator_reexecuted_by_this_lock"
    ] is False
    assert topology["total_batches"] == 19
    assert topology["full_batches"] == 18
    assert topology["studies_per_full_batch"] == 250
    assert topology["final_batch_studies"] == 30
    assert topology["maximum_concurrent_batches"] == 1
    assert [row["scheduler_task"] for row in rows] == list(range(1, 20))
    assert [row["production_batch"] for row in rows] == [
        f"c3_batch_{index:03d}" for index in range(19)
    ]
    assert [row["n_studies"] for row in rows] == [250] * 18 + [30]
    assert sum(int(row["n_studies"]) for row in rows) == 4_530
    assert sum(int(row["n_requested_objects"]) for row in rows) == 335_984
    assert sum(int(row["total_source_bytes"]) for row in rows) == 1_216_569_133_322


def test_pretransfer_lock_freezes_downloader_receipt_resume_and_retention_rules() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, _, _ = _build_synthetic_lock(Path(directory))
    downloader = summary["downloader_contract"]
    receipts = summary["stage_receipt_and_resume_contract"]
    storage = summary["storage_contract"]
    preservation = summary["preservation_and_finalization_contract"]
    assert downloader["implementation_present"] is False
    assert downloader["generation_pinned_exact_objects_required"] is True
    assert downloader["object_listing_permitted"] is False
    assert downloader["prefix_copy_permitted"] is False
    assert downloader["requester_pays_environment_variable"] == (
        "LVEF_C3_GCP_BILLING_PROJECT"
    )
    assert downloader["billing_project_argv_permitted"] is False
    assert downloader["billing_project_log_export_permitted"] is False
    assert downloader["atomic_no_clobber_finalization_required"] is True
    assert receipts["prior_attempt_overwrite_permitted"] is False
    assert receipts["canonical_output_overwrite_permitted"] is False
    assert receipts["failed_and_partial_evidence_preserved"] is True
    assert receipts["resume_requires_exact_input_authority_hashes"] is True
    assert receipts["resume_requires_pass_receipt"] is True
    assert receipts["resume_requires_output_size_and_sha256_match"] is True
    assert storage["raw_dicoms_retained_through_active_analysis"] is True
    assert storage["raw_dicom_deletion_permitted"] is False
    assert storage["maximum_active_extracted_cache_batches"] == 1
    assert storage["cache_retirement_authorized"] is False
    assert preservation["finalizer_implementation_present"] is False
    assert preservation["finalizer_requires_all_19_pass_receipts"] is True
    assert preservation["finalizer_no_clobber_required"] is True


def test_pretransfer_lock_fails_if_batch_partition_or_object_total_changes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        aggregate_root = root / "aggregate"
        original_root, supplemental_root, original, supplemental = (
            _write_synthetic_aggregate_authorities(aggregate_root)
        )
        contract_path = _write_validator_compatible_contract(root)
        batch = original_root / "c3_full_source_preflight_by_batch.csv"
        rows = list(csv.DictReader(batch.open(newline="", encoding="utf-8")))
        rows[-1]["n_studies"] = "29"
        rows[-1]["n_subjects"] = "29"
        with batch.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(production_lock.EXPECTED_BATCH_HEADER),
            )
            writer.writeheader()
            writer.writerows(rows)
        original[batch.name] = (batch.stat().st_size, _sha256(batch))
        try:
            production_lock.build_lock_summary(
                contract_path=contract_path,
                original_aggregate_root=original_root,
                supplemental_aggregate_root=supplemental_root,
                governing_commit="d" * 40,
                selected_source_manifest_sha256="a" * 64,
                environment_receipt_sha256="b" * 64,
                command_config_manifest_sha256="c" * 64,
                original_authorities=original,
                supplemental_authorities=supplemental,
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "BATCH_STUDY_PARTITION_CHANGED"
        else:
            raise AssertionError("Changed batch partition did not fail closed")


def test_pretransfer_lock_rejects_any_body_or_scientific_preauthorization() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        aggregate_root = root / "aggregate"
        original_root, supplemental_root, original, supplemental = (
            _write_synthetic_aggregate_authorities(aggregate_root)
        )
        contract_path = _write_validator_compatible_contract(root)
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        contract["source"]["source_body_download_authorized"] = True
        contract["authorization"]["source_body_download"] = True
        changed = root / "contract.yaml"
        changed.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
        try:
            production_lock.build_lock_summary(
                contract_path=changed,
                original_aggregate_root=original_root,
                supplemental_aggregate_root=supplemental_root,
                governing_commit="d" * 40,
                selected_source_manifest_sha256="a" * 64,
                environment_receipt_sha256="b" * 64,
                command_config_manifest_sha256="c" * 64,
                original_authorities=original,
                supplemental_authorities=supplemental,
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) in {
                "SOURCE_BODY_DOWNLOAD_PREAUTHORIZED",
                "SCIENTIFIC_OR_EXECUTION_ACTION_PREAUTHORIZED",
            }
        else:
            raise AssertionError("Body-transfer preauthorization did not fail closed")


def test_pretransfer_lock_requires_owner_planning_acceptance_and_no_transfer_authority() -> None:
    cases = (
        (
            "requester_pays_planning_estimate_owner_accepted",
            False,
            "REQUESTER_PAYS_PLANNING_ESTIMATE_NOT_OWNER_ACCEPTED",
        ),
        (
            "actual_dicom_transfer_authorized",
            True,
            "ACTUAL_DICOM_TRANSFER_PREAUTHORIZED",
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for index, (key, changed_value, expected_code) in enumerate(cases):
            case_root = root / f"case_{index}"
            aggregate_root = case_root / "aggregate"
            original_root, supplemental_root, original, supplemental = (
                _write_synthetic_aggregate_authorities(aggregate_root)
            )
            contract_path = _write_validator_compatible_contract(case_root)
            contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
            contract["preauthorization_gates"][key] = changed_value
            contract_path.write_text(
                yaml.safe_dump(contract, sort_keys=False), encoding="utf-8"
            )
            try:
                production_lock.build_lock_summary(
                    contract_path=contract_path,
                    original_aggregate_root=original_root,
                    supplemental_aggregate_root=supplemental_root,
                    governing_commit="d" * 40,
                    selected_source_manifest_sha256="a" * 64,
                    environment_receipt_sha256="b" * 64,
                    command_config_manifest_sha256="c" * 64,
                    original_authorities=original,
                    supplemental_authorities=supplemental,
                )
            except production_lock.PretransferLockError as exc:
                assert str(exc) == expected_code
            else:
                raise AssertionError(f"Contract mutation {key} was accepted")


def test_pretransfer_lock_is_pure_and_never_invokes_network_scheduler_or_system() -> None:
    with tempfile.TemporaryDirectory() as directory, mock.patch(
        "subprocess.run", side_effect=AssertionError("subprocess forbidden")
    ), mock.patch(
        "subprocess.Popen", side_effect=AssertionError("subprocess forbidden")
    ), mock.patch(
        "os.system", side_effect=AssertionError("system forbidden")
    ), mock.patch.object(
        urllib.request, "urlopen", side_effect=AssertionError("network forbidden")
    ):
        summary, _, _ = _build_synthetic_lock(Path(directory))
    assert summary["execution_authorized"] is False
    assert summary["immutable_aggregate_authorities"][
        "all_size_and_sha256_checks_passed"
    ] is True


def test_pretransfer_lock_source_has_no_execution_or_scientific_runtime_imports() -> None:
    tree = ast.parse(LOCK_SCRIPT.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert imported_roots.isdisjoint(
        {
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "google",
            "googleapiclient",
            "pydicom",
            "numpy",
            "torch",
            "torchvision",
            "sklearn",
        }
    )


def test_pretransfer_lock_output_is_mode_600_and_never_clobbered() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        summary, _, _ = _build_synthetic_lock(root)
        output = root / "lock.json"
        production_lock.write_json_no_clobber(
            output, summary, restricted_root=root
        )
        original = output.read_bytes()
        assert output.stat().st_mode & 0o777 == 0o600
        try:
            production_lock.write_json_no_clobber(
                output, summary, restricted_root=root
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "OUTPUT_ALREADY_EXISTS_NO_CLOBBER"
        else:
            raise AssertionError("Existing lock was not protected")
        assert output.read_bytes() == original


def test_pretransfer_lock_output_rejects_nonprivate_or_outside_root() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        summary, _, _ = _build_synthetic_lock(root)
        private_root = root / "private"
        private_root.mkdir(mode=0o700)
        nonprivate_root = root / "nonprivate"
        nonprivate_root.mkdir()
        nonprivate_root.chmod(0o755)
        try:
            production_lock.write_json_no_clobber(
                nonprivate_root / "lock.json",
                summary,
                restricted_root=nonprivate_root,
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "OUTPUT_PARENT_NOT_OWNER_PRIVATE"
        else:
            raise AssertionError("Nonprivate output root was accepted")
        assert not (nonprivate_root / "lock.json").exists()
        try:
            production_lock.write_json_no_clobber(
                root / "outside.json",
                summary,
                restricted_root=private_root,
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "OUTPUT_OUTSIDE_RESTRICTED_ROOT"
        else:
            raise AssertionError("Output outside restricted root was accepted")
        assert not (root / "outside.json").exists()


def test_pretransfer_lock_rejects_tampered_subordinate_receipt_binding() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        aggregate_root = root / "aggregate"
        original_root, supplemental_root, original, supplemental = (
            _write_synthetic_aggregate_authorities(aggregate_root)
        )
        contract_path = _write_validator_compatible_contract(root)
        predecessor = supplemental_root / (
            "c3_autoclass_adjudication.summary.json"
        )
        predecessor.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PASS_SYNTHETIC_TAMPER",
                    "aggregate_only": True,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        supplemental[predecessor.name] = (
            predecessor.stat().st_size,
            _sha256(predecessor),
        )
        try:
            production_lock.build_lock_summary(
                contract_path=contract_path,
                original_aggregate_root=original_root,
                supplemental_aggregate_root=supplemental_root,
                governing_commit="d" * 40,
                selected_source_manifest_sha256="a" * 64,
                environment_receipt_sha256="b" * 64,
                command_config_manifest_sha256="c" * 64,
                original_authorities=original,
                supplemental_authorities=supplemental,
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "SUPPLEMENTAL_RECEIPT_HASH_BINDING_MISMATCH"
        else:
            raise AssertionError("Tampered subordinate receipt binding was accepted")


def test_pretransfer_lock_requires_the_producer_defined_supplemental_pass_status() -> None:
    producer_source = (
        ROOT / "scripts" / "adjudicate_lvef_c3_autoclass.py"
    ).read_text(encoding="utf-8")
    assert production_lock.SUPPLEMENTAL_RECEIPT_PASS_STATUS == (
        "PASS_SUPPLEMENTAL_ADJUDICATION"
    )
    assert (
        f'"status": "{production_lock.SUPPLEMENTAL_RECEIPT_PASS_STATUS}"'
        in producer_source
    )

    for index, invalid_status in enumerate(
        (
            "PASS",
            "pass_supplemental_adjudication",
            "PASS_SUPPLEMENTAL_ADJUDICATION ",
            "FAIL_WITH_EXPLICIT_REASON",
            "",
            None,
            True,
        )
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / f"case_{index}"
            aggregate_root = root / "aggregate"
            original_root, supplemental_root, original, supplemental = (
                _write_synthetic_aggregate_authorities(aggregate_root)
            )
            receipt = supplemental_root / production_lock.SUPPLEMENTAL_RECEIPT_FILENAME
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["status"] = invalid_status
            receipt.write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
            )
            supplemental[receipt.name] = (receipt.stat().st_size, _sha256(receipt))
            try:
                production_lock.build_lock_summary(
                    contract_path=_write_validator_compatible_contract(root),
                    original_aggregate_root=original_root,
                    supplemental_aggregate_root=supplemental_root,
                    governing_commit="d" * 40,
                    selected_source_manifest_sha256="a" * 64,
                    environment_receipt_sha256="b" * 64,
                    command_config_manifest_sha256="c" * 64,
                    original_authorities=original,
                    supplemental_authorities=supplemental,
                )
            except production_lock.PretransferLockError as exc:
                assert str(exc) == "SUPPLEMENTAL_VALIDATION_RECEIPT_NOT_PASS"
            else:
                raise AssertionError(
                    f"Invalid supplemental status case {index} was accepted"
                )


def test_checkout_authority_is_read_from_git_metadata_without_subprocess() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        git_dir = root / ".git"
        ref = git_dir / "refs" / "heads" / "codex"
        ref.mkdir(parents=True)
        commit = "d" * 40
        (git_dir / "HEAD").write_text(
            "ref: refs/heads/codex/lvef-multitask-revalidation\n",
            encoding="utf-8",
        )
        (ref / "lvef-multitask-revalidation").write_text(
            commit + "\n", encoding="utf-8"
        )
        assert production_lock.verify_checkout_authority(root, commit) == commit
        try:
            production_lock.verify_checkout_authority(root, "e" * 40)
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "CHECKOUT_COMMIT_MISMATCH"
        else:
            raise AssertionError("Mismatched checkout commit was accepted")


def test_actual_authority_file_hash_mismatch_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "authority.json"
        artifact.write_text("{}\n", encoding="utf-8")
        try:
            production_lock._resolve_file_hash_authority(
                provided_sha256="a" * 64,
                artifact_path=artifact,
                invalid_hash_code="INVALID",
                missing_file_code="MISSING",
                mismatch_code="MISMATCH",
            )
        except production_lock.PretransferLockError as exc:
            assert str(exc) == "MISMATCH"
        else:
            raise AssertionError("Mismatched authority file hash was accepted")
        observed, status = production_lock._resolve_file_hash_authority(
            provided_sha256=_sha256(artifact),
            artifact_path=artifact,
            invalid_hash_code="INVALID",
            missing_file_code="MISSING",
            mismatch_code="MISMATCH",
        )
        assert observed == _sha256(artifact)
        assert status == "HASH_VERIFIED_FROM_ACTUAL_FILE"


def test_pretransfer_lock_aggregate_schema_rejects_identifier_or_path_injection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, _, _ = _build_synthetic_lock(Path(directory))
    unsafe_key = dict(summary)
    unsafe_key["cohort"] = {**summary["cohort"], "study_id": "synthetic"}
    try:
        production_lock.validate_aggregate_safe_summary(unsafe_key)
    except production_lock.PretransferLockError as exc:
        assert str(exc) == "LOCK_SUMMARY_RESTRICTED_KEY_PRESENT"
    else:
        raise AssertionError("Identifier-shaped key was not rejected")
    unsafe_value = dict(summary)
    unsafe_value["remaining_execution_blockers"] = [
        "/restricted/synthetic/private/path"
    ]
    try:
        production_lock.validate_aggregate_safe_summary(unsafe_value)
    except production_lock.PretransferLockError as exc:
        assert str(exc) == "LOCK_SUMMARY_RESTRICTED_VALUE_PRESENT"
    else:
        raise AssertionError("Restricted path-shaped value was not rejected")


def test_all_full_c3_execution_stubs_refuse_before_qsub_or_cloud_tools() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        invoked = root / "forbidden_executable_invoked"
        for name in ("qsub", "gcloud", "gsutil", "bq"):
            executable = fake_bin / name
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$0\" >> \"$SYNTHETIC_SENTINEL\"\nexit 99\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
        owner = root / "owner.json"
        owner.write_text("{}\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "SYNTHETIC_SENTINEL": str(invoked),
                "LVEF_C3_EXECUTION_ENV_FILE": str(root / "synthetic.env"),
                "SGE_TASK_ID": "1",
                "LVEF_C3_PYTHON": sys.executable,
            }
        )
        commands = (
            ["/bin/bash", str(ROOT / "scripts" / "scc_run_lvef_c3_full.sh")],
            ["/bin/bash", str(ROOT / "scripts" / "scc_run_lvef_c3_batch.sh")],
            ["/bin/bash", str(ROOT / "scripts" / "scc_finalize_lvef_c3_full.sh")],
            [
                "/bin/bash",
                str(ROOT / "scripts" / "scc_submit_lvef_c3_full.sh"),
                str(CONTRACT),
                str(owner),
                str(root / "synthetic.env"),
                "--submit",
            ],
        )
        for command in commands:
            result = subprocess.run(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            assert result.returncode == 78
        assert not invoked.exists()
