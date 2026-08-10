from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: every identity, path, quota, and byte value below
# is generated in a temporary directory and does not represent SCC data.

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import capture_lvef_c3_live_quota as prior
import capture_lvef_c3_post_expansion_capacity as capacity
import lvef_multitask_analysis_modes as analysis_modes
import test_lvef_c3_live_quota as live_fixture


CURRENT_COMMIT = "c" * 40
CONTROL_PATH = "/synthetic/control/project"
CONTROL_TARGET = "/synthetic/control"
CONTROL_USAGE = 10_500_000_000
CONTROL_POLICY = ROOT / "configs" / "lvef_c3_control_tier_policy.json"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _bundle(root: Path) -> tuple[Path, dict[str, object]]:
    parent_root = root / "parent"
    parent_receipt_path, _ = live_fixture._bundle(parent_root, quota_gb="1989")
    parent_aggregate = prior.validate_restricted_evidence(
        parent_receipt_path,
        expected_commit=live_fixture.COMMIT,
        now_utc=live_fixture.NOW,
    )
    assert parent_aggregate["status"] == "PASS_LIVE_QUOTA_GATE"
    parent_aggregate_path = parent_root / "aggregate.json"
    _write_private(parent_aggregate_path, _json_bytes(parent_aggregate))

    supplement = root / "supplement"
    supplement.mkdir()
    supplement.chmod(0o700)
    findmnt = _json_bytes(
        {
            "filesystems": [
                {
                    "source": "synthetic-control-fs",
                    "target": CONTROL_TARGET,
                    "fstype": "nfs4",
                    "options": "rw,nosuid,nodev",
                }
            ]
        }
    )
    df = (
        "Filesystem 1B-blocks Used Avail Mounted on\n"
        f"synthetic-control-fs 100000000000 40000000000 60000000000 {CONTROL_TARGET}\n"
    ).encode("utf-8")
    du = f"{CONTROL_USAGE}\t{CONTROL_PATH}\n".encode("utf-8")
    commands = {
        "findmnt": live_fixture._tool_record(
            supplement,
            "findmnt",
            [
                "--json",
                "--target",
                CONTROL_PATH,
                "--output",
                "SOURCE,TARGET,FSTYPE,OPTIONS",
            ],
            findmnt,
        ),
        "df": live_fixture._tool_record(
            supplement,
            "df",
            ["-B1", "--output=source,size,used,avail,target", CONTROL_PATH],
            df,
        ),
        "du": live_fixture._tool_record(
            supplement,
            "du",
            ["-x", "-s", "-B1", CONTROL_PATH],
            du,
        ),
    }
    parent_receipt_payload = parent_receipt_path.read_bytes()
    parent_aggregate_payload = parent_aggregate_path.read_bytes()
    receipt: dict[str, object] = {
        "schema_version": capacity.SCHEMA_VERSION,
        "receipt_kind": capacity.RECEIPT_KIND,
        "status": capacity.RECEIPT_STATUS,
        "captured_at_utc": "2026-08-10T12:00:00Z",
        "capture_identity": {
            "effective_uid": os.getuid(),
            "effective_username_sha256": _digest(
                pwd.getpwuid(os.getuid()).pw_name.encode("utf-8")
            ),
            "hostname_sha256": _digest(socket.gethostname().encode("utf-8")),
            "git_commit": CURRENT_COMMIT,
        },
        "parent_authority": {
            "restricted_receipt_path": str(parent_receipt_path),
            "restricted_receipt_sha256": _digest(parent_receipt_payload),
            "restricted_receipt_byte_count": len(parent_receipt_payload),
            "aggregate_path": str(parent_aggregate_path),
            "aggregate_sha256": _digest(parent_aggregate_payload),
            "aggregate_byte_count": len(parent_aggregate_payload),
            "governing_commit": live_fixture.COMMIT,
        },
        "control_path": CONTROL_PATH,
        "commands": commands,
        "no_mutation_attestations": {
            "quota_changed": False,
            "files_moved": 0,
            "files_deleted": 0,
            "cloud_requests": 0,
            "object_bodies_downloaded": 0,
            "pquota_commands_repeated": 0,
            "research_filesystem_commands_repeated": 0,
        },
    }
    receipt_path = supplement / "control_receipt.json"
    _write_private(receipt_path, _json_bytes(receipt))
    return receipt_path, receipt


