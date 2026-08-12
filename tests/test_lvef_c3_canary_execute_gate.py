from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary as canary
import lvef_c3_canary_dispatch as dispatch
import lvef_c3_canary_execution_authority as authority


class _State:
    def __init__(self, permitted: bool):
        self.permitted = permitted

    def permits(self, scope: str) -> bool:
        assert scope == "execute_exact_five_canary"
        return self.permitted


def test_execute_is_blocked_by_current_state_before_private_or_scheduler_paths() -> None:
    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=_State(False)
    ), mock.patch.object(
        canary, "validate_installation"
    ) as installation, mock.patch.object(
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
        installation.assert_not_called()
        private.assert_not_called()
        owner.assert_not_called()
        submit.assert_not_called()


def test_future_execute_branch_requires_both_authorities_and_exact_dispatch_result() -> None:
    future_authority = {"owner_authorized": True}
    terminal = {
        "status": "DISPATCHED_FROZEN_DAG",
        "submission_count": 5,
        "production_continuation_triggered": False,
    }
    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=_State(True)
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
        authority,
        "load_and_validate_execution_authority",
        return_value=future_authority,
    ) as owner, mock.patch.object(
        dispatch, "dispatch_authorized_canary", return_value=terminal
    ) as submit:
        result = canary.execute_authorized_canary()
    owner.assert_called_once_with(
        expected_governing_commit="a" * 40, require_output_absent=True
    )
    submit.assert_called_once_with(future_authority)
    assert result["frozen_scheduler_submissions"] == 5
    assert result["qsub_submissions"] == 5
    assert result["production_continuation"] is False
