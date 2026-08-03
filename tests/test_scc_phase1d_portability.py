from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve_lvef_scc_python.sh"
BLOCK_RUNNER = ROOT / "scripts" / "run_lvef_scc_bash_block.sh"
PHASE1D_COMMANDS = (
    ROOT / "docs" / "lvef_multitask" / "scc_phase1d_execution_commands.md"
)
NEW_SCC_PYTHON_HELPERS = (
    ROOT / "scripts" / "audit_canonical_selected_clip_inventory.py",
    ROOT / "scripts" / "audit_duplicate_clip_keys_v2.py",
    ROOT / "scripts" / "lvef_multitask_clinical_metadata.py",
    ROOT / "scripts" / "lvef_multitask_clip_provenance.py",
)

BARE_PYTHON_COMMAND = re.compile(
    r"(?:^|[;&|()][ \t]*)(?:command[ \t]+)?python(?:[0-9]+(?:\.[0-9]+)*)?(?=[ \t])"
)
BARE_PYTHON_ASSIGNMENT = re.compile(
    r"^[ \t]*[A-Za-z_][A-Za-z0-9_]*=[\"']?python(?:[0-9]+(?:\.[0-9]+)*)?[\"']?(?:[ \t]*$|[ \t])"
)
PYTHON_ENV_SHEBANG = re.compile(r"^#!.*(?:/usr/bin/env[ \t]+)?python(?:[0-9.]*)\b")
DIRECT_PROJECT_SCRIPT = re.compile(
    r"(?:^|[;&|()][ \t]*)(?:\./)?scripts/[A-Za-z0-9_./-]+\.py(?=[ \t]|$)"
)


def bash_code_lines(markdown: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    inside = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if not inside and line.strip() == "```bash":
            inside = True
            continue
        if inside and line.strip() == "```":
            inside = False
            continue
        if inside:
            lines.append((number, line))
    return lines


def bash_code_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in markdown.splitlines():
        if current is None and line.strip() == "```bash":
            current = []
            continue
        if current is not None and line.strip() == "```":
            blocks.append("\n".join(current) + "\n")
            current = None
            continue
        if current is not None:
            current.append(line)
    assert current is None, "unterminated Bash code block"
    return blocks


def unsafe_python_invocations(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for number, line in lines:
        if (
            BARE_PYTHON_COMMAND.search(line)
            or BARE_PYTHON_ASSIGNMENT.search(line)
            or PYTHON_ENV_SHEBANG.search(line)
            or DIRECT_PROJECT_SCRIPT.search(line)
        ):
            violations.append((number, line))
    return violations


def test_bare_project_python_invocation_is_rejected_synthetically() -> None:
    bad = [(1, "python scripts/lvef_multitask_clinical_metadata.py --help")]
    bad3 = [(1, "python3 - scripts/lvef_multitask_clinical_metadata.py")]
    direct = [(1, "scripts/lvef_multitask_clinical_metadata.py --help")]
    good = [
        (
            1,
            '"$LVEF_SCC_PYTHON_RESOLVED" '
            "scripts/lvef_multitask_clinical_metadata.py --help",
        )
    ]
    assert unsafe_python_invocations(bad)
    assert unsafe_python_invocations(bad3)
    assert unsafe_python_invocations(direct)
    assert unsafe_python_invocations(good) == []


def test_all_lvef_scc_command_blocks_use_explicit_python() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "docs" / "lvef_multitask").glob("scc*.md")):
        for number, line in unsafe_python_invocations(
            bash_code_lines(path.read_text(encoding="utf-8"))
        ):
            violations.append(f"{path.relative_to(ROOT)}:{number}:{line}")
    for path in (RESOLVER, BLOCK_RUNNER):
        numbered = list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))
        for number, line in unsafe_python_invocations(numbered):
            violations.append(f"{path.relative_to(ROOT)}:{number}:{line}")
    for path in NEW_SCC_PYTHON_HELPERS:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        if PYTHON_ENV_SHEBANG.search(first_line):
            violations.append(
                f"{path.relative_to(ROOT)}:1:environment-dependent Python shebang"
            )
    assert violations == []