def _rewrite(path: Path, value: object) -> None:
    _write_private(path, _json_bytes(value))


def _validate(path: Path) -> dict[str, object]:
    return capacity.validate_composite_capacity(
        path,
        control_policy_path=CONTROL_POLICY,
        expected_commit=CURRENT_COMMIT,
        now_utc=datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
    )


def _validate_with_policy(path: Path, policy: Path) -> dict[str, object]:
    return capacity.validate_composite_capacity(
        path,
        control_policy_path=policy,
        expected_commit=CURRENT_COMMIT,
        now_utc=datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
    )


def _error(path: Path) -> str:
    try:
        _validate(path)
    except capacity.PostExpansionCapacityError as exc:
        return str(exc)
    raise AssertionError("synthetic evidence unexpectedly passed")


def test_composite_passes_without_conflating_evidence_and_control_gate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        output = _validate(path)
        assert output["status"] == capacity.OUTPUT_STATUS
        assert output["live_quota_evidence_passed"] is True
        assert output["minimum_effective_quota_gate_passed"] is True
        assert output["physical_filesystem_capacity_gate_passed"] is True
        assert output["projected_200gb_reserve_gate_passed"] is True
        assert output["research_capacity_gate_passed"] is True
        assert output["research_file_quota_reported_count"] == 10_000_000
        assert output["research_file_usage_reported_count"] == 335_984
        assert output["research_file_quota_gate_passed"] is True
        assert output["control_tier_quota_evidence_passed"] is True
        assert output["control_tier_operational_burden_authority_bound"] is True
        assert output["control_tier_max_additional_write_burden_bytes"] == 10_000_000_000
        assert output["control_tier_available_after_burden_bytes"] == -9_500_000_000
        assert output["backed_control_tier_gate_passed"] is False
        assert output["backed_control_tier_byte_gate_passed"] is False
        assert output["backed_control_tier_file_gate_passed"] is True
        assert output["full_c3_authorized"] is False
        assert output["dicom_body_transfer_authorized"] is False
        policy, _ = analysis_modes.load_policy(
            ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
        )
        result = analysis_modes.validate_candidate_bytes(
            _json_bytes(output),
            filename="lvef_c3_post_expansion_capacity.summary.json",
            profile_name="phase1ee_post_expansion_capacity_summary_json",
            policy=policy,
        )
        assert result["status"] == "PASS"


def test_exact_research_and_control_arithmetic_and_mount_ruling() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        output = _validate(path)
        assert output["research_quota_bytes"] == 1_989_000_000_000
        assert output["research_quota_available_bytes"] == (
            output["research_quota_bytes"]
            - output["research_exact_allocated_usage_bytes"]
        )
        assert output["control_quota_bytes"] == 11_000_000_000
        assert output["control_exact_allocated_usage_bytes"] == CONTROL_USAGE
        assert output["control_quota_available_bytes"] == 500_000_000
        assert output["control_file_quota_reported_count"] == 500_000
        assert output["control_file_usage_reported_count"] == 20_123
        assert output["control_file_quota_gate_passed"] is True
        assert output["control_filesystem_available_bytes"] == 60_000_000_000
        assert output["mounted_filesystems_distinct"] is True
        assert output["research_bind_mount"] is False
        assert output["control_bind_mount"] is False


