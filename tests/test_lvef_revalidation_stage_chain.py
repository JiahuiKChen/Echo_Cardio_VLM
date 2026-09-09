"""Execution-order and claim-consumption tests; no scheduler or project data."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lvef_revalidation_stage_chain as chain


def fixture(tmp_path):
    tmp_path.chmod(0o700)
    ctx = {"root": tmp_path, "run": {"repository_root": str(ROOT), "analysis_commit": "a" * 40},
           "manifest_sha256": "b" * 64}
    env = {key: "4" for key in chain.THREAD_KEYS}
    env.update(JOB_ID="12345", NSLOTS="4")
    return ctx, env


def test_exact_stage_order_binds_manifest_and_renders_only_validated_bundle(tmp_path):
    ctx, env = fixture(tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write(b'{"status":"SYNTHETIC_PASS"}\n')
        return SimpleNamespace(returncode=0)
    result = chain.execute(ctx, tmp_path / "manifest.json", environment=env, command_runner=run)
    assert result["completed_stages"] == list(chain.STAGES)
    assert result["status"] == "PASS_ANALYSIS_STAGE_CHAIN"
    assert calls[0][2] == "validate" and "--seal" not in calls[0]
    assert calls[1][2] == "validate" and calls[1][-1] == "--seal"
    assert [cmd[2] for cmd in calls[2:7]] == ["development", "freeze", "release-test", "evaluate", "report"]
    assert all("b" * 64 in command for command in calls[:-1])
    assert calls[-1][2:] == ["--bundle", str(tmp_path / "aggregate_bundle.restricted.json"), "--output", str(tmp_path / "figures")]
    assert result["visual_quality_review_pending"] is True
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (tmp_path / "stage_logs").iterdir())


@pytest.mark.parametrize("prior", ["development.claim.restricted.json", "test_evaluation.claim.restricted.json", "analysis_lock.restricted.json"])
def test_consumed_scientific_claim_or_lock_stops_before_any_execution(tmp_path, prior):
    ctx, env = fixture(tmp_path)
    (tmp_path / prior).write_text("preserved prior authority")
    with pytest.raises(chain.authority.AuthorityError, match="PRIOR_EXECUTION_REQUIRES_RECONCILIATION"):
        chain.execute(ctx, tmp_path / "manifest.json", environment=env,
                      command_runner=lambda *a, **k: pytest.fail("must not execute"))
    assert not (tmp_path / "stage_chain.claim.restricted.json").exists()
    assert (tmp_path / prior).read_text() == "preserved prior authority"


def test_failed_development_prevents_freeze_release_and_test_and_cannot_restart(tmp_path):
    ctx, env = fixture(tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command[2])
        return SimpleNamespace(returncode=78 if command[2] == "development" else 0)
    result = chain.execute(ctx, tmp_path / "manifest.json", environment=env, command_runner=run)
    assert calls == ["validate", "validate", "development"]
    assert result["failed_stage"] == "development" and result["completed_stages"] == ["validate", "seal"]
    assert json.loads((tmp_path / "stage_chain.failure.restricted.json").read_text())["automatic_retry_attempted"] is False
    with pytest.raises(chain.authority.AuthorityError, match="PRIOR_EXECUTION_REQUIRES_RECONCILIATION"):
        chain.execute(ctx, tmp_path / "manifest.json", environment=env, command_runner=run)
    assert len(calls) == 3


@pytest.mark.parametrize("change", [{"JOB_ID": ""}, {"NSLOTS": "1"}, {"OPENBLAS_NUM_THREADS": "32"}])
def test_requires_actual_four_slot_scheduler_context_before_claim(tmp_path, change):
    ctx, env = fixture(tmp_path)
    env.update(change)
    with pytest.raises(chain.authority.AuthorityError, match="CPU_SCHEDULER_PROFILE_REQUIRED"):
        chain.execute(ctx, tmp_path / "manifest.json", environment=env,
                      command_runner=lambda *a, **k: pytest.fail("must not execute"))
    assert not list(tmp_path.iterdir())
