from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: no network or SCC access.
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_lvef_c3_phase1ef_safe_outputs as gate


def _expect(code: str, function) -> None:
    try:
        function()
    except gate.Phase1EFSafeOutputError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected {code}")


def test_role_set_and_filenames_are_exact() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        paths = []
        for role, (filename, _) in gate.ROLE_SPECS.items():
            path = root / filename
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
            paths.append(f"{role}={path}")
        assert set(gate._parse_artifacts(paths)) == set(gate.ROLE_SPECS)
        _expect(
            "SAFE_OUTPUT_ARTIFACT_SET_NOT_EXACT",
            lambda: gate._parse_artifacts(paths[:-1]),
        )


def test_live_bytes_are_profile_validated_and_receipt_is_private() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        root.chmod(0o700)
        artifacts = []
        # Exercise wiring and receipt creation while mocking profile semantics;
        # every real profile is separately exercised by its producer tests.
        for role, (filename, _) in gate.ROLE_SPECS.items():
            path = root / filename
            path.write_text(json.dumps({"synthetic": role}) + "\n", encoding="utf-8")
            path.chmod(0o600)
            artifacts.append(f"{role}={path}")
        receipt = root / "safe_gate.restricted.json"
        original = gate.modes.validate_candidate_bytes
        gate.modes.validate_candidate_bytes = lambda *args, **kwargs: {"status": "PASS"}
        try:
            value = gate.execute(
                attempt_id="lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
                governing_commit="a" * 40,
                policy_path=ROOT / "configs/lvef_multitask_safe_export_policy.yaml",
                artifacts_raw=artifacts,
                receipt_path=receipt,
            )
        finally:
            gate.modes.validate_candidate_bytes = original
        assert value["validated_artifact_count"] == 5
        assert value["restricted_outputs_exported"] is False
        assert receipt.stat().st_mode & 0o777 == 0o600
        _expect(
            "SAFE_OUTPUT_RECEIPT_COLLISION",
            lambda: gate.execute(
                attempt_id="lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
                governing_commit="a" * 40,
                policy_path=ROOT / "configs/lvef_multitask_safe_export_policy.yaml",
                artifacts_raw=artifacts,
                receipt_path=receipt,
            ),
        )
