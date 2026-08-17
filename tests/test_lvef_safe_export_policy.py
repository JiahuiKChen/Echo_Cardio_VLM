import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

# SYNTHETIC_TEST_DATA_ONLY: all identifier-shaped values in this file are fixtures.


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_lvef_git_export_safety import (
    _assert_closed_control_hash_refresh,
    scan_staged_git_safety,
)
from lvef_multitask_analysis_modes import (
    AUTHORIZATION_STATUS,
    DIRECT_MODE,
    EXPORT_MODE,
    SafetyPolicyError,
    prepare_export_request,
    release_approved_export,
    validate_candidate_bytes,
    validate_policy,
)


def _policy(restricted: Path) -> dict:
    roots = [{"id": "synthetic_restricted", "path": str(restricted)}]
    return {
        "schema_version": 1,
        "policy_id": "synthetic_safe_export_v1",
        "authorization": {"status": AUTHORIZATION_STATUS},
        "modes": {
            DIRECT_MODE: {
                "enabled": True,
                "approved_roots": roots,
                "receipt_roots": roots,
            },
            EXPORT_MODE: {
                "enabled": True,
                "restricted_staging_roots": roots,
                "repository_release_roots": ["approved_exports"],
                "release_receipt_suffix": ".release_receipt.json",
                "human_approval_required": True,
            },
        },
        "forbidden_content": {
            "column_or_key_names": ["subject_id", "study_id", "path", "embedding", "y_pred"],
            "value_patterns": [r"(?i)/restricted/", r"(?i)gs://", r"(?i)\.dcm(?:$|\s)"],
            "git_blocked_suffixes": [".dcm", ".npz", ".npy", ".parquet", ".pt"],
        },
        "export_profiles": {
            "summary_json": {
                "kind": "json",
                "extensions": [".json"],
                "max_bytes": 4096,
                "required_top_level_keys": ["status", "count"],
                "allowed_top_level_keys": [
                    "status",
                    "count",
                    "safety_gate_passed",
                    "details",
                ],
                "field_types": {"status": "string", "count": "integer"},
            },
            "batch_csv": {
                "kind": "csv",
                "extensions": [".csv"],
                "max_bytes": 4096,
                "max_rows": 10,
                "required_columns": ["batch", "object_count"],
                "allowed_columns": ["batch", "object_count", "source_bytes"],
            },
        },
    }


def _approve(template_path: Path) -> None:
    approval = json.loads(template_path.read_text())
    approval.update(
        {
            "decision": "APPROVED",
            "approved_by": "SYNTHETIC_REVIEWER",
            "approver_role": "data steward",
            "approved_at_utc": "2026-08-05T12:00:00Z",
            "rationale": "Synthetic aggregate passed independent review.",
        }
    )
    template_path.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_policy_and_schema_validation_are_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        restricted = Path(directory) / "restricted"
        restricted.mkdir()
        policy = _policy(restricted)
        validate_policy(policy)
        valid = b'{"status":"PASS","count":3}\n'
        result = validate_candidate_bytes(
            valid,
            filename="summary.json",
            profile_name="summary_json",
            policy=policy,
        )
        assert result["status"] == "PASS"

        for invalid in (
            b'{"status":"PASS","count":3,"subject_id":"SYN"}\n',
            b'{"status":"PASS","count":3,"extra":0}\n',
            b'{"status":"PASS","count":"3"}\n',
            b'{"status":"PASS","count":NaN}\n',
            (
                b'{"status":"/restricted/project/mimicecho/private",'
                b'"status":"PASS","count":3}\n'
            ),
            (
                b'{"status":"PASS","count":3,'
                b'"details":{" subject_id":"10000001"}}\n'
            ),
        ):
            try:
                validate_candidate_bytes(
                    invalid,
                    filename="summary.json",
                    profile_name="summary_json",
                    policy=policy,
                )
            except (SafetyPolicyError, ValueError):
                continue
            raise AssertionError(f"Unsafe candidate was accepted: {invalid!r}")


