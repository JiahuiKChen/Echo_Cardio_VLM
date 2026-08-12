bash -p -c '
set -euo pipefail
umask 077

WT=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
OLD=6a814b3080f1159facf86ee60895117d187a41b7
NEW=2088832bcdb599f5f56ec12d9d6168330bbbf7b7
BASE=23c74ccfd145ab9a423b6942a431a1894a34ab67
PY=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
DIAG_ROOT="$1"
PRIOR="$2"
CRC="$3"
AUDITS=/restricted/projectnb/mimicecho/audits
ATTEMPT4="$AUDITS/lvef_multitask_phase1ef_post_reallocation_lock_attempt_004"
CAPACITY_RECEIPT="$ATTEMPT4/restricted/capacity/post_reallocation_capacity.restricted.json"
CAPACITY_AGGREGATE="$ATTEMPT4/aggregate/lvef_c3_post_reallocation_capacity.summary.json"
OUT="$DIAG_ROOT/current_environment_2088832bcdb599f5f56ec12d9d6168330bbbf7b7.restricted.json"

sha256_value() {
  local digest remainder
  read -r digest remainder < <(/usr/bin/sha256sum "$1")
  printf "%s\n" "$digest"
}

require_clean_checkout() {
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ""|"?? .DS_Store"|"?? docs/.DS_Store") ;;
      *) return 1 ;;
    esac
  done < <(/usr/bin/git -C "$WT" status --porcelain=v1 --untracked-files=all)
}

test -n "$DIAG_ROOT"
test -n "$PRIOR"
test -n "$CRC"
test -d "$DIAG_ROOT"
test ! -L "$DIAG_ROOT"
test -O "$DIAG_ROOT"
case "$(/usr/bin/stat -c %a "$DIAG_ROOT")" in
  700|2700) ;;
  *) exit 65 ;;
esac
test -e "$PY"
test -f "$PRIOR"
test ! -L "$PRIOR"
test -f "$CRC"
test ! -L "$CRC"
test ! -e "$OUT"
test ! -L "$OUT"

test "$(/usr/bin/git -C "$WT" branch --show-current)" = codex/lvef-multitask-revalidation
CURRENT="$("/usr/bin/git" -C "$WT" rev-parse HEAD)"
case "$CURRENT" in
  "$OLD"|"$NEW") ;;
  *) exit 65 ;;
esac
require_clean_checkout
/usr/bin/git -C "$WT" merge-base --is-ancestor "$BASE" "$CURRENT"

for suffix in 001 002 003 004; do
  test -d "$AUDITS/lvef_multitask_phase1ef_post_reallocation_lock_attempt_$suffix"
done
test ! -e "$AUDITS/lvef_multitask_phase1ef_post_reallocation_lock_attempt_005"
test ! -L "$AUDITS/lvef_multitask_phase1ef_post_reallocation_lock_attempt_005"
test ! -e /restricted/projectnb/mimicecho/lvef_multitask_c3_v2/attempts/lvef_c3_phase1ee_production_lock_006
test ! -L /restricted/projectnb/mimicecho/lvef_multitask_c3_v2/attempts/lvef_c3_phase1ee_production_lock_006

test "$(/usr/bin/stat -c %s "$CAPACITY_RECEIPT")" = 10907
test "$(sha256_value "$CAPACITY_RECEIPT")" = b3bae07dcd6958b7cdfdd827a0de565ae0972753e04955fd03cd137b2e30ab62
test "$(/usr/bin/stat -c %s "$CAPACITY_AGGREGATE")" = 5399
test "$(sha256_value "$CAPACITY_AGGREGATE")" = 4d15b0a1a80188ce95d31659eca50cf18c8b6a1ebdef4022a56975f6e3e4bd20

/usr/bin/git -C "$WT" fetch origin --prune
test "$(/usr/bin/git -C "$WT" rev-parse origin/codex/lvef-multitask-revalidation)" = "$NEW"
/usr/bin/git -C "$WT" merge-base --is-ancestor "$CURRENT" "$NEW"
if [[ "$CURRENT" != "$NEW" ]]; then
  /usr/bin/git -C "$WT" merge --ff-only origin/codex/lvef-multitask-revalidation
fi

