from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
import traceback
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lvef_c3_worker_scheduler_context_test",
    ROOT / "scripts/lvef_c3_full_scheduler.py",
)
assert SPEC and SPEC.loader
scheduler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduler)


UID = 8123
USERNAME = "sealed-owner"
HOME = "/restricted/sealed/home"
SHELL = "/bin/bash"
JOB_ID = "8123456"
ROLE = "lvef_c3_r8u_r5_resume"
COMMIT = "a" * 40
RUNNER_SHA256 = "b" * 64
PYTHON_SHA256 = "c" * 64


def _account(
    *, name: str = USERNAME, home: str = HOME, shell: str = SHELL
) -> SimpleNamespace:
    return SimpleNamespace(pw_name=name, pw_dir=home, pw_shell=shell)


def _sealed_qsub_environment() -> dict[str, str]:
    value = dict(scheduler.CONTROLLED_QSUB_ENVIRONMENT)
    value.update(
        {
            "SGE_ROOT": str(scheduler.CANONICAL_SGE_ROOT),
            "SGE_CELL": "default",
            "SGE_QMASTER_PORT": "6444",
            "HOME": HOME,
            "USER": USERNAME,
            "LOGNAME": USERNAME,
            "SHELL": SHELL,
        }
    )
    return value


def _ambient_worker_environment() -> dict[str, str]:
    return {
        "JOB_ID": JOB_ID,
        "USER": USERNAME,
        "LOGNAME": USERNAME,
        "HOME": HOME,
        "SHELL": SHELL,
        "SSH_AUTH_SOCK": "/not/forwarded",
        "GOOGLE_APPLICATION_CREDENTIALS": "/not/forwarded",
    }


def _worker_kwargs() -> dict[str, object]:
    sealed = _sealed_qsub_environment()
    return {
        "expected_effective_uid": UID,
        "expected_scheduler_username": USERNAME,
        "canonical_home": HOME,
        "expected_job_id": JOB_ID,
        "expected_job_role": ROLE,
        "observed_job_role": ROLE,
        "expected_qsub_environment_sha256": (
            scheduler.qsub_environment_sha256(sealed)
        ),
        "sealed_qsub_environment": sealed,
        "expected_implementation_commit": COMMIT,
        "observed_implementation_commit": COMMIT,
        "expected_runner_sha256": RUNNER_SHA256,
        "observed_runner_sha256": RUNNER_SHA256,
        "expected_python_sha256": PYTHON_SHA256,
        "observed_python_sha256": PYTHON_SHA256,
        "source_environment": _ambient_worker_environment(),
    }


def _assert_error(code: str, function: object, *args: object, **kwargs: object) -> None:
    try:
        function(*args, **kwargs)
    except scheduler.FullSchedulerError as exc:
        assert exc.code == code
        return
    raise AssertionError(f"expected {code}")


def _build(**changes: object) -> object:
    arguments = _worker_kwargs()
    arguments.update(changes)
    with (
        mock.patch.object(scheduler.os, "geteuid", return_value=UID),
        mock.patch.object(scheduler.pwd, "getpwuid", return_value=_account()),
        mock.patch.object(
            scheduler,
            "validate_sge_root_authority",
            return_value="SGE_ROOT_CANONICAL_INPUT",
        ),
    ):
        return scheduler.build_worker_scheduler_context(**arguments)


def test_login_qsub_builder_remains_strict_for_account_fields_and_nss() -> None:
    source = _sealed_qsub_environment()
    with (
        mock.patch.object(scheduler.os, "geteuid", return_value=UID),
        mock.patch.object(scheduler.pwd, "getpwuid", return_value=_account()),
        mock.patch.object(
            scheduler,
            "validate_sge_root_authority",
            return_value="SGE_ROOT_CANONICAL_INPUT",
        ),
    ):
        environment, _ = scheduler.build_qsub_environment(source)
        assert environment == source
        for name, mismatch in (
            ("USER", "other-owner"),
            ("HOME", "/restricted/other/home"),
            ("SHELL", "/bin/sh"),
        ):
            changed = dict(source)
            changed[name] = mismatch
            if name == "USER":
                changed["LOGNAME"] = mismatch
            _assert_error(
                "SCHEDULER_IDENTITY_INVALID",
                scheduler.build_qsub_environment,
                changed,
            )
    with (
        mock.patch.object(scheduler.os, "geteuid", return_value=UID),
        mock.patch.object(scheduler.pwd, "getpwuid", side_effect=KeyError),
        mock.patch.object(
            scheduler,
            "validate_sge_root_authority",
            return_value="SGE_ROOT_CANONICAL_INPUT",
        ),
    ):
        _assert_error(
            "SCHEDULER_IDENTITY_INVALID",
            scheduler.build_qsub_environment,
            source,
        )


