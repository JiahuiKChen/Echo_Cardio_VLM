# SCC Phase 1E-D live-quota and pre-transfer commands

Status: read-only quota/filesystem capture and offline specification lock only. This runbook does not authorize a cloud request, scheduler submission, quota change, file move/deletion, object-body transfer, DICOM processing, extraction, EchoPrime inference, embedding generation, modeling, prediction, or confirmatory-performance access.

Run every block in a strict child Bash process. Replace `__PHASE1ED_IMPLEMENTATION_COMMIT__` only with the reviewed implementation commit after local, origin, and SCC equality is established. The existing Phase 1E-B/C run root and immutable job-7104307/Autoclass outputs remain unchanged. Attempts 001 and 002 are immutable failed evidence and must not be reused: attempt 001 stopped before capture because its safe SCC setgid-only directory mode was not yet accepted, while attempt 002 completed all four authorized read-only commands but its offline validator did not yet recognize SCC's native two-line `pquota` header. The commands below use the fresh no-clobber attempt 003.

## 1. Bind the existing owner-private authorities

```bash
bash --noprofile --norc <<'PHASE1ED_BIND'
set -euo pipefail
umask 077

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
SESSION_ENV=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env
TARGET_COMMIT=__PHASE1ED_IMPLEMENTATION_COMMIT__
PHASE1ED_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_003

test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test ! -L "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = 600
source "$SESSION_ENV"
EXPECTED_COMMIT="$TARGET_COMMIT"

test "$(git -C "$WORKTREE" branch --show-current)" = codex/lvef-multitask-revalidation
test "$(git -C "$WORKTREE" rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git -C "$WORKTREE" status --porcelain --untracked-files=no)"
test ! -e "$PHASE1ED_ATTEMPT_ROOT"
mkdir -m 700 -- "$PHASE1ED_ATTEMPT_ROOT"
test -d "$PHASE1ED_ATTEMPT_ROOT"
test -O "$PHASE1ED_ATTEMPT_ROOT"
test ! -L "$PHASE1ED_ATTEMPT_ROOT"
case "$(stat -c '%a' "$PHASE1ED_ATTEMPT_ROOT")" in
  700|2700) ;;
  *) exit 2 ;;
esac
mkdir -m 700 -- "$PHASE1ED_ATTEMPT_ROOT/restricted"
case "$(stat -c '%a' "$PHASE1ED_ATTEMPT_ROOT/restricted")" in
  700|2700) ;;
  *) exit 2 ;;
esac

CAPTURE_ENV="$PHASE1ED_ATTEMPT_ROOT/restricted/phase1ed_live_quota_capture.env"
test ! -e "$CAPTURE_ENV"
install -m 600 /dev/null "$CAPTURE_ENV"
{
  printf 'WORKTREE=%q\n' "$WORKTREE"
  printf 'EXPECTED_COMMIT=%q\n' "$EXPECTED_COMMIT"
  printf 'PYTHON=%q\n' "$PYTHON"
  printf 'EXPECTED_PYTHON_SHA256=%q\n' "$EXPECTED_PYTHON_SHA256"
  printf 'PHASE1ED_ATTEMPT_ROOT=%q\n' "$PHASE1ED_ATTEMPT_ROOT"
  printf 'LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT=%q\n' /restricted/projectnb/mimicecho
  printf 'LVEF_C3_LIVE_QUOTA_PRINCIPAL=%q\n' mimicecho
  printf 'MIGRATION_WITNESS=%q\n' "$MIGRATION_WITNESS"
  printf 'EXPECTED_MIGRATION_WITNESS_SHA256=%q\n' "$EXPECTED_MIGRATION_WITNESS_SHA256"
  printf 'MIGRATION_CLASSIFICATION=%q\n' "$MIGRATION_CLASSIFICATION"
  printf 'EXPECTED_MIGRATION_CLASSIFICATION_SHA256=%q\n' "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256"
} >"$CAPTURE_ENV"
chmod 600 "$CAPTURE_ENV"

printf '%s\n' PHASE1ED_CAPTURE_ENV_READY=YES
printf '%s\n' PHASE1ED_CLOUD_REQUESTS=0
printf '%s\n' PHASE1ED_SCHEDULER_SUBMISSIONS=0
PHASE1ED_BIND
```

The capture environment contains SCC paths and the quota principal, so it remains owner-private and must not enter Git or an ordinary response.

## 2. Capture and validate the live quota evidence

```bash
bash --noprofile --norc <<'PHASE1ED_CAPTURE'
set -euo pipefail
WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
PHASE1ED_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_003
CAPTURE_ENV="$PHASE1ED_ATTEMPT_ROOT/restricted/phase1ed_live_quota_capture.env"

"$WORKTREE/scripts/scc_capture_lvef_c3_live_quota.sh" \
  --capture-env "$CAPTURE_ENV"
PHASE1ED_CAPTURE
```

