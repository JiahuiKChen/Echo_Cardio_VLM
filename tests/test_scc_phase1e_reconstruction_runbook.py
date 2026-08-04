from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "lvef_multitask" / "scc_phase1e_reconstruction_commands.md"
JOB_SCRIPT = ROOT / "scripts" / "scc_run_lvef_reconstruction_smoke.sh"
ENVIRONMENT_CAPTURE = ROOT / "scripts" / "capture_lvef_reconstruction_environment.py"
PRESERVATION_SCRIPT = ROOT / "scripts" / "preserve_lvef_reconstruction_smoke.py"


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)


def test_phase1e_job_script_and_every_runbook_block_parse_as_bash() -> None:
    subprocess.run(["bash", "-n", str(JOB_SCRIPT)], check=True)
    blocks = _bash_blocks(RUNBOOK.read_text(encoding="utf-8"))
    assert len(blocks) == 5
    for block in blocks:
        subprocess.run(["bash", "-n"], input=block, text=True, check=True)


def test_runbook_locks_git_and_known_restricted_authorities() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    required = {
        "__PHASE1EA_COMMIT__",
        "codex/lvef-multitask-revalidation",
        "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask",
        "/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all",
        "/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/stage_d_500study_scc",
        "/restricted/project/mimicecho/audits/lvef_multitask_phase1d_20260803T234842Z_912250174f99",
        "manifests/all_eligible_studies.csv",
        "manifests/subject_split_map_v1.csv",
        "manifests/selected_records.csv",
        "study_embeddings_512/study_embedding_manifest.csv",
        "duplicate_clip_resolution_v2_restricted.csv",
        "canonical_selected_clip_inventory_restricted.csv",
        "batch_%03d",
        "SHA256SUMS.txt",
        "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b",
        "138642379",
        "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb",
    }
    assert all(value in text for value in required)
    assert text.count("__PHASE1EA_COMMIT__") >= 2
    assert 'git merge --ff-only "origin/$BRANCH"' in text
    assert 'test -z "$(git status --porcelain)"' in text


def test_runbook_uses_committed_pipeline_and_direct_job_submission() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    runner = JOB_SCRIPT.read_text(encoding="utf-8")
    environment_capture = ENVIRONMENT_CAPTURE.read_text(encoding="utf-8")
    preservation = PRESERVATION_SCRIPT.read_text(encoding="utf-8")
    required_scripts = {
        "scripts/build_lvef_reconstruction_source_manifest.py",
        "scripts/download_lvef_reconstruction_smoke.py",
        "scripts/scc_run_lvef_reconstruction_smoke.sh",
        "scripts/capture_lvef_reconstruction_environment.py",
        "scripts/lvef_reconstruction_smoke.py",
        "scripts/audit_lvef_reconstruction_smoke_run.py",
        "scripts/preserve_lvef_reconstruction_smoke.py",
    }
    combined = text + "\n" + runner
    assert all(script in combined for script in required_scripts)
    assert "qsub" in text
    assert "scripts/scc_run_lvef_reconstruction_smoke.sh" in text
    assert "cat >" not in text
    assert "mktemp" not in text
    assert "LVEF_E1A_ENV_FILE" in text
    assert "--historical-study-manifest" in text
    assert "--duplicate-resolution" in text
    assert "--canonical-inventory" in text
    assert "--release-checksums" in text
    assert "--expected-source-manifest-sha256" in text
    assert "EXPECTED_SMOKE_SOURCE_SHA256" in text
    assert "restricted_input_authority_hash_set_exact" in text
    assert "locked_split_counts_match" in text
    assert "pydicom.__version__" in text
    assert "complete installed-distribution inventory" in text
    assert "captured scheduler must be `SGE`" in text
    assert "numeric `scheduler_job_id` must equal the `JOB_ID`" in text
    assert '"package_inventory": installed_package_inventory()' in environment_capture
    assert '"scheduler": "SGE"' in environment_capture
    assert 'if not payload["scheduler_job_id"].isdigit()' in environment_capture
    assert 'environment["scheduler"] != str(args.scheduler)' in preservation
    assert 'str(environment["scheduler_job_id"]) != str(args.job_id)' in preservation
    assert "--scheduler SGE" in runner
    assert '--job-id "$JOB_ID"' in runner


def test_job_is_exactly_four_study_smoke_and_never_fits_or_predicts() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    runner = JOB_SCRIPT.read_text(encoding="utf-8")
    assert 'assert source["smoke_n_studies"] == 4' in text
    assert 'assert source["smoke_n_roles"] == 4' in text
    assert 'assert preflight["n_studies"] == 4' in text
    assert 'assert preflight["n_subjects"] == 4' in text
    assert 'assert preflight["n_expected_objects"] <= 1000' in text
    assert "run_smoke_once run_a" in runner
    assert "run_smoke_once run_b" in runner
    assert runner.count("run_smoke_once run_a") == 1
    assert runner.count("run_smoke_once run_b") == 1
    assert "--device cuda" in runner
    assert "--device auto" not in runner
    assert "--preflight-only" in text
    assert "-l gpus=1" in text
    assert "-l gpu_c=8.0" in text
    assert "-l gpu_memory=48G" in text

    forbidden_scripts = {
        "run_multimodal_fusion.py",
        "run_tabular_measurement_baseline.py",
        "run_multitask_vision_baseline.py",
        "run_multitask_tabular_baseline.py",
        "run_multitask_fusion_baseline.py",
        "run_echoprime_embedding_baseline.py",
        "run_echoprime_embedding_pipeline.sh",
        "run_cloud_echo_cohort.sh",
        "scc_run_fullscale_pipeline.sh",
    }
    assert all(name not in combined for name in forbidden_scripts for combined in [text, runner])


def test_safe_output_block_never_prints_restricted_or_provenance_files() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    blocks = _bash_blocks(text)
    paste_block = blocks[-1]
    cat_lines = [line.strip() for line in paste_block.splitlines() if line.strip().startswith("cat ")]
    assert len(cat_lines) == 17
    for line in cat_lines:
        assert "/restricted/" not in line
        assert "$RUN_ROOT/restricted" not in line
        assert "$RUN_ROOT/provenance" not in line
        assert "PRESERVATION_ROOT" not in line
        assert "$RUN_ROOT/aggregate" in line or "$PRESERVATION_AGGREGATE" in line
    assert 'assert safety["aggregate_safety_gate_passed"] is True' in paste_block
    assert 'assert preservation["all_sha256_match"] is True' in paste_block
    assert 'assert preservation["environment_recorded"] is True' in paste_block
    assert 'assert preservation["job_metadata_recorded"] is True' in paste_block
    assert "git add" not in text
    assert "git commit" not in text
    assert "git push" not in text


def test_no_nonportable_file_enumeration_or_generated_job_body() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "rg --files" not in text
    assert "find $" not in text
    assert not re.search(r"cat\s*>+\s*[^\n]*\.sh", text)
    assert not re.search(r"(?:echo|printf)[^\n]*>+[^\n]*\.sh", text)
