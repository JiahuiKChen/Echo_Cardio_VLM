from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary as canary
import lvef_c3_canary_dispatch as dispatch
import lvef_c3_canary_execution_authority as authority
import lvef_c3_canary_state as canary_state


class _State:
    canary_lifecycle = SimpleNamespace(private_state_root="/private/canary-state")


def test_top_level_wrapper_disables_source_adjacent_bytecode_reads() -> None:
    wrapper = (ROOT / "scripts/scc_run_lvef_c3_canary.sh").read_text()
    assert "-X pycache_prefix=/dev/null/lvef_c3_canary" in wrapper


def test_execute_is_blocked_by_missing_private_state_before_packet_or_scheduler() -> None:
    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=_State()
    ), mock.patch.object(
        canary,
        "validate_installation",
        return_value={"governing_commit": "a" * 40},
    ) as installation, mock.patch.object(
        canary_state,
        "load_state",
        side_effect=canary_state.CanaryStateError("CANARY_STATE_ROOT_MISSING"),
    ), mock.patch.object(
        canary, "validate_scc_private_authority"
    ) as private, mock.patch.object(
        authority, "load_and_validate_execution_authority"
    ) as owner, mock.patch.object(
        dispatch, "dispatch_authorized_canary"
    ) as submit:
        output = io.StringIO()
        with redirect_stdout(output):
            assert canary.main(["--execute"]) == 78
        assert "REAL_CANARY_CANONICAL_STATE_AUTHORIZATION_REQUIRED" in output.getvalue()
        installation.assert_called_once_with(None)
        private.assert_not_called()
        owner.assert_not_called()
        submit.assert_not_called()


def test_execute_branch_requires_lifecycle_packet_and_exact_dispatch_result() -> None:
    private_state = {
        "current_state": "CANARY_MANIFEST_SEALED",
        "run_id": "lvef_c3_exact_five_canary_ab12cd34",
    }
    future_authority = {
        "owner_authorized": True,
        "run_id": private_state["run_id"],
        "authorization_sha256": "b" * 64,
        "manifest": {"embedded_sha256": "c" * 64},
        "scheduler_plan": {"canonical_sha256": "d" * 64},
    }
    terminal = {
        "status": "DISPATCHED_FROZEN_DAG",
        "submission_count": 5,
        "production_continuation_triggered": False,
    }
    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=_State()
    ), mock.patch.object(
        canary,
        "validate_installation",
        return_value={"governing_commit": "a" * 40},
    ), mock.patch.object(
        canary,
        "validate_scc_private_authority",
        return_value={
            "status": "PASS_SCC_PRIVATE_AUTHORITY_READ_ONLY",
            "scc_private_authority_required": True,
        },
    ), mock.patch.object(
        canary_state, "load_state", return_value=private_state
    ), mock.patch.object(
        canary_state, "assert_execute_permitted"
    ) as permit, mock.patch.object(
        canary_state, "transition_state"
    ) as transition, mock.patch.object(
        authority,
        "load_and_validate_execution_authority",
        return_value=future_authority,
    ) as owner, mock.patch.object(
        dispatch, "dispatch_authorized_canary", return_value=terminal
    ) as submit:
        result = canary.execute_authorized_canary()
    owner.assert_called_once_with(
        authority.FIXED_PATH,
        expected_governing_commit="a" * 40,
        require_output_absent=True,
        paths=None,
    )
    permit.assert_called_once()
    transition.assert_called_once()
    submit.assert_called_once_with(future_authority)
    assert result["frozen_scheduler_submissions"] == 5
    assert result["qsub_submissions"] == 5
    assert result["production_continuation"] is False


def test_dispatch_filesystem_failure_records_terminal_fail_after_executing() -> None:
    run_id = "lvef_c3_exact_five_canary_ab12cd34"
    sealed = {"current_state": "CANARY_MANIFEST_SEALED", "run_id": run_id}
    executing = {"current_state": "CANARY_EXECUTING", "run_id": run_id}
    future_authority = {
        "owner_authorized": True,
        "run_id": run_id,
        "authorization_sha256": "b" * 64,
        "manifest": {"embedded_sha256": "c" * 64},
        "scheduler_plan": {"canonical_sha256": "d" * 64},
    }
    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=_State()
    ), mock.patch.object(
        canary, "validate_installation", return_value={"governing_commit": "a" * 40}
    ), mock.patch.object(
        canary, "validate_scc_private_authority",
        return_value={
            "status": "PASS_SCC_PRIVATE_AUTHORITY_READ_ONLY",
            "scc_private_authority_required": True,
        },
    ), mock.patch.object(
        canary_state, "load_state", side_effect=[sealed, executing]
    ), mock.patch.object(
        canary_state, "assert_execute_permitted"
    ), mock.patch.object(
        canary_state, "transition_state"
    ) as transition, mock.patch.object(
        authority, "load_and_validate_execution_authority",
        return_value=future_authority,
    ), mock.patch.object(
        dispatch, "dispatch_authorized_canary", side_effect=OSError("synthetic")
    ):
        output = io.StringIO()
        with redirect_stdout(output):
            assert canary.main(["--execute"]) == 78
    assert "BLOCKED_CANARY_DISPATCH_FILESYSTEM_FAILED" in output.getvalue()
    assert transition.call_count == 2
    assert transition.call_args_list[-1].kwargs["target_state"] == (
        "CANARY_TERMINAL_FAIL"
    )