def test_worker_context_uses_sealed_identity_and_has_a_closed_environment() -> None:
    arguments = _worker_kwargs()
    with (
        mock.patch.object(scheduler.os, "geteuid", return_value=UID),
        mock.patch.object(scheduler.pwd, "getpwuid", return_value=_account()),
        mock.patch.object(
            scheduler,
            "validate_sge_root_authority",
            return_value="SGE_ROOT_CANONICAL_INPUT",
        ),
        mock.patch.object(
            scheduler,
            "build_qsub_environment",
            side_effect=AssertionError("worker called submitter builder"),
        ) as submitter_builder,
    ):
        value = scheduler.build_worker_scheduler_context(**arguments)
    submitter_builder.assert_not_called()
    assert value.environment == {
        **scheduler.CONTROLLED_QSUB_ENVIRONMENT,
        "SGE_ROOT": str(scheduler.CANONICAL_SGE_ROOT),
        "SGE_CELL": "default",
        "SGE_QMASTER_PORT": "6444",
        "USER": USERNAME,
        "LOGNAME": USERNAME,
        "HOME": HOME,
    }
    assert set(value.environment) <= (
        set(scheduler.CONTROLLED_QSUB_ENVIRONMENT)
        | scheduler.WORKER_SCHEDULER_CONTEXT_NAMES
    )
    assert "SHELL" not in value.environment
    assert "JOB_ID" not in value.environment
    assert "SSH_AUTH_SOCK" not in value.environment
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in value.environment
    assert value.diagnostics.effective_uid_match
    assert value.diagnostics.job_id_match
    assert value.diagnostics.task_context_match
    assert value.diagnostics.job_role_match
    assert value.diagnostics.passwd_lookup_status == "PASS"
    assert value.diagnostics.classifications == ("PASS",)


def test_ambient_identity_mismatches_are_diagnostic_only() -> None:
    ambient = _ambient_worker_environment()
    ambient.update(
        {
            "USER": "scheduler-user",
            "LOGNAME": "scheduler-logname",
            "HOME": "/execution/home",
            "SHELL": "/bin/sh",
        }
    )
    value = _build(source_environment=ambient)
    assert value.environment["USER"] == USERNAME
    assert value.environment["LOGNAME"] == USERNAME
    assert value.environment["HOME"] == HOME
    assert "SHELL" not in value.environment
    assert not value.diagnostics.observed_user_match
    assert not value.diagnostics.observed_logname_match
    assert not value.diagnostics.observed_home_match
    assert not value.diagnostics.observed_shell_match
    assert value.diagnostics.classifications == (
        "SCHEDULER_ENV_USER_MISMATCH",
        "SCHEDULER_ENV_LOGNAME_MISMATCH",
        "SCHEDULER_ENV_HOME_MISMATCH",
        "SCHEDULER_ENV_SHELL_MISMATCH",
    )


def test_passwd_lookup_unavailable_is_nonblocking_and_separately_classified() -> None:
    arguments = _worker_kwargs()
    with (
        mock.patch.object(scheduler.os, "geteuid", return_value=UID),
        mock.patch.object(scheduler.pwd, "getpwuid", side_effect=OSError),
        mock.patch.object(
            scheduler,
            "validate_sge_root_authority",
            return_value="SGE_ROOT_CANONICAL_INPUT",
        ),
    ):
        value = scheduler.build_worker_scheduler_context(**arguments)
    assert not value.diagnostics.passwd_lookup_available
    assert value.diagnostics.passwd_lookup_status == "LOOKUP_UNAVAILABLE"
    assert value.diagnostics.classifications == (
        "SCHEDULER_ACCOUNT_LOOKUP_UNAVAILABLE",
    )
    assert value.diagnostics.effective_uid_match


def test_passwd_consistency_statuses_do_not_override_kernel_identity() -> None:
    for account, expected_status, expected_classification in (
        (
            _account(name="different-owner"),
            "NAME_MISMATCH",
            "SCHEDULER_ACCOUNT_NAME_MISMATCH",
        ),
        (
            _account(home="/different/home"),
            "HOME_MISMATCH",
            "SCHEDULER_ACCOUNT_HOME_MISMATCH",
        ),
        (
            _account(shell="/bin/sh"),
            "SHELL_MISMATCH",
            "SCHEDULER_ACCOUNT_SHELL_MISMATCH",
        ),
    ):
        arguments = _worker_kwargs()
        with (
            mock.patch.object(scheduler.os, "geteuid", return_value=UID),
            mock.patch.object(scheduler.pwd, "getpwuid", return_value=account),
            mock.patch.object(
                scheduler,
                "validate_sge_root_authority",
                return_value="SGE_ROOT_CANONICAL_INPUT",
            ),
        ):
            value = scheduler.build_worker_scheduler_context(**arguments)
        assert value.diagnostics.passwd_lookup_status == expected_status
        assert value.diagnostics.classifications == (expected_classification,)
        assert value.diagnostics.effective_uid_match