def test_contextual_forbidden_key_exception_cannot_be_broadened() -> None:
    with tempfile.TemporaryDirectory() as directory:
        restricted = Path(directory) / "restricted"
        restricted.mkdir()
        policy = _policy(restricted)
        policy["forbidden_content"]["column_or_key_names"].append("label")
        policy["export_profiles"]["summary_json"][
            "contextual_forbidden_key_exceptions"
        ] = [
            {
                "path": "details.*.label",
                "allowed_string_values": ["synthetic_safe_value"],
            }
        ]
        try:
            validate_policy(policy)
        except SafetyPolicyError:
            pass
        else:
            raise AssertionError("A non-storage contextual exception was accepted")


def test_two_step_export_requires_hash_bound_approval_and_revalidates_bytes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        policy = _policy(restricted)
        validate_policy(policy)
        policy_sha = hashlib.sha256(b"synthetic-policy").hexdigest()
        candidate = restricted / "staging" / "summary.json"
        candidate.parent.mkdir()
        candidate.write_text('{"status":"PASS","count":3}\n')
        request = restricted / "review" / "request.json"
        approval = restricted / "review" / "approval.json"
        prepare_export_request(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=candidate,
            profile_name="summary_json",
            request_path=request,
            approval_template_path=approval,
            purpose="synthetic aggregate release",
        )

        try:
            release_approved_export(
                policy=policy,
                policy_sha256=policy_sha,
                candidate=candidate,
                request_path=request,
                approval_path=approval,
                destination=Path("approved_exports/summary.json"),
                repo_root=repo,
            )
        except SafetyPolicyError:
            pass
        else:
            raise AssertionError("Pending approval released an export")

        _approve(approval)
        receipt, destination, receipt_path = release_approved_export(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=candidate,
            request_path=request,
            approval_path=approval,
            destination=Path("approved_exports/summary.json"),
            repo_root=repo,
        )
        assert destination.read_bytes() == candidate.read_bytes()
        assert receipt_path.is_file()
        assert receipt["restricted_identifiers_exported"] is False
        assert "approved_by" not in receipt
        assert receipt["candidate_sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()


def test_changed_candidate_is_blocked_after_approval() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        policy = _policy(restricted)
        policy_sha = hashlib.sha256(b"synthetic-policy").hexdigest()
        candidate = restricted / "summary.json"
        candidate.write_text('{"status":"PASS","count":3}\n')
        request = restricted / "request.json"
        approval = restricted / "approval.json"
        prepare_export_request(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=candidate,
            profile_name="summary_json",
            request_path=request,
            approval_template_path=approval,
            purpose="synthetic aggregate release",
        )
        _approve(approval)
        candidate.write_text('{"status":"PASS","count":4}\n')
        try:
            release_approved_export(
                policy=policy,
                policy_sha256=policy_sha,
                candidate=candidate,
                request_path=request,
                approval_path=approval,
                destination=Path("approved_exports/summary.json"),
                repo_root=repo,
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Candidate mutation after approval was accepted")


def test_git_gate_accepts_reviewed_pair_and_blocks_restricted_artifacts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        policy_sha = hashlib.sha256(b"synthetic-policy").hexdigest()
        candidate = restricted / "summary.json"
        candidate.write_text('{"status":"PASS","count":3}\n')
        request = restricted / "request.json"
        approval = restricted / "approval.json"
        prepare_export_request(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=candidate,
            profile_name="summary_json",
            request_path=request,
            approval_template_path=approval,
            purpose="synthetic aggregate release",
        )
        _approve(approval)
        _, destination, receipt_path = release_approved_export(
            policy=policy,
            policy_sha256=policy_sha,
            candidate=candidate,
            request_path=request,
            approval_path=approval,
            destination=Path("approved_exports/summary.json"),
            repo_root=repo,
        )
        resolved_repo = repo.resolve()
        _git(
            repo,
            "add",
            str(destination.relative_to(resolved_repo)),
            str(receipt_path.relative_to(resolved_repo)),
        )
        result = scan_staged_git_safety(repo=repo, policy=policy, policy_sha256=policy_sha)
        assert result["status"] == "PASS"
        assert result["approved_export_artifacts_checked"] == 1

        restricted_binary = repo / "patient_embeddings.npz"
        restricted_binary.write_bytes(b"SYNTHETIC\x00RESTRICTED")
        _git(repo, "add", restricted_binary.name)
        try:
            scan_staged_git_safety(repo=repo, policy=policy, policy_sha256=policy_sha)
        except SafetyPolicyError:
            return
        raise AssertionError("Restricted binary artifact passed the Git safety gate")


def test_git_gate_accepts_only_closed_control_entrypoint_hash_refresh() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = "configs/lvef_c3_canary_scheduler_plan_v1.json"
    payload = (root / relative).read_bytes()
    assert _assert_closed_control_hash_refresh(
        repo=root, relative_path=relative, payload=payload
    )

    changed = json.loads(payload)
    changed["status"] = "UNAUTHORIZED_CONTROL_CHANGE"
    try:
        _assert_closed_control_hash_refresh(
            repo=root,
            relative_path=relative,
            payload=(json.dumps(changed, sort_keys=True) + "\n").encode("utf-8"),
        )
    except SafetyPolicyError:
        pass
    else:
        raise AssertionError("Non-hash control JSON mutation passed the Git gate")

    for mutation in ("missing_reviewed_output", "extra_output", "other_stage"):
        changed = json.loads(payload)
        if mutation == "missing_reviewed_output":
            changed["stages"][1]["outputs"].remove(
                "technical_disposition_manifest"
            )
        elif mutation == "extra_output":
            changed["stages"][1]["outputs"].append("unreviewed_output")
        else:
            changed["stages"][0]["outputs"].append("unreviewed_output")
        try:
            _assert_closed_control_hash_refresh(
                repo=root,
                relative_path=relative,
                payload=(
                    json.dumps(changed, sort_keys=True) + "\n"
                ).encode("utf-8"),
            )
        except SafetyPolicyError:
            pass
        else:
            raise AssertionError(
                f"Unreviewed control output mutation passed: {mutation}"
            )

    assert not _assert_closed_control_hash_refresh(
        repo=root,
        relative_path="configs/unreviewed_control.json",
        payload=b"{}\n",
    )


def test_git_gate_accepts_only_reviewed_live_dependency_policy_addition() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = "configs/lvef_c3_canary_live_dependencies_v1.json"
    payload = (root / relative).read_bytes()
    current = json.loads(payload)
    dependency = next(
        item
        for item in current["dependencies"]
        if item["dependency_id"] == "production_contract_and_callables"
    )
    policy_path = (
        "configs/lvef_c3_source_signal_object_technical_disposition_v1.json"
    )
    assert dependency["artifact_paths"].count(policy_path) == 1

    prior = json.loads(payload)
    prior_dependency = next(
        item
        for item in prior["dependencies"]
        if item["dependency_id"] == "production_contract_and_callables"
    )
    prior_dependency["artifact_paths"].remove(policy_path)

    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory) / "repo"
        target = repo / relative
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(prior, sort_keys=True) + "\n", encoding="utf-8")
        _git(repo, "init", "-q")
        _git(repo, "add", relative)
        _git(
            repo,
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=synthetic@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-q",
            "-m",
            "synthetic pre-transition authority",
        )
        assert _assert_closed_control_hash_refresh(
            repo=repo, relative_path=relative, payload=payload
        )

        for mutation in (
            "missing_reviewed_addition",
            "reordered_reviewed_addition",
            "extra_artifact",
            "unrelated_dependency_change",
        ):
            changed = json.loads(payload)
            changed_dependency = next(
                item
                for item in changed["dependencies"]
                if item["dependency_id"] == "production_contract_and_callables"
            )
            if mutation == "missing_reviewed_addition":
                changed_dependency["artifact_paths"].remove(policy_path)
            elif mutation == "reordered_reviewed_addition":
                changed_dependency["artifact_paths"].remove(policy_path)
                changed_dependency["artifact_paths"].append(policy_path)
            elif mutation == "extra_artifact":
                changed_dependency["artifact_paths"].append(
                    "configs/unreviewed_policy.json"
                )
            else:
                changed["dependencies"][0]["artifact_paths"].append(
                    "configs/unreviewed_control.json"
                )
            try:
                _assert_closed_control_hash_refresh(
                    repo=repo,
                    relative_path=relative,
                    payload=(json.dumps(changed, sort_keys=True) + "\n").encode(
                        "utf-8"
                    ),
                )
            except SafetyPolicyError:
                pass
            else:
                raise AssertionError(
                    f"Unreviewed live-dependency mutation passed: {mutation}"
                )


