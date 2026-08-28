from __future__ import annotations

"""Dependency-light execution tests for the fixed R8R/R8U SCC wrapper."""

import inspect
from pathlib import Path
import shlex
import subprocess
import tempfile
import traceback
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "scc_run_lvef_c3_r8r_recovery_continuation.sh"


def _synthetic_runner(root: Path) -> tuple[Path, Path]:
    worktree = root / "worktree"
    scripts = worktree / "scripts"
    scripts.mkdir(parents=True)
    common = scripts / "lvef_c3_production_scheduler_common.sh"
    common.write_text(
        "lvef_c3_require_projectnb_path() { :; }\n"
        "lvef_c3_require_private_projectnb_directory() { :; }\n",
        encoding="utf-8",
    )
    (scripts / "lvef_c3_r8r_recovery_continuation.py").write_text(
        "# synthetic controller argument only\n",
        encoding="utf-8",
    )

    capture = root / "capture.txt"
    python = root / "synthetic-python"
    python.write_text(
        "#!/bin/bash\n"
        "set -eu\n"
        "{\n"
        "  printf 'cwd=%s\\n' \"$PWD\"\n"
        "  printf 'cuda=%s\\n' \"${CUDA_VISIBLE_DEVICES-__UNSET__}\"\n"
        "  printf 'tmpdir=%s\\n' \"${TMPDIR-}\"\n"
        "  printf 'xdg=%s\\n' \"${XDG_CACHE_HOME-}\"\n"
        "  printf 'argv='\n"
        "  for argument in \"$@\"; do printf '<%s>' \"$argument\"; done\n"
        "  printf '\\n'\n"
        "} > \"$R8U_RUNNER_CAPTURE\"\n",
        encoding="utf-8",
    )
    python.chmod(0o700)

    source = RUNNER.read_text(encoding="utf-8")
    replacements = {
        "WORKTREE=/restricted/project/mimicecho/code/"
        "Echo_Cardio_VLM_lvef_multitask": (
            f"WORKTREE={shlex.quote(str(worktree))}"
        ),
        "PYTHON=/restricted/project/mimicecho/code/"
        "Echo_Cardio_VLM/.venv-echoprime/bin/python": (
            f"PYTHON={shlex.quote(str(python))}"
        ),
        "JOB_STORAGE_BASE=/restricted/projectnb/mimicecho/"
        "lvef_multitask_c3_v2/scheduler_runtime": (
            f"JOB_STORAGE_BASE={shlex.quote(str(root / 'storage'))}"
        ),
    }
    for original, replacement in replacements.items():
        assert source.count(original) == 1
        source = source.replace(original, replacement, 1)
    synthetic = root / "runner.sh"
    synthetic.write_text(source, encoding="utf-8")
    synthetic.chmod(0o700)
    return synthetic, capture