def test_effective_uid_job_and_role_contradictions_are_field_specific() -> None:
    arguments = _worker_kwargs()
    with mock.patch.object(scheduler.os, "geteuid", return_value=UID + 1):
        _assert_error(
            "SCHEDULER_EFFECTIVE_UID_MISMATCH",
            scheduler.build_worker_scheduler_context,
            **arguments,
        )

    wrong_job = _ambient_worker_environment()
    wrong_job["JOB_ID"] = "8123457"
    _assert_error(
        "SCHEDULER_JOB_ID_BINDING_MISMATCH",
        _build,
        source_environment=wrong_job,
    )
    _assert_error(
        "SCHEDULER_JOB_ROLE_MISMATCH",
        _build,
        observed_job_role="lvef_c3_r8u_r5_probe",
    )


def test_nonarray_and_exact_array_task_contexts_are_bound() -> None:
    undefined = _ambient_worker_environment()
    undefined["SGE_TASK_ID"] = "undefined"
    assert _build(source_environment=undefined).diagnostics.task_context_match

    unexpected_array = _ambient_worker_environment()
    unexpected_array["SGE_TASK_ID"] = "17"
    _assert_error(
        "SCHEDULER_TASK_ID_BINDING_MISMATCH",
        _build,
        source_environment=unexpected_array,
    )
    assert _build(
        source_environment=unexpected_array, expected_task_id="17"
    ).diagnostics.task_context_match
    _assert_error(
        "SCHEDULER_TASK_ID_BINDING_MISMATCH",
        _build,
        source_environment=unexpected_array,
        expected_task_id="18",
    )


def test_runtime_and_submission_bindings_are_field_specific() -> None:
    for name, value, code in (
        (
            "observed_runner_sha256",
            "d" * 64,
            "SCHEDULER_RUNNER_AUTHORITY_MISMATCH",
        ),
        (
            "observed_python_sha256",
            "d" * 64,
            "SCHEDULER_PYTHON_AUTHORITY_MISMATCH",
        ),
        (
            "observed_implementation_commit",
            "d" * 40,
            "SCHEDULER_IMPLEMENTATION_COMMIT_MISMATCH",
        ),
        (
            "expected_qsub_environment_sha256",
            "d" * 64,
            "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH",
        ),
    ):
        _assert_error(code, _build, **{name: value})

    sealed = _sealed_qsub_environment()
    expected_hash = scheduler.qsub_environment_sha256(sealed)
    sealed["SGE_CELL"] = "changed"
    _assert_error(
        "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH",
        _build,
        sealed_qsub_environment=sealed,
        expected_qsub_environment_sha256=expected_hash,
    )


def test_sealed_qsub_environment_and_canonical_home_fail_closed() -> None:
    sealed = _sealed_qsub_environment()
    sealed["UNSEALED_VARIABLE"] = "not-allowed"
    _assert_error(
        "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH",
        _build,
        sealed_qsub_environment=sealed,
        expected_qsub_environment_sha256=(
            scheduler.qsub_environment_sha256(sealed)
        ),
    )
    missing = _sealed_qsub_environment()
    del missing["SHELL"]
    _assert_error(
        "SCHEDULER_QSUB_ENVIRONMENT_BINDING_MISMATCH",
        _build,
        sealed_qsub_environment=missing,
        expected_qsub_environment_sha256=(
            scheduler.qsub_environment_sha256(missing)
        ),
    )
    _assert_error(
        "SCHEDULER_WORKER_CONTEXT_INVALID",
        _build,
        canonical_home="/restricted/sealed/../home",
    )


def test_worker_diagnostics_never_contain_identity_or_path_values() -> None:
    value = _build()
    diagnostic_values = value.diagnostics._asdict()
    assert all(
        isinstance(item, (bool, str, tuple)) for item in diagnostic_values.values()
    )
    for name, item in diagnostic_values.items():
        if isinstance(item, str):
            assert name == "passwd_lookup_status"
            assert item in scheduler.WORKER_PASSWD_LOOKUP_STATUSES
        elif isinstance(item, tuple):
            assert name == "classifications"
            assert set(item) <= scheduler.WORKER_DIAGNOSTIC_CLASSIFICATIONS
        else:
            assert isinstance(item, bool)
    rendered = repr(diagnostic_values)
    assert USERNAME not in rendered
    assert HOME not in rendered
    assert SHELL not in rendered
    assert str(UID) not in rendered


def test_submitter_builder_source_is_not_loosened_for_worker_context() -> None:
    worker_source = inspect.getsource(scheduler.build_worker_scheduler_context)
    submitter_source = inspect.getsource(scheduler.build_qsub_environment)
    assert "build_qsub_environment(" not in worker_source
    assert "pwd.getpwuid(os.geteuid())" in submitter_source
    assert 'result["USER"] != account.pw_name' in submitter_source
    assert 'Path(result["HOME"]) != Path(account.pw_dir)' in submitter_source
    assert 'Path(result["SHELL"]) != Path(account.pw_shell)' in submitter_source


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        try:
            function()
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
        else:
            print(f"PASS {name}")
            passed += 1
    print(f"SUMMARY passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