The wrapper executes exactly `pquota -u`, `findmnt --json --target`, `df -B1`, and `du -x -s -B1`. It writes raw output only below the owner-private attempt root, binds the exact command/tool/file identities, and treats a valid but insufficient quota as an operationally successful capture with a scientific `NO-GO`. It never runs `pquota -v`.

## 3. Build the offline production specification lock

This step re-hashes the two immutable aggregate roots and actual authority files. It proves their file identities only. It does not establish production semantic authority; the lock retains that limitation and the absent production downloader/batch/finalizer as explicit blockers.

```bash
bash --noprofile --norc <<'PHASE1ED_SPEC_LOCK'
set -euo pipefail
umask 077
WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
SESSION_ENV=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env
TARGET_COMMIT=__PHASE1ED_IMPLEMENTATION_COMMIT__
PHASE1ED_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_003
CHECKPOINT=/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt

test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test ! -L "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = 600
source "$SESSION_ENV"
EXPECTED_COMMIT="$TARGET_COMMIT"
test "$(git -C "$WORKTREE" rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git -C "$WORKTREE" status --porcelain --untracked-files=no)"

ORIGINAL_AGGREGATE_ROOT="$RUN_ROOT/aggregate"
SUPPLEMENTAL_AGGREGATE_ROOT="$RUN_ROOT/autoclass_adjudication/phase1ebc_autoclass_adjudication_attempt_002/aggregate"
ENVIRONMENT_RECEIPT="$SMOKE_AUTH_ROOT/provenance/environment.json"
COMMAND_CONFIG_AUTHORITY="$SMOKE_AUTH_ROOT/provenance/scc_run_lvef_reconstruction_smoke.sh"
OUTPUT="$PHASE1ED_ATTEMPT_ROOT/aggregate/lvef_c3_production_pretransfer_lock.summary.json"

test -f "$ENVIRONMENT_RECEIPT"
test -f "$COMMAND_CONFIG_AUTHORITY"
test -f "$CHECKPOINT"
test ! -e "$OUTPUT"

"$PYTHON" "$WORKTREE/scripts/lock_lvef_c3_production_orchestration.py" \
  --contract "$WORKTREE/configs/lvef_c3_execution_contract.yaml" \
  --original-aggregate-root "$ORIGINAL_AGGREGATE_ROOT" \
  --supplemental-aggregate-root "$SUPPLEMENTAL_AGGREGATE_ROOT" \
  --checkout-root "$WORKTREE" \
  --governing-commit "$EXPECTED_COMMIT" \
  --selected-cohort-manifest "$SELECTED_STUDIES" \
  --split-manifest "$SPLIT_MAP" \
  --selected-source-manifest "$SELECTED_SOURCE_MANIFEST" \
  --environment-receipt "$ENVIRONMENT_RECEIPT" \
  --command-config-manifest "$COMMAND_CONFIG_AUTHORITY" \
  --checkpoint "$CHECKPOINT" \
  --restricted-output-root "$PHASE1ED_ATTEMPT_ROOT" \
  --output "$OUTPUT"
PHASE1ED_SPEC_LOCK
```

The Phase 1E-A environment and command copy are hash-frozen canary evidence, not a production environment/command manifest. The output must therefore remain `PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED`, `execution_authorized=false`, and `full_c3_status=NO_GO`.

## 4. Aggregate-safe review

Keep both outputs under the restricted attempt root during this phase. Review only their self-validated aggregate fields, byte sizes, and SHA-256 values; never paste raw command output or the restricted receipt.

```bash
bash --noprofile --norc <<'PHASE1ED_REVIEW'
set -euo pipefail
PHASE1ED_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_003
for artifact in \
  "$PHASE1ED_ATTEMPT_ROOT/aggregate/lvef_c3_live_quota.summary.json" \
  "$PHASE1ED_ATTEMPT_ROOT/aggregate/lvef_c3_production_pretransfer_lock.summary.json"; do
  test -f "$artifact"
  test -O "$artifact"
  test ! -L "$artifact"
  test "$(stat -c '%a' "$artifact")" = 600
  printf '%s\t%s\t%s\n' \
    "$(basename "$artifact")" \
    "$(stat -c '%s' "$artifact")" \
    "$(sha256sum "$artifact" | awk '{print $1}')"
done
printf '%s\n' OBJECT_LISTING_REPEATED=NO
printf '%s\n' STORAGE_AUDIT_REPEATED=NO
printf '%s\n' CLOUD_REQUESTS=0
printf '%s\n' DICOM_BODIES_DOWNLOADED=NO
printf '%s\n' FULL_C3_STATUS=NO_GO
PHASE1ED_REVIEW
```