def _execute(
    *,
    job_name: str,
    task_id: str | None,
    slots: str = "4",
    cuda: str | None = "gpu0",
    arguments: Sequence[str] = (),
) -> tuple[subprocess.CompletedProcess[str], Mapping[str, str]]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        runner, capture = _synthetic_runner(root)
        environment = {
            "JOB_ID": "8123456",
            "JOB_NAME": job_name,
            "NSLOTS": slots,
            "R8U_RUNNER_CAPTURE": str(capture),
        }
        if task_id is not None:
            environment["SGE_TASK_ID"] = task_id
        if cuda is not None:
            environment["CUDA_VISIBLE_DEVICES"] = cuda
        completed = subprocess.run(
            [str(runner), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=environment,
        )
        observed: dict[str, str] = {}
        if capture.is_file():
            for line in capture.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                observed[key] = value
        return completed, observed


def _assert_success(
    completed: subprocess.CompletedProcess[str], observed: Mapping[str, str]
) -> None:
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert set(observed) == {"cwd", "cuda", "tmpdir", "xdg", "argv"}


def _assert_refused(
    completed: subprocess.CompletedProcess[str], observed: Mapping[str, str]
) -> None:
    assert completed.returncode in {64, 78}
    assert observed == {}


def test_r8u_batch16_recovery_is_fixed_gpu_nonarray_nslots4() -> None:
    completed, observed = _execute(
        job_name="lvef_c3_r8u_rec_deadbeef",
        task_id=None,
        cuda="gpu7",
    )
    _assert_success(completed, observed)
    assert observed["cuda"] == "gpu7"
    assert observed["tmpdir"].endswith(
        "/r8u_r2_job_8123456/r8u_r2_batch16_recovery/tmp"
    )
    assert observed["xdg"].endswith(
        "/r8u_r2_job_8123456/r8u_r2_batch16_recovery/cache/xdg"
    )
    assert "<pycache_prefix=/dev/null/lvef_c3_r8u_r2>" in observed["argv"]
    assert observed["argv"].endswith("<--run-batch16-recovery>")

    for task_id, slots in (("16", "4"), (None, "3")):
        refused, no_capture = _execute(
            job_name="lvef_c3_r8u_rec_deadbeef",
            task_id=task_id,
            slots=slots,
        )
        _assert_refused(refused, no_capture)


def test_r8u_continuation_accepts_only_tasks17_19_and_preserves_gpu() -> None:
    for task_id in ("17", "18", "19"):
        completed, observed = _execute(
            job_name="lvef_c3_r8u_seq_deadbeef",
            task_id=task_id,
            cuda="gpu2",
        )
        _assert_success(completed, observed)
        assert observed["cuda"] == "gpu2"
        assert observed["tmpdir"].endswith(
            f"/r8u_r2_job_8123456/r8u_r2_array_task_{task_id}/tmp"
        )
        assert observed["argv"].endswith(
            "<--run-continuation-17-19-array-task>"
        )

    for task_id in (None, "undefined", "16", "20", "4", "17.0"):
        refused, no_capture = _execute(
            job_name="lvef_c3_r8u_seq_deadbeef",
            task_id=task_id,
        )
        _assert_refused(refused, no_capture)
    wrong_slots, no_capture = _execute(
        job_name="lvef_c3_r8u_seq_deadbeef",
        task_id="17",
        slots="8",
    )
    _assert_refused(wrong_slots, no_capture)


def test_r8u_finalizer_is_fixed_cpu_nonarray_nslots4() -> None:
    completed, observed = _execute(
        job_name="lvef_c3_r8u_fin_deadbeef",
        task_id=None,
        cuda="gpu5",
    )
    _assert_success(completed, observed)
    assert observed["cuda"] == ""
    assert observed["tmpdir"].endswith(
        "/r8u_r2_job_8123456/r8u_r2_finalizer/tmp"
    )
    assert observed["argv"].endswith(
        "<--run-r8u-continuation-finalizer>"
    )

    for task_id, slots in (("19", "4"), (None, "1")):
        refused, no_capture = _execute(
            job_name="lvef_c3_r8u_fin_deadbeef",
            task_id=task_id,
            slots=slots,
        )
        _assert_refused(refused, no_capture)


def test_r8r_dispatch_and_storage_paths_remain_unchanged() -> None:
    cases = (
        (
            "lvef_c3_r8r_rec_deadbeef",
            None,
            "recovery",
            "--recover-batch3-preservation",
            "",
        ),
        (
            "lvef_c3_r8r_seq_deadbeef",
            "4",
            "array_task_4",
            "--run-continuation-array-task",
            "gpu3",
        ),
        (
            "lvef_c3_r8r_fin_deadbeef",
            None,
            "finalizer",
            "--run-continuation-finalizer",
            "",
        ),
    )
    for job_name, task_id, role, mode, expected_cuda in cases:
        completed, observed = _execute(
            job_name=job_name,
            task_id=task_id,
            cuda="gpu3",
        )
        _assert_success(completed, observed)
        assert observed["cuda"] == expected_cuda
        assert observed["tmpdir"].endswith(
            f"/r8r_job_8123456/{role}/tmp"
        )
        assert "<pycache_prefix=/dev/null/lvef_c3_r8r>" in observed["argv"]
        assert observed["argv"].endswith(f"<{mode}>")


def test_r8u_and_r8r_storage_roles_cannot_collide() -> None:
    r8r_completed, r8r = _execute(
        job_name="lvef_c3_r8r_rec_deadbeef",
        task_id=None,
    )
    r8u_completed, r8u = _execute(
        job_name="lvef_c3_r8u_rec_deadbeef",
        task_id=None,
    )
    _assert_success(r8r_completed, r8r)
    _assert_success(r8u_completed, r8u)
    assert "/r8r_job_8123456/recovery/" in r8r["tmpdir"]
    assert (
        "/r8u_r2_job_8123456/r8u_r2_batch16_recovery/"
        in r8u["tmpdir"]
    )
    assert r8r["tmpdir"] != r8u["tmpdir"]


def test_runner_rejects_nonfixed_names_and_positional_arguments() -> None:
    for job_name in (
        "lvef_c3_r8u_rec_deadbee",
        "lvef_c3_r8u_rec_deadbeef0",
        "lvef_c3_r8u_rec_DEADBEEF",
        "lvef_c3_r8u_gpu_deadbeef",
        "lvef_c3_r9u_rec_deadbeef",
    ):
        refused, no_capture = _execute(job_name=job_name, task_id=None)
        _assert_refused(refused, no_capture)
    refused, no_capture = _execute(
        job_name="lvef_c3_r8u_rec_deadbeef",
        task_id=None,
        arguments=("--run-batch16-recovery",),
    )
    assert refused.returncode == 64
    assert no_capture == {}


def test_every_r8u_runner_test_is_zero_argument() -> None:
    tests = {
        name: value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    }
    assert tests
    assert all(not inspect.signature(value).parameters for value in tests.values())


def _run_dependency_light() -> int:
    passed = 0
    failed = 0
    skipped = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        if inspect.signature(function).parameters:
            print(f"SKIP {name}: requires a fixture")
            skipped += 1
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
    print(f"SUMMARY passed={passed} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_dependency_light())
