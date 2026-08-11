#!/usr/bin/env bash
# Prepare the owner-private Phase 1E-F input environment without cloud access.
set -euo pipefail
umask 077

[[ $# -eq 2 ]] || {
  printf '%s\n' 'usage: scc_prepare_lvef_c3_phase1ef_environment.sh COMMIT OUTPUT_ENV' >&2
  exit 64
}
PHASE1EF_PREP_REQUESTED_COMMIT="$1"
PHASE1EF_PREP_REQUESTED_OUTPUT_ENV="$2"
[[ "$PHASE1EF_PREP_REQUESTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 65

assert_no_symlink_ancestors() {
  local candidate="$1" cursor=/ component
  local -a components=()
  [[ "$candidate" = /* ]] || return 1
  IFS=/ read -r -a components <<<"${candidate#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    cursor="${cursor%/}/$component"
    [[ ! -L "$cursor" ]] || return 1
  done
}

assert_private_directory() {
  local candidate="$1" mode
  assert_no_symlink_ancestors "$candidate"
  [[ -d "$candidate" && ! -L "$candidate" && -O "$candidate" ]]
  mode="$(stat -c '%a' -- "$candidate")"
  [[ "$mode" = 700 || "$mode" = 2700 ]]
}

PHASE1EF_PREP_WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
PHASE1EF_PREP_SESSION_ENV=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env
PHASE1EF_PREP_PRIOR_PRODUCTION_ROOT=/restricted/projectnb/mimicecho/lvef_multitask_c3_v2
PHASE1EF_PREP_PRIOR_PRODUCTION_ATTEMPT_ROOT="$PHASE1EF_PREP_PRIOR_PRODUCTION_ROOT/attempts/lvef_c3_phase1ee_production_lock_005"
PHASE1EF_PREP_PRIOR_EXECUTION_ENV="$PHASE1EF_PREP_PRIOR_PRODUCTION_ATTEMPT_ROOT/authority/c3_execution_environment.restricted.env"
PHASE1EF_PREP_ATTEMPT_ID=lvef_multitask_phase1ef_post_reallocation_lock_attempt_003
PHASE1EF_PREP_ATTEMPT_ROOT="/restricted/projectnb/mimicecho/audits/$PHASE1EF_PREP_ATTEMPT_ID"
readonly PHASE1EF_PREP_REQUESTED_COMMIT PHASE1EF_PREP_REQUESTED_OUTPUT_ENV
readonly PHASE1EF_PREP_WORKTREE PHASE1EF_PREP_SESSION_ENV
readonly PHASE1EF_PREP_PRIOR_PRODUCTION_ROOT
readonly PHASE1EF_PREP_PRIOR_PRODUCTION_ATTEMPT_ROOT
readonly PHASE1EF_PREP_PRIOR_EXECUTION_ENV PHASE1EF_PREP_ATTEMPT_ID
readonly PHASE1EF_PREP_ATTEMPT_ROOT

# The two sourced owner-private authority files are historical and may contain
# generic names such as EXPECTED_COMMIT.  Keep the requested current authority
# in collision-resistant variables, then deliberately rebind the downstream
# generic names after sourcing.
OUTPUT_ENV="$PHASE1EF_PREP_REQUESTED_OUTPUT_ENV"
WORKTREE="$PHASE1EF_PREP_WORKTREE"
SESSION_ENV="$PHASE1EF_PREP_SESSION_ENV"
PRIOR_PRODUCTION_ROOT="$PHASE1EF_PREP_PRIOR_PRODUCTION_ROOT"
PRIOR_PRODUCTION_ATTEMPT_ROOT="$PHASE1EF_PREP_PRIOR_PRODUCTION_ATTEMPT_ROOT"
PRIOR_EXECUTION_ENV="$PHASE1EF_PREP_PRIOR_EXECUTION_ENV"
ATTEMPT_ID="$PHASE1EF_PREP_ATTEMPT_ID"
PHASE1EF_ATTEMPT_ROOT="$PHASE1EF_PREP_ATTEMPT_ROOT"

[[ "$OUTPUT_ENV" = /restricted/projectnb/mimicecho/audits/* ]]
[[ ! -e "$OUTPUT_ENV" && ! -L "$OUTPUT_ENV" ]]
OUTPUT_ENV_PARENT="${OUTPUT_ENV%/*}"
assert_private_directory "$OUTPUT_ENV_PARENT"
assert_no_symlink_ancestors "$OUTPUT_ENV"
[[ -f "$SESSION_ENV" && ! -L "$SESSION_ENV" && -O "$SESSION_ENV" ]]
[[ -f "$PRIOR_EXECUTION_ENV" && ! -L "$PRIOR_EXECUTION_ENV" && -O "$PRIOR_EXECUTION_ENV" ]]
[[ "$(stat -c '%a' -- "$SESSION_ENV")" = 600 ]]
[[ "$(stat -c '%a' -- "$PRIOR_EXECUTION_ENV")" = 600 ]]

PHASE1EF_PREP_SESSION_SHA="$(sha256sum -- "$SESSION_ENV" | awk '{print $1}')"
PHASE1EF_PREP_EXECUTION_SHA="$(sha256sum -- "$PRIOR_EXECUTION_ENV" | awk '{print $1}')"
readonly PHASE1EF_PREP_SESSION_SHA PHASE1EF_PREP_EXECUTION_SHA
# shellcheck disable=SC1090
source "$SESSION_ENV"
# shellcheck disable=SC1090
source "$PRIOR_EXECUTION_ENV"
# The owner-private value remains a shell variable for the offline control
# plane but must never be inherited by authority/hash/git helper processes.
export -n LVEF_C3_GCP_BILLING_PROJECT

# Historical authority files may legitimately bind their own generic commit,
# worktree, or output names.  They must not replace this invocation's current
# commit or no-clobber destination.
EXPECTED_COMMIT="$PHASE1EF_PREP_REQUESTED_COMMIT"
OUTPUT_ENV="$PHASE1EF_PREP_REQUESTED_OUTPUT_ENV"
WORKTREE="$PHASE1EF_PREP_WORKTREE"
SESSION_ENV="$PHASE1EF_PREP_SESSION_ENV"
PRIOR_PRODUCTION_ROOT="$PHASE1EF_PREP_PRIOR_PRODUCTION_ROOT"
PRIOR_PRODUCTION_ATTEMPT_ROOT="$PHASE1EF_PREP_PRIOR_PRODUCTION_ATTEMPT_ROOT"
PRIOR_EXECUTION_ENV="$PHASE1EF_PREP_PRIOR_EXECUTION_ENV"
ATTEMPT_ID="$PHASE1EF_PREP_ATTEMPT_ID"
PHASE1EF_ATTEMPT_ROOT="$PHASE1EF_PREP_ATTEMPT_ROOT"
[[ "$(sha256sum -- "$SESSION_ENV" | awk '{print $1}')" = "$PHASE1EF_PREP_SESSION_SHA" ]]
[[ "$(sha256sum -- "$PRIOR_EXECUTION_ENV" | awk '{print $1}')" = "$PHASE1EF_PREP_EXECUTION_SHA" ]]

[[ "$(git -C "$WORKTREE" branch --show-current)" = codex/lvef-multitask-revalidation ]]
[[ "$(git -C "$WORKTREE" rev-parse HEAD)" = "$EXPECTED_COMMIT" ]]
[[ "$(git -C "$WORKTREE" rev-parse origin/codex/lvef-multitask-revalidation)" = "$EXPECTED_COMMIT" ]]
[[ -z "$(git -C "$WORKTREE" status --porcelain --untracked-files=no)" ]]
git -C "$WORKTREE" merge-base --is-ancestor \
  23c74ccfd145ab9a423b6942a431a1894a34ab67 "$EXPECTED_COMMIT"

PYTHON="$LVEF_C3_PYTHON"
PYTHON_AUTHORITY="$(readlink -f -- "$PYTHON")"
CRC32C_PYTHON="$LVEF_C3_CRC32C_PYTHON"
GCLOUD="$LVEF_C3_GCLOUD_BINARY"
GCLOUD_RECEIPT="$LVEF_C3_GCLOUD_RESOLUTION_RECEIPT"
CLOUDSDK_CONFIG="$LVEF_C3_CLOUDSDK_CONFIG"
PRODUCTION_ROOT="$LVEF_C3_PRODUCTION_ROOT"
CHECKPOINT="$LVEF_C3_CHECKPOINT"
PRIOR_ENVIRONMENT_RECEIPT="$LVEF_C3_ENVIRONMENT_RECEIPT"
PRIOR_PRODUCTION_PACKET="$PRIOR_PRODUCTION_ATTEMPT_ROOT/authority/lvef_c3_production_authority_packet.restricted.json"
PRIOR_PRODUCTION_BATCH_PLAN="$PRIOR_PRODUCTION_ATTEMPT_ROOT/authority/batch_plan.restricted.json"
ORIGINAL_AGGREGATE_ROOT="$RUN_ROOT/aggregate"
SUPPLEMENTAL_AGGREGATE_ROOT="$RUN_ROOT/autoclass_adjudication/phase1ebc_autoclass_adjudication_attempt_002/aggregate"
SELECTED_SOURCE="$SELECTED_SOURCE_MANIFEST"
SOURCE_METADATA="$RUN_ROOT/restricted/source_preflight/c3_full_source_object_metadata.restricted.jsonl"
PRIOR_CAPACITY_PARENT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ee_post_expansion_capacity_attempt_001/aggregate/lvef_c3_live_quota.summary.json
PRIOR_CAPACITY_COMPOSITE=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ee_post_expansion_capacity_attempt_002/aggregate/lvef_c3_post_expansion_capacity.summary.json

PRIOR_SAFE_01="$ORIGINAL_AGGREGATE_ROOT/scc_storage_inventory.summary.json"
PRIOR_SAFE_02="$ORIGINAL_AGGREGATE_ROOT/c3_full_source_preflight.summary.json"
PRIOR_SAFE_03="$ORIGINAL_AGGREGATE_ROOT/c3_full_source_preflight_by_batch.csv"
PRIOR_SAFE_04="$ORIGINAL_AGGREGATE_ROOT/c3_full_source_cost_estimate.json"
PRIOR_SAFE_05="$ORIGINAL_AGGREGATE_ROOT/c3_full_source_preflight_safety_gate.json"
PRIOR_SAFE_06="$ORIGINAL_AGGREGATE_ROOT/c3_full_resource_plan.json"
PRIOR_SAFE_07="$SUPPLEMENTAL_AGGREGATE_ROOT/c3_autoclass_adjudication.summary.json"
PRIOR_SAFE_08="$SUPPLEMENTAL_AGGREGATE_ROOT/c3_source_inventory_authority_adjudication.summary.json"
PRIOR_SAFE_09="$SUPPLEMENTAL_AGGREGATE_ROOT/c3_cost_authority_adjudication.summary.json"
PRIOR_SAFE_10="$SUPPLEMENTAL_AGGREGATE_ROOT/c3_autoclass_adjudication_provenance_manifest.json"
PRIOR_SAFE_11="$SUPPLEMENTAL_AGGREGATE_ROOT/c3_autoclass_adjudication_safety_gate.json"
PRIOR_SAFE_12="$SUPPLEMENTAL_AGGREGATE_ROOT/c3_autoclass_combined_validation.summary.json"

# Verify the interpreter and immutable packet bytes before using either to
# parse subordinate authorities.  This prevents an unverified executable or
# packet from defining the identities that the rest of this preflight trusts.
[[ -f "$PYTHON_AUTHORITY" && ! -L "$PYTHON_AUTHORITY" ]]
[[ "$(sha256sum -- "$PYTHON_AUTHORITY" | awk '{print $1}')" = 1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb ]]
[[ -f "$CRC32C_PYTHON" && ! -L "$CRC32C_PYTHON" ]]
[[ "$(sha256sum -- "$CRC32C_PYTHON" | awk '{print $1}')" = 52a2a75599d1bbbd1f5705af946fc3ffbd68b5430adcda0dea2d0a00b33fd1b5 ]]
[[ -f "$PRIOR_PRODUCTION_PACKET" && ! -L "$PRIOR_PRODUCTION_PACKET" ]]
[[ "$(stat -c '%s' -- "$PRIOR_PRODUCTION_PACKET")" = 7492 ]]
[[ "$(sha256sum -- "$PRIOR_PRODUCTION_PACKET" | awk '{print $1}')" = 2725570d1137640e0c00ae790f1ae3583d63b17c7f957e86e886892dd0e6ba07 ]]

required_vars=(
  PYTHON PYTHON_AUTHORITY CRC32C_PYTHON GCLOUD GCLOUD_RECEIPT
  CLOUDSDK_CONFIG PRODUCTION_ROOT CHECKPOINT PRIOR_ENVIRONMENT_RECEIPT
  SELECTED_STUDIES SELECTED_SOURCE SOURCE_METADATA SPLIT_MAP
  MIGRATION_WITNESS MIGRATION_CLASSIFICATION
  EXPECTED_MIGRATION_WITNESS_SHA256 EXPECTED_MIGRATION_CLASSIFICATION_SHA256
  LVEF_C3_GCP_BILLING_PROJECT
)
for variable in "${required_vars[@]}"; do
  [[ -n "${!variable:-}" ]]
done

# Read expected file identities from the immutable passing attempt-005 packet.
packet_binding() {
  "$PYTHON" - "$PRIOR_PRODUCTION_PACKET" "$1" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
item = value["authority"][sys.argv[2]]
assert set(item) == {"size_bytes", "sha256"}
assert isinstance(item["size_bytes"], int) and item["size_bytes"] > 0
assert isinstance(item["sha256"], str) and len(item["sha256"]) == 64
print(item["size_bytes"], item["sha256"])
PY
}

read -r CHECKPOINT_EXPECTED_SIZE CHECKPOINT_EXPECTED_SHA < <(packet_binding checkpoint)
read -r SELECTED_STUDIES_EXPECTED_SIZE SELECTED_STUDIES_EXPECTED_SHA < <(packet_binding selected_study_manifest)
read -r SELECTED_SOURCE_EXPECTED_SIZE SELECTED_SOURCE_EXPECTED_SHA < <(packet_binding selected_source_manifest)
read -r SOURCE_METADATA_EXPECTED_SIZE SOURCE_METADATA_EXPECTED_SHA < <(packet_binding selected_source_metadata_receipt)
read -r SPLIT_MAP_EXPECTED_SIZE SPLIT_MAP_EXPECTED_SHA < <(packet_binding split_map)
read -r PRIOR_ENVIRONMENT_EXPECTED_SIZE PRIOR_ENVIRONMENT_EXPECTED_SHA < <(packet_binding environment_receipt)
read -r PRIOR_BATCH_PLAN_EXPECTED_SIZE PRIOR_BATCH_PLAN_EXPECTED_SHA < <(packet_binding batch_plan)

[[ "$CHECKPOINT_EXPECTED_SHA" = 7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b ]]
[[ "$SELECTED_STUDIES_EXPECTED_SHA" = 920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1 ]]
[[ "$SELECTED_SOURCE_EXPECTED_SHA" = 35071385477515e40e6cc1503561b7c7aa434127435ce91aa3ae8b0a009188ff ]]
[[ "$SPLIT_MAP_EXPECTED_SHA" = c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517 ]]

input_roles=(
  CHECKPOINT SELECTED_STUDIES SELECTED_SOURCE SOURCE_METADATA SPLIT_MAP
  PRIOR_ENVIRONMENT_RECEIPT MIGRATION_WITNESS MIGRATION_CLASSIFICATION
  PRIOR_PRODUCTION_PACKET PRIOR_PRODUCTION_BATCH_PLAN
  PRIOR_CAPACITY_PARENT PRIOR_CAPACITY_COMPOSITE
  PRIOR_SAFE_01 PRIOR_SAFE_02 PRIOR_SAFE_03 PRIOR_SAFE_04 PRIOR_SAFE_05 PRIOR_SAFE_06
  PRIOR_SAFE_07 PRIOR_SAFE_08 PRIOR_SAFE_09 PRIOR_SAFE_10 PRIOR_SAFE_11 PRIOR_SAFE_12
)
for role in "${input_roles[@]}"; do
  candidate="${!role}"
  [[ -f "$candidate" && ! -L "$candidate" && -O "$candidate" ]]
  mode="$(stat -c '%a' -- "$candidate")"
  (( (8#$mode & 0022) == 0 ))
done

verify_identity() {
  local candidate="$1" expected_size="$2" expected_sha="$3"
  [[ "$(stat -c '%s' -- "$candidate")" = "$expected_size" ]]
  [[ "$(sha256sum -- "$candidate" | awk '{print $1}')" = "$expected_sha" ]]
}
verify_identity "$CHECKPOINT" "$CHECKPOINT_EXPECTED_SIZE" "$CHECKPOINT_EXPECTED_SHA"
verify_identity "$SELECTED_STUDIES" "$SELECTED_STUDIES_EXPECTED_SIZE" "$SELECTED_STUDIES_EXPECTED_SHA"
verify_identity "$SELECTED_SOURCE" "$SELECTED_SOURCE_EXPECTED_SIZE" "$SELECTED_SOURCE_EXPECTED_SHA"
verify_identity "$SOURCE_METADATA" "$SOURCE_METADATA_EXPECTED_SIZE" "$SOURCE_METADATA_EXPECTED_SHA"
verify_identity "$SPLIT_MAP" "$SPLIT_MAP_EXPECTED_SIZE" "$SPLIT_MAP_EXPECTED_SHA"
verify_identity "$PRIOR_ENVIRONMENT_RECEIPT" "$PRIOR_ENVIRONMENT_EXPECTED_SIZE" "$PRIOR_ENVIRONMENT_EXPECTED_SHA"
verify_identity "$PRIOR_PRODUCTION_BATCH_PLAN" "$PRIOR_BATCH_PLAN_EXPECTED_SIZE" "$PRIOR_BATCH_PLAN_EXPECTED_SHA"
PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE=7492
PRIOR_PRODUCTION_PACKET_EXPECTED_SHA=2725570d1137640e0c00ae790f1ae3583d63b17c7f957e86e886892dd0e6ba07
PRIOR_CAPACITY_PARENT_EXPECTED_SIZE=2257
PRIOR_CAPACITY_PARENT_EXPECTED_SHA=267bf03d8f059b4a71ebe0754015af4a710edea37c060e3e392642e1ad335d71
PRIOR_CAPACITY_COMPOSITE_EXPECTED_SIZE=5003
PRIOR_CAPACITY_COMPOSITE_EXPECTED_SHA=28fad54a68f84165cb8340c3e666de84e1f6efc6bf20b146bc7bc006d9d4171c
MIGRATION_WITNESS_EXPECTED_SIZE="$(stat -c '%s' -- "$MIGRATION_WITNESS")"
MIGRATION_CLASSIFICATION_EXPECTED_SIZE="$(stat -c '%s' -- "$MIGRATION_CLASSIFICATION")"
verify_identity "$PRIOR_PRODUCTION_PACKET" "$PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE" "$PRIOR_PRODUCTION_PACKET_EXPECTED_SHA"
verify_identity "$PRIOR_CAPACITY_PARENT" "$PRIOR_CAPACITY_PARENT_EXPECTED_SIZE" "$PRIOR_CAPACITY_PARENT_EXPECTED_SHA"
verify_identity "$PRIOR_CAPACITY_COMPOSITE" "$PRIOR_CAPACITY_COMPOSITE_EXPECTED_SIZE" "$PRIOR_CAPACITY_COMPOSITE_EXPECTED_SHA"
verify_identity "$MIGRATION_WITNESS" "$MIGRATION_WITNESS_EXPECTED_SIZE" "$EXPECTED_MIGRATION_WITNESS_SHA256"
verify_identity "$MIGRATION_CLASSIFICATION" "$MIGRATION_CLASSIFICATION_EXPECTED_SIZE" "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256"

prior_sizes=(2566 2866 1136 1659 609 5261 1604 1588 3456 6295 1151 1173)
prior_hashes=(
  3e27c71285558402d546bd7e15290cbd08fbb5b1eea445d2d04bc5cc9d8df3d6
  8aaac6cbd62245184db05d47a98cd69ca5787a8caaec620e9a410ad99d0694b6
  6c17d2bccf992d79023023e011431fe86da35cbab68451f7fb3ea85520abbfa1
  bad47492ca5980b91b9560c5cd385afd87a4f54b48e1a6a3fe149ae04be03285
  d846b8d6210b50e20533bac3361c42ff5bde2bd24360ac18f6178c4c0b671c54
  5610bd3ec3a2cf3fd4ab905946ab6f3ab25824e38d30ced1b8061f349ba3b357
  ec480c69e3a2412b18c958b43bc361db55fd9faf16dcf3ed8b25256e5ba21f1a
  3cce1ef791ee20b9528f38c001d4fbb5405354c20053d083502138de9647c5dd
  3ac53f0aa7afbd3cd1195c78b467bb11041c87f9dc89d8ba0d64fd311c25dc56
  4ab11f7c255efd192cebc612704e11ae5b855d8f3fb8710b2261f768d522b11b
  7718e5f7aeb718f1b68973bae1bd27c3a7645d39e04414d218c616c64528b363
  ec64010819b001f25147c74b8e73af9673b1525acb91ef4355414a34f3f6e73d
)
for index in "${!prior_sizes[@]}"; do
  variable="PRIOR_SAFE_$(printf '%02d' "$((index + 1))")"
  verify_identity "${!variable}" "${prior_sizes[$index]}" "${prior_hashes[$index]}"
  printf -v "${variable}_EXPECTED_SIZE" '%s' "${prior_sizes[$index]}"
  printf -v "${variable}_EXPECTED_SHA" '%s' "${prior_hashes[$index]}"
done

[[ ! -e "$PHASE1EF_ATTEMPT_ROOT" && ! -L "$PHASE1EF_ATTEMPT_ROOT" ]]
[[ ! -e "/restricted/project/mimicecho/audits/$ATTEMPT_ID" && ! -L "/restricted/project/mimicecho/audits/$ATTEMPT_ID" ]]
[[ ! -e "$PRODUCTION_ROOT/attempts/lvef_c3_phase1ee_production_lock_006" && ! -L "$PRODUCTION_ROOT/attempts/lvef_c3_phase1ee_production_lock_006" ]]

temporary="$(mktemp "${OUTPUT_ENV}.tmp.XXXXXX")"
chmod 600 "$temporary"
{
  for variable in \
    WORKTREE EXPECTED_COMMIT PYTHON PYTHON_AUTHORITY CRC32C_PYTHON GCLOUD \
    GCLOUD_RECEIPT CLOUDSDK_CONFIG PRODUCTION_ROOT PHASE1EF_ATTEMPT_ROOT \
    ORIGINAL_AGGREGATE_ROOT SUPPLEMENTAL_AGGREGATE_ROOT PRIOR_CAPACITY_PARENT \
    PRIOR_CAPACITY_COMPOSITE PRIOR_PRODUCTION_PACKET PRIOR_PRODUCTION_BATCH_PLAN \
    SELECTED_STUDIES SELECTED_SOURCE SOURCE_METADATA SPLIT_MAP CHECKPOINT \
    PRIOR_ENVIRONMENT_RECEIPT MIGRATION_WITNESS MIGRATION_CLASSIFICATION \
    LVEF_C3_GCP_BILLING_PROJECT \
    CHECKPOINT_EXPECTED_SIZE CHECKPOINT_EXPECTED_SHA \
    SELECTED_STUDIES_EXPECTED_SIZE SELECTED_STUDIES_EXPECTED_SHA \
    SELECTED_SOURCE_EXPECTED_SIZE SELECTED_SOURCE_EXPECTED_SHA \
    SOURCE_METADATA_EXPECTED_SIZE SOURCE_METADATA_EXPECTED_SHA \
    SPLIT_MAP_EXPECTED_SIZE SPLIT_MAP_EXPECTED_SHA \
    PRIOR_ENVIRONMENT_EXPECTED_SIZE PRIOR_ENVIRONMENT_EXPECTED_SHA \
    PRIOR_BATCH_PLAN_EXPECTED_SIZE PRIOR_BATCH_PLAN_EXPECTED_SHA \
    PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE PRIOR_PRODUCTION_PACKET_EXPECTED_SHA \
    PRIOR_CAPACITY_PARENT_EXPECTED_SIZE PRIOR_CAPACITY_PARENT_EXPECTED_SHA \
    PRIOR_CAPACITY_COMPOSITE_EXPECTED_SIZE PRIOR_CAPACITY_COMPOSITE_EXPECTED_SHA \
    MIGRATION_WITNESS_EXPECTED_SIZE MIGRATION_CLASSIFICATION_EXPECTED_SIZE \
    EXPECTED_MIGRATION_WITNESS_SHA256 EXPECTED_MIGRATION_CLASSIFICATION_SHA256 \
    PRIOR_SAFE_01 PRIOR_SAFE_02 PRIOR_SAFE_03 PRIOR_SAFE_04 PRIOR_SAFE_05 PRIOR_SAFE_06 \
    PRIOR_SAFE_07 PRIOR_SAFE_08 PRIOR_SAFE_09 PRIOR_SAFE_10 PRIOR_SAFE_11 PRIOR_SAFE_12 \
    PRIOR_SAFE_01_EXPECTED_SIZE PRIOR_SAFE_01_EXPECTED_SHA \
    PRIOR_SAFE_02_EXPECTED_SIZE PRIOR_SAFE_02_EXPECTED_SHA \
    PRIOR_SAFE_03_EXPECTED_SIZE PRIOR_SAFE_03_EXPECTED_SHA \
    PRIOR_SAFE_04_EXPECTED_SIZE PRIOR_SAFE_04_EXPECTED_SHA \
    PRIOR_SAFE_05_EXPECTED_SIZE PRIOR_SAFE_05_EXPECTED_SHA \
    PRIOR_SAFE_06_EXPECTED_SIZE PRIOR_SAFE_06_EXPECTED_SHA \
    PRIOR_SAFE_07_EXPECTED_SIZE PRIOR_SAFE_07_EXPECTED_SHA \
    PRIOR_SAFE_08_EXPECTED_SIZE PRIOR_SAFE_08_EXPECTED_SHA \
    PRIOR_SAFE_09_EXPECTED_SIZE PRIOR_SAFE_09_EXPECTED_SHA \
    PRIOR_SAFE_10_EXPECTED_SIZE PRIOR_SAFE_10_EXPECTED_SHA \
    PRIOR_SAFE_11_EXPECTED_SIZE PRIOR_SAFE_11_EXPECTED_SHA \
    PRIOR_SAFE_12_EXPECTED_SIZE PRIOR_SAFE_12_EXPECTED_SHA; do
    printf '%s=%q\n' "$variable" "${!variable}"
  done
} >"$temporary"
chmod 600 "$temporary"
# A hard link gives this no-clobber publication an atomic EEXIST failure.
# Unlike `mv -n`, it cannot report success while silently retaining a
# concurrently created destination.
ln -- "$temporary" "$OUTPUT_ENV"
unlink "$temporary"
[[ -f "$OUTPUT_ENV" && ! -L "$OUTPUT_ENV" && -O "$OUTPUT_ENV" ]]
[[ "$(stat -c '%a' -- "$OUTPUT_ENV")" = 600 ]]
unset LVEF_C3_GCP_BILLING_PROJECT
printf '%s\n' 'PHASE1EF_OWNER_PRIVATE_ENVIRONMENT=PREPARED_NO_CLOUD'