test "$(/usr/bin/git -C "$WT" rev-parse HEAD)" = "$NEW"
require_clean_checkout
test "$(sha256_value "$WT/scripts/capture_lvef_c3_production_environment.py")" = b52edf8230e43b386810327dae658c4bac4014896acc52956235d556ce50e179
test "$(sha256_value "$WT/scripts/scc_execute_lvef_c3_phase1ef_attempt.sh")" = 2449e021f40be5f67a5a74159857992d71bfb24239ab5a1bf337c4090147c2b3
test "$(sha256_value "$WT/scripts/scc_prepare_lvef_c3_phase1ef_environment.sh")" = b94c913051e74e77f04d67b7ca12a7dcfed5ee6b642c17c9edda2a8a67d6d408
test "$(sha256_value "$WT/scripts/lvef_c3_phase1ef_authority_manifest.py")" = 9c165edf2f1364e4d89e76f28131a13f77bfd6e4d5acc592bddd7205af819323
test "$(sha256_value "$WT/scripts/scc_capture_lvef_c3_post_reallocation_capacity.sh")" = feaebdc13dbf79fb27b07def48c0d43ae7866445fce0c205f3f0b0a3306f82da

set +e
CAPTURE_SAFE="$("$PY" "$WT/scripts/capture_lvef_c3_production_environment.py" \
  --prior-environment "$PRIOR" \
  --governing-commit "$NEW" \
  --checkout-root "$WT" \
  --crc32c-python "$CRC" \
  --crc32c-python-expected-sha256 52a2a75599d1bbbd1f5705af946fc3ffbd68b5430adcda0dea2d0a00b33fd1b5 \
  --crc32c-worker "$WT/scripts/lvef_c3_crc32c_worker.py" \
  --output "$OUT")"
CAPTURE_STATUS=$?
set -e
if [[ "$CAPTURE_STATUS" -ne 0 ]]; then
  printf "%s\n" "$CAPTURE_SAFE"
  exit "$CAPTURE_STATUS"
fi

test -f "$OUT"
test ! -L "$OUT"
test -O "$OUT"
test "$(/usr/bin/stat -c %a "$OUT")" = 600
"$PY" -I -c "import json,sys; v=json.load(open(sys.argv[1],encoding=\"utf-8\")); assert v[\"governing_commit\"]==sys.argv[2]; assert v[\"status\"]==\"PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION\"; assert v[\"gpu_execution_performed\"] is False; assert v[\"cloud_request_performed\"] is False; assert v[\"dicom_body_read\"] is False; assert v[\"model_fitted\"] is False; assert v[\"prediction_generated\"] is False; assert v[\"confirmatory_performance_accessed\"] is False" "$OUT" "$NEW"

test "$(/usr/bin/stat -c %s "$CAPACITY_RECEIPT")" = 10907
test "$(sha256_value "$CAPACITY_RECEIPT")" = b3bae07dcd6958b7cdfdd827a0de565ae0972753e04955fd03cd137b2e30ab62
test "$(/usr/bin/stat -c %s "$CAPACITY_AGGREGATE")" = 5399
test "$(sha256_value "$CAPACITY_AGGREGATE")" = 4d15b0a1a80188ce95d31659eca50cf18c8b6a1ebdef4022a56975f6e3e4bd20
test ! -e "$AUDITS/lvef_multitask_phase1ef_post_reallocation_lock_attempt_005"
test ! -e /restricted/projectnb/mimicecho/lvef_multitask_c3_v2/attempts/lvef_c3_phase1ee_production_lock_006

printf "%s\n" "SCC_D3_FAST_FORWARD=PASS"
printf "%s\n" "CURRENT_ENVIRONMENT_POSTCOMMIT_VALIDATION=PASS"
printf "%s\n" "ATTEMPT_004_CAPACITY_ARTIFACTS_PRESERVED=YES"
printf "%s\n" "ATTEMPT_004_RERUN=NO"
printf "%s\n" "FRESH_LOGICAL_ATTEMPT_EXECUTED=NO"
printf "%s\n" "CLOUD_REQUESTS=0"
printf "%s\n" "QSUB_SUBMISSIONS=0"
' phase1ef-d3 \
  "${PHASE1EFD3_DIAG_ROOT-}" \
  "${PRIOR_ENVIRONMENT_RECEIPT-}" \
  "${CRC32C_PYTHON-}"
