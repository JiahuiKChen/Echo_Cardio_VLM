import hashlib
import json
from pathlib import Path
import sys
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lvef_multitask_analysis_modes import (
    AUTHORIZATION_STATUS,
    DIRECT_MODE,
    EXPORT_MODE,
    SafetyPolicyError,
    bind_path_to_roots,
    create_direct_restricted_receipt,
    load_policy,
    repository_root,
    validate_direct_restricted_receipt,
)


def _root_spec(root: Path) -> list[dict[str, str]]:
    return [{"id": "synthetic_restricted", "path": str(root)}]


def _direct_policy(root: Path) -> dict:
    return {
        "schema_version": 1,
        "policy_id": "synthetic_policy_v1",
        "authorization": {"status": AUTHORIZATION_STATUS},
        "modes": {
            DIRECT_MODE: {
                "enabled": True,
                "approved_roots": _root_spec(root),
                "receipt_roots": _root_spec(root),
            },
            EXPORT_MODE: {
                "enabled": True,
                "restricted_staging_roots": _root_spec(root),
                "repository_release_roots": ["approved_exports"],
                "release_receipt_suffix": ".release_receipt.json",
                "human_approval_required": True,
            },
        },
        "forbidden_content": {
            "column_or_key_names": ["subject_id"],
            "value_patterns": [r"(?i)/restricted/"],
            "git_blocked_suffixes": [".dcm"],
        },
        "export_profiles": {
            "summary": {
                "kind": "json",
                "extensions": [".json"],
                "max_bytes": 1024,
                "required_top_level_keys": ["status", "count"],
                "allowed_top_level_keys": ["status", "count"],
            }
        },
    }


def test_default_policy_declares_two_distinct_modes() -> None:
    policy, policy_sha = load_policy(repository_root() / "configs" / "lvef_multitask_safe_export_policy.yaml")
    assert set(policy["modes"]) == {DIRECT_MODE, EXPORT_MODE}
    assert policy["authorization"]["status"] == AUTHORIZATION_STATUS
    assert len(policy_sha) == 64
    assert policy["modes"][DIRECT_MODE]["confirmatory_performance_authority_granted"] is False


def test_direct_receipt_stays_restricted_and_does_not_grant_export() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "restricted"
        root.mkdir()
        source = root / "source" / "rows.csv"
        source.parent.mkdir()
        source.write_text("subject_id,value\nSYNTHETIC,1\n")
        receipt = root / "receipts" / "direct.json"
        policy = _direct_policy(root)
        policy_sha = hashlib.sha256(b"synthetic-policy").hexdigest()
        result = create_direct_restricted_receipt(
            policy=policy,
            policy_sha256=policy_sha,
            receipt_path=receipt,
            purpose="synthetic restricted metadata review",
            source_commit="0" * 40,
            organization_class="synthetic approved organization",
            input_paths=[source],
            output_paths=[root / "analysis"],
            hash_inputs=True,
        )
        saved = json.loads(receipt.read_text())
        assert result["mode"] == DIRECT_MODE
        assert saved["authorization_status"] == AUTHORIZATION_STATUS
        assert saved["export_authority_granted"] is False
        assert saved["confirmatory_performance_authority_granted"] is False
        assert saved["detailed_outputs_remain_restricted"] is True
        assert saved["path_records"][0]["relative_path"] == "source/rows.csv"
        assert "sha256" in saved["path_records"][0]
        validated = validate_direct_restricted_receipt(
            policy=policy,
            policy_sha256=policy_sha,
            receipt_path=receipt,
            source_commit="0" * 40,
            purpose="synthetic restricted metadata review",
        )
        assert validated["policy_sha256"] == policy_sha


def test_direct_receipt_validation_rejects_duplicate_json_keys() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "restricted"
        root.mkdir()
        source = root / "source.csv"
        source.write_text("subject_id,value\nSYNTHETIC,1\n")
        receipt = root / "direct.json"
        policy = _direct_policy(root)
        policy_sha = hashlib.sha256(b"synthetic-policy").hexdigest()
        create_direct_restricted_receipt(
            policy=policy,
            policy_sha256=policy_sha,
            receipt_path=receipt,
            purpose="synthetic duplicate-key rejection",
            source_commit="0" * 40,
            organization_class="synthetic approved organization",
            input_paths=[source],
            hash_inputs=True,
        )
        marker = f'"mode": "{DIRECT_MODE}"'
        text = receipt.read_text()
        assert marker in text
        receipt.write_text(
            text.replace(
                marker,
                f'"mode": "UNSAFE_DUPLICATE",\n  {marker}',
                1,
            )
        )
        try:
            validate_direct_restricted_receipt(
                policy=policy,
                policy_sha256=policy_sha,
                receipt_path=receipt,
                source_commit="0" * 40,
                purpose="synthetic duplicate-key rejection",
            )
        except SafetyPolicyError:
            return
        raise AssertionError("Duplicate keys in a controlled receipt were accepted")


def test_restricted_binding_rejects_outside_path_and_symlink_escape() -> None:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        restricted = base / "restricted"
        outside = base / "outside"
        restricted.mkdir()
        outside.mkdir()
        secret = outside / "secret.csv"
        secret.write_text("subject_id\nSYNTHETIC\n")
        roots = _root_spec(restricted)
        try:
            bind_path_to_roots(secret, roots, must_exist=True, expect="file")
        except SafetyPolicyError:
            pass
        else:
            raise AssertionError("Outside path was accepted as restricted")

        link = restricted / "escape.csv"
        link.symlink_to(secret)
        try:
            bind_path_to_roots(link, roots, must_exist=True, expect="file")
        except SafetyPolicyError:
            pass
        else:
            raise AssertionError("Symlink escape was accepted")


def test_restricted_binding_rejects_parent_traversal_even_when_target_is_inside() -> None:
    with tempfile.TemporaryDirectory() as directory:
        restricted = Path(directory) / "restricted"
        nested = restricted / "nested"
        nested.mkdir(parents=True)
        target = restricted / "target.txt"
        target.write_text("synthetic")
        traversal = nested / ".." / "target.txt"
        try:
            bind_path_to_roots(traversal, _root_spec(restricted), must_exist=True, expect="file")
        except SafetyPolicyError:
            return
        raise AssertionError("Parent traversal was accepted")


def test_direct_receipt_cannot_be_written_inside_git_repository() -> None:
    repo = repository_root()
    policy = _direct_policy(repo)
    receipt = repo / "docs" / "lvef_multitask" / "SYNTHETIC_RESTRICTED_RECEIPT.json"
    assert not receipt.exists()
    try:
        create_direct_restricted_receipt(
            policy=policy,
            policy_sha256="0" * 64,
            receipt_path=receipt,
            purpose="synthetic rejection test",
            source_commit="0" * 40,
            organization_class="synthetic approved organization",
        )
    except SafetyPolicyError:
        assert not receipt.exists()
        return
    raise AssertionError("Restricted receipt was written inside Git")