def test_git_gate_blocks_unreviewed_file_in_release_root() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        unreviewed = repo / "approved_exports" / "summary.json"
        unreviewed.parent.mkdir()
        unreviewed.write_text('{"status":"PASS","count":3}\n')
        _git(repo, "add", str(unreviewed.relative_to(repo)))
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Unreviewed export-root artifact passed the Git safety gate")


def test_git_gate_blocks_patient_locator_in_plain_text() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        leaked = repo / "leaked.txt"
        leaked.write_text(
            "files/" + "p10/" + "p" + "10000001/" + "s" + "20000001/clip.dcm\n"
        )
        _git(repo, "add", leaked.name)
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Patient/study DICOM locator passed the plain-text Git gate")


def test_git_gate_blocks_identifier_rows_in_markdown() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        leaked = repo / "leaked.md"
        leaked.write_text("subject_id,study_id\n10000001,20000001\n")
        _git(repo, "add", leaked.name)
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Identifier-shaped Markdown table passed the Git gate")


def test_git_gate_blocks_whitespace_prefixed_single_identifier_column() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        leaked = repo / "leaked.csv"
        leaked.write_text(" subject_id,value\n10000001,55\n")
        _git(repo, "add", leaked.name)
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Whitespace-prefixed identifier column passed the Git gate")


