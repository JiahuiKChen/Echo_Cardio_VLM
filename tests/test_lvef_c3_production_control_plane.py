from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_multitask_analysis_modes as analysis_modes
import lvef_c3_orchestration_core as core
import prepare_lvef_c3_production_control_plane as prepare


def test_runtime_environment_is_sorted_literal_and_private_value_not_emitted() -> None:
    text = prepare._runtime_environment_text(
        {
            "LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project",
            "LVEF_C3_ATTEMPT_ID": "lvef_c3_phase1ee_synthetic_001",
        }
    )
    assert text.splitlines() == sorted(text.splitlines())
    try:
        prepare._runtime_environment_text({"INVALID": "$(touch /tmp/no)"})
    except prepare.ControlPlanePreparationError as exc:
        assert str(exc) == "RUNTIME_ENVIRONMENT_VALUE_NOT_LITERAL"
    else:
        raise AssertionError("shell syntax was accepted in the private environment")


def test_scc_setgid_only_private_directory_mode_is_accepted() -> None:
    assert core.owner_private_directory_mode_ok(0o700)
    assert core.owner_private_directory_mode_ok(0o2700)
    for mode in (0o770, 0o750, 0o2770, 0o2777):
        assert not core.owner_private_directory_mode_ok(mode)


def test_scheduler_private_directory_gate_accepts_only_700_or_2700() -> None:
    common = ROOT / "scripts/lvef_c3_production_scheduler_common.sh"
    source = common.read_text(encoding="utf-8")
    assert source.count('== "700"') == 1
    assert '== "700" || "$1" == "2700"' in source
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        candidate = root / "private"
        candidate.mkdir()
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_stat = fake_bin / "stat"
        fake_stat.write_text(
            "#!/bin/sh\n"
            "case \"$2\" in\n"
            "  %U) id -un ;;\n"
            "  %a) printf '%s\\n' \"$SYNTHETIC_MODE\" ;;\n"
            "  *) exit 64 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_stat.chmod(0o700)
        command = (
            f"source {shlex.quote(str(common))}; "
            "lvef_c3_require_projectnb_path(){ :; }; "
            "lvef_c3_require_private_projectnb_directory "
            f"{shlex.quote(str(candidate))}"
        )
        for mode, expected_status in (
            ("700", 0),
            ("2700", 0),
            ("750", 78),
            ("770", 78),
            ("2770", 78),
            ("2777", 78),
        ):
            completed = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                    "SYNTHETIC_MODE": mode,
                },
            )
            assert completed.returncode == expected_status


def test_control_plane_aggregate_profile_is_closed_and_safe() -> None:
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_offline_control_plane_preparation_summary_v1",
        "status": "PASS_OFFLINE_CONTROL_PLANE_PREPARED_UNAUTHORIZED",
        "attempt_id": "lvef_c3_phase1ee_synthetic_001",
        "governing_commit": "a" * 40,
        "production_batches": 19,
        "selected_studies": 4530,
        "selected_subjects": 4530,
        "normalized_source_objects": 335984,
        "selected_source_bytes": 1216569133322,
        "batch_plan_sha256": "b" * 64,
        "batch_plan_aggregate_sha256": "c" * 64,
        "runtime_authority_sha256": "d" * 64,
        "initial_ledger_set_sha256": "e" * 64,
        "execution_environment_sha256": "f" * 64,
        "authorization_scopes_granted": 0,
        "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
        "object_bodies_downloaded": 0,
        "real_dicom_extraction": False,
        "echoprime_inference": False,
        "model_fitting": False,
        "confirmatory_performance_accessed": False,
        "contains_identifiers": False,
        "contains_source_locators": False,
        "contains_private_project": False,
        "contains_restricted_paths": False,
    }
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
    )
    result = analysis_modes.validate_candidate_bytes(
        (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"),
        filename="lvef_c3_control_plane_preparation.summary.json",
        profile_name="phase1ee_control_plane_preparation_summary_json",
        policy=policy,
    )
    assert result["status"] == "PASS"


def test_control_plane_rejects_symlinked_checkout_or_attempt_ancestor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real = root / "real"
        real.mkdir()
        link = root / "linked"
        link.symlink_to(real, target_is_directory=True)
        try:
            prepare._require_no_symlink_ancestors(
                link / "attempts" / "synthetic", "SYNTHETIC"
            )
        except prepare.ControlPlanePreparationError as exc:
            assert str(exc) == "SYNTHETIC_SYMLINK_ANCESTOR"
        else:
            raise AssertionError("Control-plane preparation followed a symlinked ancestor")
