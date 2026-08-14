from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lvef_c3_full_scheduler_test",
    ROOT / "scripts/lvef_c3_full_scheduler.py",
)
assert SPEC and SPEC.loader
scheduler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduler)


HEAD = "a" * 40
ATTEMPT = f"lvef_c3_full_{'b' * 16}_{HEAD[:8]}"


def completed(
    stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def topology() -> object:
    return scheduler.build_topology(head=HEAD, attempt_id=ATTEMPT)


def test_exact_two_submission_topology_is_fixed_and_sequential() -> None:
    value = topology()
    array = value.array_command()
    assert array.count(str(scheduler.QSUB_PATH)) == 1
    assert array[1] == "-clear" and array.count("-clear") == 1
    assert array[array.index("-t") + 1] == "1-19"
    assert array[array.index("-tc") + 1] == "1"
    assert array[array.index("-P") + 1] == "mimicecho"
    assert array[array.index("-r") + 1] == "n"
    assert "gpus=1" in array and "gpu_c=8.0" in array and "gpu_memory=48G" in array
    assert str(scheduler.ARRAY_RUNNER) == array[-1]
    assert "-V" not in array and "-v" not in array

    finalizer = value.finalizer_command("8123456")
    assert finalizer[1] == "-clear" and finalizer.count("-clear") == 1
    assert finalizer[finalizer.index("-hold_jid") + 1] == "8123456"
    assert "gpus=1" not in finalizer
    assert str(scheduler.FINALIZER_RUNNER) == finalizer[-1]
    assert "-t" not in finalizer and "-V" not in finalizer and "-v" not in finalizer


def _assert_scheduler_error(
    function: object, *args: object, **kwargs: object
) -> scheduler.FullSchedulerError:
    try:
        function(*args, **kwargs)
    except scheduler.FullSchedulerError as exc:
        return exc
    raise AssertionError("expected FullSchedulerError")


def test_numeric_qsub_output_is_exact_and_ambiguous_forms_fail() -> None:
    for payload in (
        b"", b"0\n", b"12.1-19:1\n", b"12\n13\n", b" 12\n", b"12 extra\n"
    ):
        _assert_scheduler_error(scheduler.parse_numeric_qsub_stdout, payload)
    assert scheduler.parse_numeric_qsub_stdout(b"8123456\n") == "8123456"
    assert scheduler.parse_array_qsub_stdout(b"8123456\n") == "8123456"
    assert scheduler.parse_array_qsub_stdout(b"8123456.1-19:1\n") == "8123456"
    for payload in (b"8123456.1-18:1\n", b"8123456.1-19:2\n", b"8123456.1\n"):
        _assert_scheduler_error(scheduler.parse_array_qsub_stdout, payload)


def test_qsub_environment_canonicalizes_approved_alias_and_is_closed() -> None:
    account = pwd.getpwuid(os.geteuid())
    source = {
        "SGE_ROOT": str(scheduler.APPROVED_SGE_ROOT_ALIAS),
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "SHELL": account.pw_shell,
        "SGE_CELL": "default",
        "GOOGLE_APPLICATION_CREDENTIALS": "/not/forwarded",
        "PYTHONPATH": "/not/forwarded",
    }
    with mock.patch.object(
        scheduler,
        "validate_sge_root_authority",
        return_value="SGE_ROOT_APPROVED_ALIAS_RESOLVED",
    ):
        result, classification = scheduler.build_qsub_environment(source)
    assert result["SGE_ROOT"] == str(scheduler.CANONICAL_SGE_ROOT)
    assert classification == "SGE_ROOT_APPROVED_ALIAS_RESOLVED"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in result
    assert "PYTHONPATH" not in result
    assert set(result) <= set(scheduler.CONTROLLED_QSUB_ENVIRONMENT) | scheduler.SCHEDULER_CONTEXT_NAMES


def test_sge_root_traversal_is_rejected_before_resolution() -> None:
    for value in ("/usr/local/sge/../sge_root", "usr/local/sge/sge_root", "/tmp/sge"):
        captured = _assert_scheduler_error(scheduler.validate_sge_root_authority, value)
        assert captured.code == "SGE_ROOT_UNAPPROVED_LEXICAL_PATH"


def test_scheduler_tools_require_root_owned_nonwritable_executables() -> None:
    regular = os.stat_result(
        (stat.S_IFREG | 0o755, 0, 0, 1, 0, 0, 1, 0, 0, 0)
    )
    with (
        mock.patch.object(scheduler, "_require_nonsymlink_components"),
        mock.patch.object(scheduler, "_lstat", return_value=regular),
        mock.patch.object(scheduler.os, "access", return_value=True),
    ):
        scheduler.validate_scheduler_tools()

    for changed_mode, changed_uid in (
        (stat.S_IFREG | 0o775, 0),
        (stat.S_IFREG | 0o757, 0),
        (stat.S_IFREG | 0o655, 0),
        (stat.S_IFREG | 0o755, os.geteuid() + 1),
    ):
        metadata = os.stat_result(
            (changed_mode, 0, 0, 1, changed_uid, 0, 1, 0, 0, 0)
        )
        with (
            mock.patch.object(scheduler, "_require_nonsymlink_components"),
            mock.patch.object(scheduler, "_lstat", return_value=metadata),
            mock.patch.object(scheduler.os, "access", return_value=True),
        ):
            captured = _assert_scheduler_error(
                scheduler.validate_scheduler_tools
            )
        assert captured.code == "SCHEDULER_TOOL_AUTHORITY_INVALID"


def test_preflight_never_calls_qsub_or_claim() -> None:
    calls: list[str] = []

    def science(mode: str, **_: object) -> str:
        calls.append(mode)
        if mode == "--print-fixed-identity":
            return ATTEMPT
        return scheduler.SCIENCE_MARKERS[mode]

    with (
        mock.patch.object(scheduler, "validate_scheduler_tools"),
        mock.patch.object(scheduler, "build_qsub_environment", return_value=({"USER": "owner"}, "CANONICAL")),
        mock.patch.object(scheduler, "validate_git_authority", return_value=HEAD),
        mock.patch.object(scheduler, "run_science_mode", side_effect=science),
        mock.patch.object(scheduler, "validate_no_active_jobs") as qstat,
        mock.patch.object(scheduler.subprocess, "run") as any_process,
    ):
        value = scheduler.preflight()
    assert value.attempt_id == ATTEMPT
    assert calls == ["--validate-installation", "--print-fixed-identity", "--preflight-only"]
    qstat.assert_called_once()
    any_process.assert_not_called()


def test_render_cli_emits_explicit_scheduler_no_body_markers() -> None:
    value = topology()
    with (
        mock.patch.object(scheduler, "preflight", return_value=value) as preflight_gate,
        mock.patch.object(
            scheduler, "render", return_value=("ARRAY_COMMAND", "FINALIZER_COMMAND")
        ),
        mock.patch("builtins.print") as printer,
        mock.patch.object(scheduler.subprocess, "run") as process,
    ):
        assert scheduler.guarded_main(["--render"]) == 0
    process.assert_not_called()
    preflight_gate.assert_called_once_with(run_science_preflight=False)
    lines = [call.args[0] for call in printer.call_args_list]
    assert "FULL_C3_QSUB_COMMAND_RENDERING=PASS" in lines
    assert any(line.startswith("FULL_C3_ARRAY_QSUB_ARGV_SHA256=") for line in lines)
    assert any(
        line.startswith("FULL_C3_FINALIZER_QSUB_TEMPLATE_ARGV_SHA256=")
        for line in lines
    )
    assert all("/restricted/" not in line for line in lines)
    assert "FULL_C3_SCHEDULER_CONTEXT=PASS" in lines
    assert "FULL_C3_ACTIVE_MATCHING_PRODUCTION_JOBS=0" in lines
    assert "FULL_C3_SEQUENTIAL_TWO_SUBMISSION_TOPOLOGY=PASS" in lines
    assert "FULL_C3_SCHEDULER_PREFLIGHT=PASS" in lines
    assert "QSUB_SUBMISSIONS=0" in lines


def test_active_job_gate_is_owner_scoped_and_rejects_other_commit_names() -> None:
    value = topology()
    environment = {"USER": "owner"}
    commands: list[list[str]] = []

    def qstat(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        payload = (
            b"<job_info><queue_info><job_list><JB_name>"
            b"lvef_c3_full_seq_deadbeef"
            b"</JB_name></job_list></queue_info><job_info/></job_info>"
        )
        return completed(stdout=payload)

    captured = _assert_scheduler_error(
        scheduler.validate_no_active_jobs,
        value,
        environment,
        runner=qstat,
    )
    assert captured.code == "ACTIVE_MATCHING_PRODUCTION_JOB_EXISTS"
    assert commands == [[str(scheduler.QSTAT_PATH), "-xml", "-u", "owner"]]

    for legacy_name in (
        "c3_dl1_0123456789ab",
        "c3_dlr_0123456789ab",
        "c3_ext_0123456789ab",
        "c3_emb_0123456789ab",
        "c3_pre_0123456789ab",
        "c3_ret_0123456789ab",
        "c3_fin_0123456789ab",
    ):
        def legacy_qstat(
            command: list[str], *, _name: str = legacy_name, **_: object
        ) -> subprocess.CompletedProcess[bytes]:
            return completed(
                stdout=(
                    "<job_info><queue_info><job_list><JB_name>"
                    f"{_name}"
                    "</JB_name></job_list></queue_info><job_info/></job_info>"
                ).encode("ascii")
            )

        captured = _assert_scheduler_error(
            scheduler.validate_no_active_jobs,
            value,
            environment,
            runner=legacy_qstat,
        )
        assert captured.code == "ACTIVE_MATCHING_PRODUCTION_JOB_EXISTS"


def test_science_control_stdout_is_exactly_one_line() -> None:
    marker = scheduler.SCIENCE_MARKERS["--preflight-only"].encode("ascii")
    for stdout in (marker, marker + b"\n"):
        assert scheduler.run_science_mode(
            "--preflight-only",
            runner=lambda *_args, _stdout=stdout, **_kwargs: completed(stdout=_stdout),
        ) == marker.decode("ascii")
    for stdout in (marker + b"\n\n", marker + b"\r\n", marker + b"\nextra"):
        captured = _assert_scheduler_error(
            scheduler.run_science_mode,
            "--preflight-only",
            runner=lambda *_args, _stdout=stdout, **_kwargs: completed(stdout=_stdout),
        )
        assert captured.code == "SCIENCE_CONTROL_OUTPUT_INVALID"


def test_submit_calls_qsub_exactly_twice_and_second_is_held_on_first() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = topology()
        attempt_root = Path(directory) / "attempts" / ATTEMPT
        attempt_root.mkdir(parents=True)
        os.chmod(attempt_root, 0o700)
        test_topology = scheduler.SchedulerTopology(
            head=value.head,
            attempt_id=value.attempt_id,
            scheduler_root=attempt_root / "scheduler",
            array_job_name=value.array_job_name,
            finalizer_job_name=value.finalizer_job_name,
        )
        science_calls: list[str] = []

        def science(mode: str, **_: object) -> str:
            science_calls.append(mode)
            return scheduler.SCIENCE_MARKERS.get(mode, ATTEMPT)

        qsub_commands: list[list[str]] = []

        def qsub(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
            qsub_commands.append(command)
            job_id = (
                b"8123456.1-19:1\n"
                if len(qsub_commands) == 1
                else b"8123457\n"
            )
            return completed(stdout=job_id)

        with (
            mock.patch.object(
                scheduler,
                "validate_installation",
                return_value=(test_topology, {"USER": "owner"}, "CANONICAL"),
            ),
            mock.patch.object(scheduler, "run_science_mode", side_effect=science),
            mock.patch.object(scheduler, "validate_no_active_jobs") as qstat,
        ):
            array_id, finalizer_id = scheduler.submit(qsub_runner=qsub)

        assert (array_id, finalizer_id) == ("8123456", "8123457")
        assert len(qsub_commands) == 2
        assert qsub_commands[1][qsub_commands[1].index("-hold_jid") + 1] == array_id
        assert science_calls == ["--claim-submission"]
        assert qstat.call_count == 2
        receipt = test_topology.scheduler_root / "submission_receipt.restricted.json"
        assert receipt.is_file()
        scheduler.validate_submission_receipt(
            scheduler.json.loads(receipt.read_text()),
            topology=test_topology,
            expected_qsub_environment_sha256=(
                scheduler.qsub_environment_sha256({"USER": "owner"})
            ),
        )
        changed = scheduler.json.loads(receipt.read_text())
        changed["qsub_environment_sha256"] = "0" * 64
        captured = _assert_scheduler_error(
            scheduler.validate_submission_receipt,
            changed,
            topology=test_topology,
            expected_qsub_environment_sha256=(
                scheduler.qsub_environment_sha256({"USER": "owner"})
            ),
        )
        assert captured.code == "SUBMISSION_RECEIPT_ENVIRONMENT_INVALID"


def test_active_job_gate_rejects_non_qstat_xml_and_qsub_stderr() -> None:
    value = topology()
    captured = _assert_scheduler_error(
        scheduler.validate_no_active_jobs,
        value,
        {"USER": "owner"},
        runner=lambda *_args, **_kwargs: completed(stdout=b"<ok/>"),
    )
    assert captured.code == "ACTIVE_JOB_CHECK_FAILED"
    scheduler.validate_no_active_jobs(
        value,
        {"USER": "owner"},
        runner=lambda *_args, **_kwargs: completed(
            stdout=b"<job_info><queue_info/><job_info/></job_info>"
        ),
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        captured = _assert_scheduler_error(
            scheduler._capture_qsub,
            "array",
            ["qsub"],
            root=root,
            environment={"USER": "owner"},
            runner=lambda *_args, **_kwargs: completed(
                stdout=b"8123456.1-19:1\n", stderr=b"warning\n"
            ),
            parser=scheduler.parse_array_qsub_stdout,
        )
        assert captured.code == "ARRAY_QSUB_PROCESS_FAILED"
        assert (root / "array.qsub.stderr.restricted").read_bytes() == b"warning\n"


def test_failed_array_or_finalizer_never_causes_a_third_qsub() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = topology()
        for fail_on in (1, 2):
            attempt_root = Path(directory) / str(fail_on) / ATTEMPT
            attempt_root.mkdir(parents=True)
            os.chmod(attempt_root, 0o700)
            test_topology = scheduler.SchedulerTopology(
                head=value.head,
                attempt_id=value.attempt_id,
                scheduler_root=attempt_root / "scheduler",
                array_job_name=value.array_job_name,
                finalizer_job_name=value.finalizer_job_name,
            )
            calls = 0

            def qsub(_: list[str], **__: object) -> subprocess.CompletedProcess[bytes]:
                nonlocal calls
                calls += 1
                if calls == fail_on:
                    return completed(stderr=b"safe scheduler failure", returncode=1)
                return completed(stdout=f"{9000000 + calls}\n".encode())

            with (
                mock.patch.object(scheduler, "validate_installation", return_value=(test_topology, {"USER": "owner"}, "CANONICAL")),
                mock.patch.object(scheduler, "run_science_mode"),
                mock.patch.object(scheduler, "validate_no_active_jobs"),
            ):
                _assert_scheduler_error(scheduler.submit, qsub_runner=qsub)
            assert calls == fail_on


def test_submission_claim_attempt_root_requires_700_or_2700() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = topology()
        attempt = Path(directory) / ATTEMPT
        attempt.mkdir(mode=0o700)
        candidate = scheduler.SchedulerTopology(
            head=value.head,
            attempt_id=value.attempt_id,
            scheduler_root=attempt / "scheduler",
            array_job_name=value.array_job_name,
            finalizer_job_name=value.finalizer_job_name,
        )
        os.chmod(attempt, 0o770)
        captured = _assert_scheduler_error(
            scheduler._require_attempt_root, candidate
        )
        assert captured.code == "SUBMISSION_CLAIM_ATTEMPT_ROOT_INVALID"
        os.chmod(attempt, 0o700)
        assert scheduler._require_attempt_root(candidate) == candidate.scheduler_root


def test_shell_interfaces_are_fixed_and_do_not_accept_paths_or_commands() -> None:
    submitter = (ROOT / "scripts/scc_submit_lvef_c3_full_sequential.sh").read_text()
    array = (ROOT / "scripts/scc_run_lvef_c3_full_sequential.sh").read_text()
    finalizer = (ROOT / "scripts/scc_run_lvef_c3_full_finalizer.sh").read_text()
    assert "[[ $# -eq 1 ]]" in submitter
    assert "--validate-installation|--preflight-only|--render|--submit" in submitter
    assert "--preflight-report" in submitter
    assert '"$SCIENCE" --preflight-report' in submitter
    assert "qsub" not in submitter
    assert "[[ $# -eq 0 ]]" in array and "--run-array-task" in array
    assert "SGE_TASK_ID" in array
    assert "[[ $# -eq 0 ]]" in finalizer and "--run-cohort-finalizer" in finalizer
    assert "CUDA_VISIBLE_DEVICES=''" in finalizer
    assert "''|undefined" in finalizer
    assert "-I -B -X pycache_prefix=" in submitter + array + finalizer


def test_job_wrappers_bind_all_temporary_storage_to_private_projectnb_roots() -> None:
    common = (
        ROOT / "scripts/lvef_c3_production_scheduler_common.sh"
    ).read_text()
    array = (ROOT / "scripts/scc_run_lvef_c3_full_sequential.sh").read_text()
    finalizer = (ROOT / "scripts/scc_run_lvef_c3_full_finalizer.sh").read_text()
    fixed_base = (
        "JOB_STORAGE_BASE=/restricted/projectnb/mimicecho/"
        "lvef_multitask_c3_v2/scheduler_runtime"
    )
    bindings = {
        "TMPDIR": "$JOB_STORAGE_ROOT/tmp",
        "XDG_CACHE_HOME": "$JOB_STORAGE_ROOT/cache/xdg",
        "TORCH_HOME": "$JOB_STORAGE_ROOT/cache/torch",
        "MPLCONFIGDIR": "$JOB_STORAGE_ROOT/cache/matplotlib",
        "NUMBA_CACHE_DIR": "$JOB_STORAGE_ROOT/cache/numba",
        "PIP_CACHE_DIR": "$JOB_STORAGE_ROOT/cache/pip",
        "JOBLIB_TEMP_FOLDER": "$JOB_STORAGE_ROOT/tmp/joblib",
    }
    for wrapper in (array, finalizer):
        assert fixed_base in wrapper
        assert (
            'COMMON="$WORKTREE/scripts/'
            'lvef_c3_production_scheduler_common.sh"' in wrapper
        )
        assert '[[ -f "$COMMON" && ! -L "$COMMON" ]]' in wrapper
        assert 'source "$COMMON"' in wrapper
        assert '"$JOB_STORAGE_ROOT/cache"' in wrapper
        assert 'lvef_c3_require_projectnb_path "$storage_directory"' in wrapper
        assert (
            'lvef_c3_require_private_projectnb_directory "$storage_directory"'
            in wrapper
        )
        assert wrapper.count('mkdir -p "$storage_directory"') == 2
        for name, value in bindings.items():
            assert f'export {name}="{value}"' in wrapper
        assert "$HOME" not in wrapper and "${HOME" not in wrapper
        for prohibited in ("rm ", "rmdir ", "trap ", "purge", "cleanup"):
            assert prohibited not in wrapper.lower()

    assert 'JOB_STORAGE_PARENT="$JOB_STORAGE_BASE/array_job_$JOB_ID"' in array
    assert 'JOB_STORAGE_ROOT="$JOB_STORAGE_PARENT/task_$SGE_TASK_ID"' in array
    assert "CUDA_VISIBLE_DEVICES" not in array
    assert 'JOB_STORAGE_PARENT="$JOB_STORAGE_BASE/finalizer_job_$JOB_ID"' in finalizer
    assert 'JOB_STORAGE_ROOT="$JOB_STORAGE_PARENT/cohort"' in finalizer
    assert "export CUDA_VISIBLE_DEVICES=''" in finalizer

    # The sourced fixed helper enforces every existing component is not a
    # symlink, ownership by the current job owner, and effective-private
    # 0700/2700 modes for each created directory.
    assert "[[ ! -L \"$cursor\" ]]" in common
    assert "PRIVATE_DIRECTORY_WRONG_OWNER" in common
    assert '[[ "$1" == "700" || "$1" == "2700" ]]' in common
    assert "CUDA_VISIBLE_DEVICES" not in common
    assert "\nsource " not in common
