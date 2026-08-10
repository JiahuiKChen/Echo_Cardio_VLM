# SCC Phase 1E-D live-quota and pre-transfer commands

Status: read-only quota/filesystem capture and offline specification lock only. This runbook does not authorize a cloud request, scheduler submission, quota change, file move/deletion, object-body transfer, DICOM processing, extraction, EchoPrime inference, embedding generation, modeling, prediction, or confirmatory-performance access.

Run every block in a strict child Bash process. Replace `__PHASE1ED_IMPLEMENTATION_COMMIT__` only with the reviewed implementation commit after local, origin, and SCC equality is established. The existing Phase 1E-B/C run root and immutable job-7104307/Autoclass outputs remain unchanged. Attempts 001–003 are immutable failed evidence and must not be reused: attempt 001 stopped before capture because its safe SCC setgid-only directory mode was not yet accepted; attempt 002 completed all four authorized read-only commands but its offline validator did not yet recognize SCC's native two-line `pquota` header; and attempt 003 produced a valid live-quota aggregate before the separate offline specification lock rejected the producer's exact `PASS_SUPPLEMENTAL_ADJUDICATION` status because the consumer expected a generic `PASS`. The commands below use the fresh no-clobber attempt 004.

## 1. Bind the existing authorities and create an offline-only attempt

```bash
bash --noprofile --norc <<'PHASE1ED_BIND'
set -euo pipefail
umask 077

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
SESSION_ENV=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env
TARGET_COMMIT=__PHASE1ED_IMPLEMENTATION_COMMIT__
PHASE1ED_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_004

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
mkdir -m 700 -- "$PHASE1ED_ATTEMPT_ROOT/aggregate"
case "$(stat -c '%a' "$PHASE1ED_ATTEMPT_ROOT/aggregate")" in
  700|2700) ;;
  *) exit 2 ;;
esac
printf '%s\n' PHASE1ED_OFFLINE_SPEC_ATTEMPT_READY=YES
printf '%s\n' PHASE1ED_CLOUD_REQUESTS=0
printf '%s\n' PHASE1ED_SCHEDULER_SUBMISSIONS=0
PHASE1ED_BIND
```

Attempt 004 is an offline-only successor. It must not contain or copy the attempt-003 raw quota receipt or aggregate.

## 2. Bind the already validated attempt-003 live-quota aggregate

```bash
bash --noprofile --norc <<'PHASE1ED_QUOTA_BIND'
set -euo pipefail
LIVE_QUOTA=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_003/aggregate/lvef_c3_live_quota.summary.json
EXPECTED_BYTES=2261
EXPECTED_SHA256=e0714eb4260973a118a6eab585ba87e55437bc48410b51ceee50182f43c961b9
test -f "$LIVE_QUOTA"
test -O "$LIVE_QUOTA"
test ! -L "$LIVE_QUOTA"
test "$(stat -c '%a' "$LIVE_QUOTA")" = 600
test "$(stat -c '%s' "$LIVE_QUOTA")" = "$EXPECTED_BYTES"
test "$(sha256sum "$LIVE_QUOTA" | awk '{print $1}')" = "$EXPECTED_SHA256"
printf '%s\n' PHASE1ED_ATTEMPT_003_LIVE_QUOTA_BOUND=YES
printf '%s\n' PHASE1ED_READ_ONLY_QUOTA_COMMANDS_REPEATED=NO
PHASE1ED_QUOTA_BIND
```

Attempt 003 already executed exactly one each of `pquota -u`, `findmnt --json --target`, `df -B1`, and non-enumerating `du -x -s -B1`. Its validator passed and its aggregate records the quota `NO-GO`. Do not repeat those commands merely to co-locate evidence under a newer commit.

## 3. Build the offline production specification lock

This step re-hashes the two immutable aggregate roots and actual authority files. It proves their file identities only. It does not establish production semantic authority; the lock retains that limitation and the absent production downloader/batch/finalizer as explicit blockers.

```bash
bash --noprofile --norc <<'PHASE1ED_SPEC_LOCK'
set -euo pipefail
umask 077
WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
SESSION_ENV=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env
TARGET_COMMIT=__PHASE1ED_IMPLEMENTATION_COMMIT__
PHASE1ED_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_004
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

Keep both outputs under their separate immutable restricted attempt roots during this phase. Review only their self-validated aggregate fields, byte sizes, and SHA-256 values; never paste raw command output or the restricted receipt.

```bash
bash --noprofile --norc <<'PHASE1ED_REVIEW'
set -euo pipefail
LIVE_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_003
SPEC_ATTEMPT_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ed_live_pretransfer_attempt_004
for artifact in \
  "$LIVE_ATTEMPT_ROOT/aggregate/lvef_c3_live_quota.summary.json" \
  "$SPEC_ATTEMPT_ROOT/aggregate/lvef_c3_production_pretransfer_lock.summary.json"; do
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
printf '%s\n' READ_ONLY_QUOTA_COMMANDS_REPEATED=NO
printf '%s\n' CLOUD_REQUESTS=0
printf '%s\n' DICOM_BODIES_DOWNLOADED=NO
printf '%s\n' FULL_C3_STATUS=NO_GO
PHASE1ED_REVIEW
```