def test_git_gate_blocks_whitespace_prefixed_json_identifier_key() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        leaked = repo / "leaked.json"
        leaked.write_text('{" subject_id":"10000001"}\n')
        _git(repo, "add", leaked.name)
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Whitespace-prefixed restricted JSON key passed the Git gate")


def test_git_gate_blocks_duplicate_and_nested_normalization_json_bypasses() -> None:
    cases = (
        (
            "duplicate.json",
            (
                '{"status":"/restricted/project/mimicecho/private",'
                '"status":"PASS"}\n'
            ),
        ),
        (
            "nested.json",
            '{"status":"PASS","details":{" subject_id":"10000001"}}\n',
        ),
    )
    for filename, payload in cases:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            restricted = base / "restricted"
            repo = base / "repo"
            restricted.mkdir()
            repo.mkdir()
            _git(repo, "init", "-q")
            policy = _policy(restricted)
            leaked = repo / filename
            leaked.write_text(payload)
            _git(repo, "add", leaked.name)
            try:
                scan_staged_git_safety(
                    repo=repo,
                    policy=policy,
                    policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
                )
            except SafetyPolicyError:
                continue
            raise AssertionError(f"Unsafe JSON bypass passed the Git gate: {filename}")


def test_git_gate_blocks_jsonl_even_when_the_policy_fixture_omits_the_suffix() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        leaked = repo / "leaked.jsonl"
        leaked.write_text('{"subject_id":"10000001","study_id":"20000001"}\n')
        _git(repo, "add", leaked.name)
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("JSONL restricted-row artifact passed the Git gate")


def test_git_gate_blocks_generated_restricted_clinician_packet_filename() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        repo = base / "repo"
        restricted.mkdir()
        repo.mkdir()
        _git(repo, "init", "-q")
        policy = _policy(restricted)
        leaked = repo / "clinical_metadata_clinician_signoff_restricted.md"
        leaked.write_text("Exact raw description and unit intended to stay on SCC.\n")
        _git(repo, "add", leaked.name)
        try:
            scan_staged_git_safety(
                repo=repo,
                policy=policy,
                policy_sha256=hashlib.sha256(b"synthetic-policy").hexdigest(),
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Restricted clinician packet filename passed the Git gate")