def test_owner_reallocation_options_preserve_minimum_and_reserve() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        output = _validate(path)
        assert output["minimum_control_tier_option_bytes"] == 25_000_000_000
        assert output["minimum_option_research_quota_bytes"] == 1_975_000_000_000
        assert output["minimum_option_quota_gate_passed"] is True
        assert output["minimum_option_reserve_gate_passed"] is True
        assert output["minimum_option_control_gate_passed"] is True
        assert output["preferred_control_tier_option_bytes"] == 50_000_000_000
        assert output["preferred_option_research_quota_bytes"] == 1_950_000_000_000
        assert output["preferred_option_quota_gate_passed"] is True
        assert output["preferred_option_reserve_gate_passed"] is True
        assert output["preferred_option_control_gate_passed"] is True


def test_control_burden_policy_is_closed_and_arithmetically_bound() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, _ = _bundle(root)
        policy = json.loads(CONTROL_POLICY.read_text(encoding="utf-8"))
        policy["maximum_additional_control_write_burden_bytes"] += 1
        policy_path = root / "altered_policy.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        try:
            _validate_with_policy(path, policy_path)
        except capacity.PostExpansionCapacityError as exc:
            assert str(exc) == "CONTROL_POLICY_BURDEN_ARITHMETIC_INVALID"
        else:
            raise AssertionError("altered control-tier burden was accepted")

        policy_link = root / "policy_link.json"
        policy_link.symlink_to(CONTROL_POLICY)
        try:
            _validate_with_policy(path, policy_link)
        except capacity.PostExpansionCapacityError as exc:
            assert str(exc) == "CONTROL_POLICY_NOT_REGULAR_NOFOLLOW_FILE"
        else:
            raise AssertionError("symlinked control-tier policy was accepted")


def test_file_count_quota_is_finite_and_fail_closed_when_insufficient() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        output = _validate(path)
        output["research_file_quota_reported_count"] = 1_000_000
        output["research_file_quota_available_count"] = (
            1_000_000 - output["research_file_usage_reported_count"]
        )
        output["research_file_quota_gate_passed"] = False
        capacity.validate_aggregate_output(output)
        assert output["research_file_quota_gate_passed"] is False
        assert output["research_capacity_gate_passed"] is True


def test_parent_receipt_and_aggregate_are_hash_bound_and_exactly_rederived() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        parent = receipt["parent_authority"]
        assert isinstance(parent, dict)
        aggregate_path = Path(str(parent["aggregate_path"]))
        aggregate_path.write_bytes(aggregate_path.read_bytes() + b" ")
        assert _error(path) == "AGGREGATE_HASH_OR_SIZE_MISMATCH"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        parent = receipt["parent_authority"]
        assert isinstance(parent, dict)
        parent["restricted_receipt_sha256"] = "f" * 64
        _rewrite(path, receipt)
        assert _error(path) == "RESTRICTED_RECEIPT_HASH_OR_SIZE_MISMATCH"


def test_no_repeat_and_no_mutation_attestations_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        attestations = receipt["no_mutation_attestations"]
        assert isinstance(attestations, dict)
        attestations["pquota_commands_repeated"] = 1
        _rewrite(path, receipt)
        assert _error(path) == "READ_ONLY_OR_NO_REPEAT_BOUNDARY_VIOLATED"

    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        attestations = receipt["no_mutation_attestations"]
        assert isinstance(attestations, dict)
        attestations["cloud_requests"] = 1
        _rewrite(path, receipt)
        assert _error(path) == "READ_ONLY_OR_NO_REPEAT_BOUNDARY_VIOLATED"


