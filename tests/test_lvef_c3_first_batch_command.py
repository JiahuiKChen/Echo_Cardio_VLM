from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_first_batch_command as command


def _inputs(root: Path) -> dict[str, Path]:
    worktree = root / "worktree"
    projectnb = root / "projectnb"
    worktree.mkdir(parents=True)
    projectnb.mkdir(parents=True)
    return {
        "dispatcher": worktree / "scripts" / "dispatcher.sh",
        "execution_environment": projectnb / "attempt" / "authority" / "execution.env",
        "launch_authority": projectnb / "attempt" / "authority" / "launch.json",
        "dispatch_authorization": projectnb / "attempt" / "authorizations" / "dispatch" / "FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json",
        "body_authorization": projectnb / "attempt" / "authorizations" / "download" / "c3_batch_000.authorization.json",
    }


def _render(root: Path) -> bytes:
    values = _inputs(root)
    with mock.patch.object(command, "WORKTREE_PREFIX", root / "worktree"), mock.patch.object(
        command, "PROJECTNB_PREFIX", root / "projectnb"
    ):
        return command.render(
            governing_commit="a" * 40,
            attempt_id="lvef_c3_phase1ee_synthetic_006",
            **values,
        )


def test_exact_first_batch_command_is_unexecuted_and_scope_free() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = _render(root)
        text = payload.decode("utf-8")
        assert "UNEXECUTED" in text
        assert "--submit FIRST_BATCH_DOWNLOAD" in text
        assert text.rstrip().endswith('"$DISPATCH_AUTHORIZATION" - 1')
        assert "qsub" not in text
        assert "owner-authorization-affirmed" not in text
        assert "gcloud" not in text.lower()


def test_command_is_owner_private_no_clobber_and_exact() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = _render(root)
        parent = root / "private"
        parent.mkdir(mode=0o700)
        output = parent / "first_batch.sh"
        command.write_no_clobber(output, payload)
        command.validate_exact(output, expected=payload)
        assert output.stat().st_mode & 0o777 == 0o600
        try:
            command.write_no_clobber(output, payload)
        except command.FirstBatchCommandError as exc:
            assert str(exc) == "COMMAND_OUTPUT_COLLISION"
        else:
            raise AssertionError("existing command was overwritten")


def test_command_rejects_tamper_symlink_and_unapproved_root() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = _render(root)
        parent = root / "private"
        parent.mkdir(mode=0o700)
        output = parent / "first_batch.sh"
        command.write_no_clobber(output, payload)
        output.write_bytes(payload + b"# changed\n")
        try:
            command.validate_exact(output, expected=payload)
        except command.FirstBatchCommandError as exc:
            assert str(exc) == "COMMAND_BYTES_NOT_EXACT"
        else:
            raise AssertionError("tampered command was accepted")
        link = parent / "link.sh"
        link.symlink_to(output)
        try:
            command.validate_exact(link, expected=payload)
        except command.FirstBatchCommandError as exc:
            assert str(exc) == "COMMAND_NOT_REGULAR"
        else:
            raise AssertionError("symlink command was accepted")

        values = _inputs(root / "second")
        with mock.patch.object(command, "WORKTREE_PREFIX", root / "second" / "worktree"), mock.patch.object(
            command, "PROJECTNB_PREFIX", root / "second" / "projectnb"
        ):
            try:
                command.render(
                    governing_commit="a" * 40,
                    attempt_id="lvef_c3_phase1ee_synthetic_006",
                    **{**values, "body_authorization": root / "outside.json"},
                )
            except command.FirstBatchCommandError as exc:
                assert str(exc) == "BODY_AUTHORIZATION_OUTSIDE_APPROVED_ROOT"
            else:
                raise AssertionError("outside authorization path was accepted")
