from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_environment_preparer_is_offline_private_and_hash_bound() -> None:
    source = (
        ROOT / "scripts" / "scc_prepare_lvef_c3_phase1ef_environment.sh"
    ).read_text(encoding="utf-8")
    assert "set -euo pipefail" in source
    assert "umask 077" in source
    assert 'ln -- "$temporary" "$OUTPUT_ENV"' in source
    assert 'unlink "$temporary"' in source
    assert not any(
        line.lstrip().startswith("mv -n") for line in source.splitlines()
    )
    assert "stat -c '%a'" in source
    assert "assert_no_symlink_ancestors" in source
    assert "assert_private_directory" in source
    assert 'OUTPUT_ENV_PARENT="${OUTPUT_ENV%/*}"' in source
    assert 'assert_private_directory "$OUTPUT_ENV_PARENT"' in source
    assert 'assert_no_symlink_ancestors "$OUTPUT_ENV"' in source
    assert "sha256sum" in source
    assert "packet_binding" in source
    assert "export -n LVEF_C3_GCP_BILLING_PROJECT" in source
    assert "PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE=7492" in source
    assert "PRIOR_SAFE_12_EXPECTED_SHA" in source
    assert "MIGRATION_WITNESS_EXPECTED_SIZE" in source
    assert source.index('sha256sum -- "$PYTHON_AUTHORITY"') < source.index(
        "packet_binding()"
    )
    assert source.index('stat -c \'%s\' -- "$PRIOR_PRODUCTION_PACKET"') < source.index(
        "packet_binding()"
    )
    assert "git -C \"$WORKTREE\" merge-base --is-ancestor" in source
    assert "LVEF_C3_GCP_BILLING_PROJECT=%q" not in source
    assert "printf '%s=%q\\n'" in source
    for forbidden in (
        "gcloud ", "gsutil", "objects.list", "alt=media", "qsub",
        "pquota", "findmnt", "df -B1", "du -", "rm -", "rm -rf",
    ):
        assert forbidden not in source


def test_environment_preparer_preserves_fixed_scientific_authorities() -> None:
    source = (
        ROOT / "scripts" / "scc_prepare_lvef_c3_phase1ef_environment.sh"
    ).read_text(encoding="utf-8")
    assert "lvef_c3_phase1ee_production_lock_005" in source
    assert "lvef_c3_phase1ee_production_lock_006" in source
    assert "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004" in source
    for prior in ("001", "002", "003"):
        assert (
            "PHASE1EF_PREP_ATTEMPT_ID="
            f"lvef_multitask_phase1ef_post_reallocation_lock_attempt_{prior}"
        ) not in source
    assert "23c74ccfd145ab9a423b6942a431a1894a34ab67" in source
    assert "7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b" in source
    assert "920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1" in source
    assert "35071385477515e40e6cc1503561b7c7aa434127435ce91aa3ae8b0a009188ff" in source
    assert "c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517" in source


def test_environment_preparer_rebinds_current_authority_after_historical_sources() -> None:
    source = (
        ROOT / "scripts" / "scc_prepare_lvef_c3_phase1ef_environment.sh"
    ).read_text(encoding="utf-8")
    second_source = source.index('source "$PRIOR_EXECUTION_ENV"')
    git_check = source.index('git -C "$WORKTREE" rev-parse HEAD')
    publication = source.index('ln -- "$temporary" "$OUTPUT_ENV"')
    rebindings = {
        "EXPECTED_COMMIT": "PHASE1EF_PREP_REQUESTED_COMMIT",
        "OUTPUT_ENV": "PHASE1EF_PREP_REQUESTED_OUTPUT_ENV",
        "WORKTREE": "PHASE1EF_PREP_WORKTREE",
        "SESSION_ENV": "PHASE1EF_PREP_SESSION_ENV",
        "PRIOR_PRODUCTION_ROOT": "PHASE1EF_PREP_PRIOR_PRODUCTION_ROOT",
        "PRIOR_PRODUCTION_ATTEMPT_ROOT": (
            "PHASE1EF_PREP_PRIOR_PRODUCTION_ATTEMPT_ROOT"
        ),
        "PRIOR_EXECUTION_ENV": "PHASE1EF_PREP_PRIOR_EXECUTION_ENV",
        "ATTEMPT_ID": "PHASE1EF_PREP_ATTEMPT_ID",
        "PHASE1EF_ATTEMPT_ROOT": "PHASE1EF_PREP_ATTEMPT_ROOT",
    }
    for generic, sentinel in rebindings.items():
        rebind = source.index(f'{generic}="${sentinel}"', second_source)
        assert second_source < rebind < git_check
    assert source.index(
        'OUTPUT_ENV="$PHASE1EF_PREP_REQUESTED_OUTPUT_ENV"', second_source
    ) < publication
    assert 'PHASE1EF_PREP_SESSION_SHA=' in source
    assert 'PHASE1EF_PREP_EXECUTION_SHA=' in source
    readonly_lines = "\n".join(
        line for line in source.splitlines() if line.startswith("readonly ")
    )
    for sentinel in rebindings.values():
        assert sentinel in readonly_lines
    assert "PHASE1EF_PREP_SESSION_SHA" in readonly_lines
    assert "PHASE1EF_PREP_EXECUTION_SHA" in readonly_lines
    assert '= "$PHASE1EF_PREP_SESSION_SHA"' in source
    assert '= "$PHASE1EF_PREP_EXECUTION_SHA"' in source