def test_phase1d_command_blocks_have_unique_markers_and_parse_as_bash() -> None:
    markdown = PHASE1D_COMMANDS.read_text(encoding="utf-8")
    expected_markers = {
        "phase1d-portability-preflight",
        "phase1d-duplicate-provenance-v2",
        "phase1d-canonical-clip-inventory",
        "phase1d-clinical-metadata",
    }
    markers = re.findall(
        r"^<!-- lvef-scc-block:([A-Za-z0-9_.-]+) -->$", markdown, re.M
    )
    assert set(markers) == expected_markers
    assert len(markers) == len(expected_markers)

    blocks = bash_code_blocks(markdown)
    assert len(blocks) == len(expected_markers) + 1  # dispatcher plus four bodies
    for index, block in enumerate(blocks):
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=block,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"Bash block {index}: {completed.stderr}"


def test_dispatcher_validates_python_before_creating_phase1d_packet() -> None:
    dispatcher = bash_code_blocks(PHASE1D_COMMANDS.read_text(encoding="utf-8"))[0]
    resolver_position = dispatcher.index("scripts/resolve_lvef_scc_python.sh")
    packet_creation_position = dispatcher.index(
        'mkdir -p "$phase1d_root/aggregate" "$phase1d_root/restricted"'
    )
    assert resolver_position < packet_creation_position
    assert "phase1d_dispatch_status=PYTHON_PREFLIGHT_FAILED" in dispatcher


def test_phase1d_audit_blocks_match_current_cli_contracts() -> None:
    markdown = PHASE1D_COMMANDS.read_text(encoding="utf-8")
    contracts = {
        "audit_duplicate_clip_keys_v2.py": {
            "--component-manifest",
            "--component-embedding-npz",
            "--component-extraction-manifest",
            "--component-dicom-audit",
            "--merged-manifest",
            "--merged-embedding-npz",
            "--selected-studies",
            "--aggregate-output-dir",
            "--restricted-output-dir",
            "--expected-duplicate-keys",
            "--expected-embedding-dim",
            "--vector-rtol",
            "--vector-atol",
        },
        "audit_canonical_selected_clip_inventory.py": {
            "--component-manifest",
            "--component-extraction-manifest",
            "--component-dicom-audit",
            "--selected-studies",
            "--hash-mode",
            "--expected-selected-imaging-studies",
            "--aggregate-output-dir",
            "--restricted-output-dir",
        },
        "lvef_multitask_clinical_metadata.py": {
            "--mapping-csv",
            "--aggregate-output-dir",
            "--restricted-output-dir",
            "--followup-output-dir",
        },
    }
    for script_name, options in contracts.items():
        source = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert script_name in markdown
        for option in options:
            assert option in source, f"{script_name} no longer accepts {option}"
            assert option in markdown, f"Phase 1D command omits {script_name} {option}"
    assert markdown.count("--component-dicom-audit") == 2