def test_command_argv_raw_hash_and_symlink_protections_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, receipt = _bundle(Path(directory))
        commands = receipt["commands"]
        assert isinstance(commands, dict)
        du = commands["du"]
        assert isinstance(du, dict)
        argv = du["argv"]
        assert isinstance(argv, list)
        argv[1] = "--apparent-size"
        du["argv_sha256"] = _digest(
            json.dumps(argv, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        _rewrite(path, receipt)
        assert _error(path) == "COMMAND_ARGV_CONTRACT_INVALID"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, receipt = _bundle(root)
        parent = receipt["parent_authority"]
        assert isinstance(parent, dict)
        aggregate = Path(str(parent["aggregate_path"]))
        moved = aggregate.with_suffix(".moved")
        aggregate.rename(moved)
        aggregate.symlink_to(moved)
        assert _error(path).startswith("BOUND_EVIDENCE_INVALID_")


def test_closed_output_rejects_extra_key_and_authorization_expansion() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        output = _validate(path)
        altered = deepcopy(output)
        altered["unexpected"] = True
        try:
            capacity.validate_aggregate_output(altered)
        except capacity.PostExpansionCapacityError as exc:
            assert str(exc) == "OUTPUT_SCHEMA_NOT_EXACT"
        else:
            raise AssertionError("extra output key was accepted")

        altered = deepcopy(output)
        altered["dicom_body_transfer_authorized"] = True
        try:
            capacity.validate_aggregate_output(altered)
        except capacity.PostExpansionCapacityError as exc:
            assert str(exc) == "OUTPUT_FIXED_FALSE_AUTHORITY_INVALID"
        else:
            raise AssertionError("authorization expansion was accepted")


def test_aggregate_contains_no_restricted_paths_or_identity_values() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _bundle(Path(directory))
        output = _validate(path)
        serialized = json.dumps(output, sort_keys=True).lower()
        for forbidden in (
            "/synthetic/",
            "control/project",
            "synthetic-control-fs",
            "effective_uid",
            "username",
            "hostname",
            "resolved_executable_path",
            "argv",
        ):
            assert forbidden not in serialized


def test_wrapper_runs_only_three_control_tier_resource_commands() -> None:
    source = (
        ROOT / "scripts" / "scc_capture_lvef_c3_post_expansion_capacity.sh"
    ).read_text(encoding="utf-8")
    assert source.count("capture_one findmnt") == 1
    assert source.count("capture_one df") == 1
    assert source.count("capture_one du") == 1
    assert "capture_one pquota" not in source
    assert "LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT" not in source
    assert "pquota_commands_repeated\": 0" in source
    assert 'source "$CAPTURE_ENV"' not in source
    assert "CAPTURE_ENV_ASSIGNMENT_NOT_LITERAL" in source
    for forbidden in (
        "gcloud ",
        "gsutil ",
        "bq ",
        "qsub ",
        "alt=media",
        "dcmread",
        "torch",
    ):
        assert forbidden not in source


def test_wrapper_end_to_end_uses_only_fake_control_commands_and_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        parent_receipt, parent = _bundle(root / "authority")
        parent_spec = parent["parent_authority"]
        assert isinstance(parent_spec, dict)

        worktree = root / "worktree"
        scripts = worktree / "scripts"
        scripts.mkdir(parents=True)
        configs = worktree / "configs"
        configs.mkdir(parents=True)
        for name in (
            "capture_lvef_c3_live_quota.py",
            "capture_lvef_c3_post_expansion_capacity.py",
            "scc_capture_lvef_c3_post_expansion_capacity.sh",
        ):
            source = ROOT / "scripts" / name
            target = scripts / name
            target.write_bytes(source.read_bytes())
            target.chmod(source.stat().st_mode & 0o777)
        (configs / CONTROL_POLICY.name).write_bytes(CONTROL_POLICY.read_bytes())
        subprocess.run(
            ["git", "init", "-b", "codex/lvef-multitask-revalidation"],
            cwd=worktree,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "synthetic@example.invalid"],
            cwd=worktree,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Synthetic Test"],
            cwd=worktree,
            check=True,
        )
        subprocess.run(["git", "add", "scripts", "configs"], cwd=worktree, check=True)
        subprocess.run(
            ["git", "commit", "-m", "synthetic authority"],
            cwd=worktree,
            check=True,
            capture_output=True,
        )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

        control = root / "control"
        control.mkdir()
        tools = root / "bin"
        tools.mkdir()
        command_counter = root / "command_counter"
        command_counter.mkdir()

        def executable(name: str, body: str) -> None:
            path = tools / name
            path.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                f"printf 1 >{str(command_counter / name)!r}\n"
                + body,
                encoding="utf-8",
            )
            path.chmod(0o755)

        executable(
            "findmnt",
            "target=\"$3\"\n"
            "printf '{\"filesystems\":[{\"source\":\"controlfs\",\"target\":\"%s\",\"fstype\":\"nfs4\",\"options\":\"rw,nosuid\"}]}\\n' \"$target\"\n",
        )
        executable(
            "df",
            "target=\"$3\"\n"
            "printf '%s\\n' 'Filesystem 1B-blocks Used Avail Mounted on'\n"
            "printf 'controlfs 100000000000 40000000000 60000000000 %s\\n' \"$target\"\n",
        )
        executable(
            "du",
            "target=\"$4\"\n"
            "printf '10500000000\\t%s\\n' \"$target\"\n",
        )

        attempt = root / "attempt"
        attempt.mkdir()
        attempt.chmod(0o700)
        restricted = attempt / "restricted"
        restricted.mkdir()
        restricted.chmod(0o700)
        capture_env = restricted / "capture.env"
        python = Path(sys.executable)
        values = {
            "WORKTREE": str(worktree),
            "EXPECTED_COMMIT": commit,
            "PYTHON": str(python),
            "EXPECTED_PYTHON_SHA256": _digest(python.read_bytes()),
            "PHASE1EE_ATTEMPT_ROOT": str(attempt),
            "LVEF_C3_CONTROL_ROOT": str(control),
            "PARENT_CAPACITY_RECEIPT": str(
                parent_spec["restricted_receipt_path"]
            ),
            "EXPECTED_PARENT_CAPACITY_RECEIPT_SHA256": str(
                parent_spec["restricted_receipt_sha256"]
            ),
            "EXPECTED_PARENT_CAPACITY_RECEIPT_BYTES": str(
                parent_spec["restricted_receipt_byte_count"]
            ),
            "PARENT_CAPACITY_AGGREGATE": str(parent_spec["aggregate_path"]),
            "EXPECTED_PARENT_CAPACITY_AGGREGATE_SHA256": str(
                parent_spec["aggregate_sha256"]
            ),
            "EXPECTED_PARENT_CAPACITY_AGGREGATE_BYTES": str(
                parent_spec["aggregate_byte_count"]
            ),
            "PARENT_CAPACITY_GOVERNING_COMMIT": str(
                parent_spec["governing_commit"]
            ),
        }
        import shlex

        capture_env.write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        capture_env.chmod(0o600)
        environment = os.environ.copy()
        environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
        command = [
            "bash",
            str(scripts / "scc_capture_lvef_c3_post_expansion_capacity.sh"),
            "--capture-env",
            str(capture_env),
        ]
        first = subprocess.run(
            command,
            cwd=worktree,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert "POST_EXPANSION_CAPACITY_VALIDATION=PASS" in first.stdout
        assert "PQUOTA_COMMANDS_REPEATED=0" in first.stdout
        assert sorted(item.name for item in command_counter.iterdir()) == [
            "df",
            "du",
            "findmnt",
        ]
        output = attempt / "aggregate" / "lvef_c3_post_expansion_capacity.summary.json"
        assert output.is_file() and not output.is_symlink()
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        before = _digest(output.read_bytes())
        second = subprocess.run(
            command,
            cwd=worktree,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert second.returncode == 2
        assert "FAILED_CAPTURE_OUTPUT_COLLISION" in second.stdout
        assert _digest(output.read_bytes()) == before