def test_offline_runbook_is_strict_no_clobber_and_safe_profile_gated() -> None:
    source = (
        ROOT / "docs" / "lvef_multitask" / "scc_phase1ef_pretransfer_commands.md"
    ).read_text(encoding="utf-8")
    assert "bash <<'PHASE1EF_STRICT_CHILD'" in source
    assert (
        "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004"
        in source
    )
    assert (
        "ATTEMPT_ID='lvef_multitask_phase1ef_post_reallocation_lock_attempt_001'"
        not in source
    )
    assert (
        "ATTEMPT_ID='lvef_multitask_phase1ef_post_reallocation_lock_attempt_002'"
        not in source
    )
    assert (
        "ATTEMPT_ID='lvef_multitask_phase1ef_post_reallocation_lock_attempt_003'"
        not in source
    )
    assert source.count("PHASE1EF_STRICT_CHILD") == 2
    assert "PHASE1EF_PRETRANSFER_WRAPPER_STARTED=YES" in source
    assert ': "${PHASE1EF_ENV:?set the owner-private mode-600 Phase 1E-F environment path}"' in source
    assert "PHASE1EF_ENV='<OWNER_PRIVATE_MODE_600_PHASE1EF_ENV>'" not in source
    assert "export -n PHASE1EF_ENV" in source
    assert "trap phase1ef_exit_marker EXIT" in source
    assert "PHASE1EF_PRETRANSFER_EXIT_STATUS=%s" in source
    assert "PHASE1EF_PRETRANSFER_LAST_STAGE=%s" in source
    assert "phase1ef_output_paths=(" in source
    assert source.index("phase1ef_output_paths=(") < source.index("mkdir -m 700")
    for output in (
        "PHASE1EF_ATTEMPT_ROOT", "BACKUP_CONTAINER", "BACKUP_ROOT",
        "RESTORE_ROOT", "PRODUCTION_ATTEMPT_ROOT", "CAPACITY_RECEIPT",
        "CAPACITY_AGGREGATE", "ENVIRONMENT_RECEIPT", "BACKUP_AGGREGATE",
        "EXECUTION_ENV", "BATCH_PLAN", "AUTHORITY_PACKET",
        "LAUNCH_AUTHORITY", "FUTURE_COMMAND", "BACKUP_MANIFEST",
        "RESTORE_RECEIPT", "PRETRANSFER_LOCK", "FINAL_LOCK",
        "TERMINAL_SEAL_ROOT", "TERMINAL_RESTORE_ROOT",
        "TERMINAL_SEAL_AGGREGATE", "SAFE_OUTPUT_GATE_ROOT",
        "SAFE_OUTPUT_GATE_RECEIPT",
    ):
        assert f'"${output}"' in source.split("phase1ef_output_paths=(", 1)[1].split(")", 1)[0]
    assert (
        'TERMINAL_SEAL_AGGREGATE="$TERMINAL_SEAL_ROOT/'
        'lvef_c3_phase1ef_terminal_recovery_seal.summary.json"'
    ) in source
    assert "validate_lvef_c3_phase1ef_safe_outputs.py" in source
    assert source.index("validate_lvef_c3_phase1ef_safe_outputs.py") < source.index(
        "FULL_C3_STATUS=GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION"
    )
    assert source.count('--artifact "capacity=$CAPACITY_AGGREGATE"') == 1
    assert source.count('--artifact "backup=$BACKUP_AGGREGATE"') == 1
    assert source.count('--artifact "pretransfer=$PRETRANSFER_LOCK"') == 1
    assert source.count('--artifact "final=$FINAL_LOCK"') == 1
    assert source.count('--artifact "terminal=$TERMINAL_SEAL_AGGREGATE"') == 1
    assert (
        '--checkout-root "$WORKTREE" --current-environment "$ENVIRONMENT_RECEIPT" \\\n'
        '  --backup-aggregate "$BACKUP_AGGREGATE"'
    ) in source
    assert "all six other current authorities" in source
    assert (
        "--declaration echoprime_environment=CHECKSUM_ONLY_NO_COPY_REQUIRED"
        in source
    )
    assert "echoprime_environment=PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE" not in source


def test_attempt_004_preparation_and_capture_are_no_clobber() -> None:
    preparer = (
        ROOT / "scripts" / "scc_prepare_lvef_c3_phase1ef_environment.sh"
    ).read_text(encoding="utf-8")
    capture = (
        ROOT / "scripts" / "scc_capture_lvef_c3_post_reallocation_capacity.sh"
    ).read_text(encoding="utf-8")
    assert (
        "PHASE1EF_PREP_ATTEMPT_ID="
        "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004"
    ) in preparer
    assert (
        "[[ \"$ATTEMPT_ID\" = "
        "lvef_multitask_phase1ef_post_reallocation_lock_attempt_004 ]]"
    ) in capture
    assert '--attempt-id "$ATTEMPT_ID"' in capture
    assert "ATTEMPT_ID PHASE1EF_ATTEMPT_ROOT" in preparer
    for prior in ("001", "002", "003"):
        assert (
            "PHASE1EF_PREP_ATTEMPT_ID="
            f"lvef_multitask_phase1ef_post_reallocation_lock_attempt_{prior}"
        ) not in preparer
    assert '[[ ! -e "$PHASE1EF_ATTEMPT_ROOT" && ! -L "$PHASE1EF_ATTEMPT_ROOT" ]]' in preparer
    assert (
        '[[ ! -e "/restricted/project/mimicecho/audits/$ATTEMPT_ID" '
        '&& ! -L "/restricted/project/mimicecho/audits/$ATTEMPT_ID" ]]'
    ) in preparer
    assert (
        '[[ ! -e "$PRODUCTION_ROOT/attempts/lvef_c3_phase1ee_production_lock_006" '
        '&& ! -L "$PRODUCTION_ROOT/attempts/lvef_c3_phase1ee_production_lock_006" ]]'
    ) in preparer