def test_resolver_accepts_supported_override_and_records_imports() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        record = Path(temporary) / "resolver.json"
        environment = os.environ.copy()
        environment["LVEF_SCC_PYTHON"] = sys.executable
        completed = subprocess.run(
            [str(RESOLVER), "--record-json", str(record)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert Path(completed.stdout.strip()).is_absolute()
        payload = json.loads(record.read_text(encoding="utf-8"))
        assert payload["status"] == "PASS"
        assert payload["version_supported"] is True
        assert payload["all_required_imports_ok"] is True
        assert payload["fallback_attempted"] is False
        assert set(payload["required_packages"]) == {
            "numpy",
            "pandas",
            "scipy",
            "sklearn",
            "yaml",
        }
        assert all(
            item["import_ok"] is True
            for item in payload["required_packages"].values()
        )


def test_resolver_rejects_emulated_python_3_9_without_fallback() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fake = root / "old-python"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            "  printf '3.9.18\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 91\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        record = root / "should-not-exist.json"
        environment = os.environ.copy()
        environment["LVEF_SCC_PYTHON"] = str(fake)
        completed = subprocess.run(
            [str(RESOLVER), "--record-json", str(record)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 65
        assert completed.stdout == ""
        assert "python_3_10_or_newer_required_no_fallback_attempted" in completed.stderr
        assert not record.exists()


def test_failing_audit_subprocess_preserves_parent_and_exposes_status() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_dir = root / "run"
        aggregate = run_dir / "aggregate"
        restricted = run_dir / "restricted"
        aggregate.mkdir(parents=True)
        restricted.mkdir()
        earlier = aggregate / "earlier_safe_output.json"
        earlier.write_text('{"status":"PASS"}\n', encoding="utf-8")
        document = root / "commands.md"
        document.write_text(
            "<!-- lvef-scc-block:synthetic-failure -->\n"
            "```bash\n"
            "printf 'RESTRICTED_BODY_STDOUT\\n'\n"
            "printf 'RESTRICTED_BODY_STDERR\\n' >&2\n"
            "exit 7\n"
            "```\n",
            encoding="utf-8",
        )
        parent = (
            "set -euo pipefail\n"
            "printf 'parent_before=alive\\n'\n"
            f"if {shlex.quote(str(BLOCK_RUNNER))} --document {shlex.quote(str(document))} "
            "--block-id synthetic-failure "
            f"--run-dir {shlex.quote(str(run_dir))} --label synthetic_failure "
            "--safe-output aggregate/earlier_safe_output.json; then\n"
            "  child_status=0\n"
            "else\n"
            "  child_status=$?\n"
            "fi\n"
            "printf 'captured_child_status=%s\\n' \"$child_status\"\n"
            "test \"$child_status\" -eq 7\n"
            "printf 'parent_after=alive\\n'\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", parent],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert "parent_before=alive" in completed.stdout
        assert "captured_child_status=7" in completed.stdout
        assert "parent_after=alive" in completed.stdout
        assert f"safe_output_ready={earlier}" in completed.stdout
        assert "RESTRICTED_BODY_STDOUT" not in completed.stdout
        assert "RESTRICTED_BODY_STDERR" not in completed.stderr
        assert "RESTRICTED_BODY_STDOUT" in (
            restricted / "synthetic_failure.stdout.log"
        ).read_text(encoding="utf-8")
        assert "RESTRICTED_BODY_STDERR" in (
            restricted / "synthetic_failure.stderr.log"
        ).read_text(encoding="utf-8")
        status = json.loads(
            (aggregate / "synthetic_failure.runner_status.json").read_text(
                encoding="utf-8"
            )
        )
        assert status["syntax_status"] == 0
        assert status["subprocess_executed"] is True
        assert status["execution_status"] == 7
        assert status["final_status"] == 7
        assert status["safe_outputs_ready"] == 1


def test_runner_syntax_failure_does_not_execute_body() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_dir = root / "run"
        (run_dir / "aggregate").mkdir(parents=True)
        (run_dir / "restricted").mkdir()
        document = root / "bad.md"
        document.write_text(
            "<!-- lvef-scc-block:bad-syntax -->\n"
            "```bash\n"
            "if true; then\n"
            "  printf 'must_not_run\\n'\n"
            "```\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(BLOCK_RUNNER),
                "--document",
                str(document),
                "--block-id",
                "bad-syntax",
                "--run-dir",
                str(run_dir),
                "--label",
                "bad_syntax",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        status = json.loads(
            (run_dir / "aggregate" / "bad_syntax.runner_status.json").read_text(
                encoding="utf-8"
            )
        )
        assert status["syntax_status"] != 0
        assert status["subprocess_executed"] is False
        assert (run_dir / "restricted" / "bad_syntax.stdout.log").read_text() == ""


def test_runner_uses_explicit_allowlisted_environment_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_dir = root / "run"
        (run_dir / "aggregate").mkdir(parents=True)
        (run_dir / "restricted").mkdir()
        document = root / "environment.md"
        document.write_text(
            "<!-- lvef-scc-block:environment-check -->\n"
            "```bash\n"
            "test -z \"${UNRELATED_PARENT_STATE:-}\"\n"
            "test \"$LVEF_PHASE1D_EXPECTED_COMMIT\" = abc123\n"
            "```\n",
            encoding="utf-8",
        )
        env_file = root / "runner.env"
        env_file.write_text("LVEF_PHASE1D_EXPECTED_COMMIT=abc123\n", encoding="utf-8")
        environment = os.environ.copy()
        environment["UNRELATED_PARENT_STATE"] = "must_not_leak"
        completed = subprocess.run(
            [
                str(BLOCK_RUNNER),
                "--document",
                str(document),
                "--block-id",
                "environment-check",
                "--run-dir",
                str(run_dir),
                "--label",
                "environment_check",
                "--env-file",
                str(env_file),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
