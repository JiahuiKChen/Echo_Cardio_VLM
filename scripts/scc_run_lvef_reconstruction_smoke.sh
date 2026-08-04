#!/usr/bin/env bash
# SGE-only Phase 1E-A four-study prospective reconstruction smoke.
set +eu
for init_file in \
  /etc/profile.d/modules.sh \
  /etc/profile \
  /etc/bashrc \
  /usr/share/Modules/init/bash; do
  command -v module >/dev/null 2>&1 && break
  [[ -f "$init_file" ]] && source "$init_file" >/dev/null 2>&1 || true
done
set -euo pipefail
umask 077

trap 'status=$?; printf "phase1e_a_job_status=FAIL exit_code=%s\n" "$status"; exit "$status"' ERR

: "${LVEF_E1A_ENV_FILE:?LVEF_E1A_ENV_FILE is required}"
test -f "$LVEF_E1A_ENV_FILE"
test -O "$LVEF_E1A_ENV_FILE"
test "$(stat -c '%a' "$LVEF_E1A_ENV_FILE")" = "600"
# The environment file is created by the committed runbook from fixed paths and
# shell-escaped scalar assignments only. It remains restricted and mode 0600.
source "$LVEF_E1A_ENV_FILE"
RUN_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${RUN_ROOT:?}"
: "${BILLING_PROJECT:?}"
: "${SMOKE_SOURCE:?}"
: "${EXPECTED_SMOKE_SOURCE_SHA256:?}"
: "${RELEASE_CHECKSUMS:?}"
: "${DOWNLOAD_ROOT:?}"
: "${CONFIG:?}"
: "${CHECKPOINT:?}"
: "${EXPECTED_CHECKPOINT_SHA256:?}"
: "${EXPECTED_CHECKPOINT_BYTES:?}"
: "${PRESERVATION_ROOT:?}"
: "${PRESERVATION_AGGREGATE:?}"

cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -x "$PYTHON"
test "${JOB_ID:-}" != ""
test "$JOB_ID" -eq "$JOB_ID" 2>/dev/null
[[ "$EXPECTED_SMOKE_SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]]
test "$(sha256sum "$SMOKE_SOURCE" | awk '{print $1}')" = "$EXPECTED_SMOKE_SOURCE_SHA256"
test "$(stat -c '%s' "$CHECKPOINT")" = "$EXPECTED_CHECKPOINT_BYTES"
test "$(sha256sum "$CHECKPOINT" | awk '{print $1}')" = "$EXPECTED_CHECKPOINT_SHA256"

if ! command -v gsutil >/dev/null 2>&1; then
  module load python3/3.10.12 >/dev/null 2>&1
  module load google-cloud-sdk/455.0.0 >/dev/null 2>&1
fi
command -v gsutil >/dev/null 2>&1

mkdir -p \
  "$RUN_ROOT/aggregate" \
  "$RUN_ROOT/provenance" \
  "$RUN_ROOT/restricted/logs"
COMMAND_FILE="$RUN_ROOT/provenance/scc_run_lvef_reconstruction_smoke.sh"
test ! -e "$COMMAND_FILE"
cp "$WORKTREE/scripts/scc_run_lvef_reconstruction_smoke.sh" "$COMMAND_FILE"
chmod 600 "$COMMAND_FILE"

"$PYTHON" scripts/download_lvef_reconstruction_smoke.py \
  --source-manifest "$SMOKE_SOURCE" \
  --expected-source-manifest-sha256 "$EXPECTED_SMOKE_SOURCE_SHA256" \
  --download-root "$DOWNLOAD_ROOT" \
  --restricted-report "$RUN_ROOT/restricted/download_report.json" \
  --aggregate-output "$RUN_ROOT/aggregate/download.json" \
  --billing-project "$BILLING_PROJECT" \
  --release-checksums "$RELEASE_CHECKSUMS" \
  >"$RUN_ROOT/restricted/logs/download.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/download.stderr.txt"

"$PYTHON" scripts/lvef_reconstruction_smoke.py audit-downloads \
  --source-manifest "$SMOKE_SOURCE" \
  --release-checksums "$RELEASE_CHECKSUMS" \
  --download-root "$DOWNLOAD_ROOT" \
  --restricted-output "$RUN_ROOT/restricted/download_audit.csv" \
  --aggregate-output "$RUN_ROOT/aggregate/download_audit.json" \
  >"$RUN_ROOT/restricted/logs/download_audit.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/download_audit.stderr.txt"

"$PYTHON" scripts/lvef_reconstruction_smoke.py audit-dicoms \
  --download-audit "$RUN_ROOT/restricted/download_audit.csv" \
  --download-root "$DOWNLOAD_ROOT" \
  --workers 4 \
  --restricted-output "$RUN_ROOT/restricted/dicom_audit.csv" \
  --aggregate-output "$RUN_ROOT/aggregate/dicom_audit.json" \
  >"$RUN_ROOT/restricted/logs/dicom_audit.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/dicom_audit.stderr.txt"

run_smoke_once() {
  local run_name="$1"
  local restricted="$RUN_ROOT/restricted/$run_name"
  local aggregate="$RUN_ROOT/aggregate/$run_name"
  mkdir -p "$restricted" "$aggregate"

  "$PYTHON" scripts/lvef_reconstruction_smoke.py extract-cines \
    --dicom-audit "$RUN_ROOT/restricted/dicom_audit.csv" \
    --download-root "$DOWNLOAD_ROOT" \
    --extraction-root "$restricted/extracted" \
    --workers 4 \
    --restricted-output "$restricted/extraction_manifest.csv" \
    --aggregate-output "$aggregate/extraction.json" \
    >"$RUN_ROOT/restricted/logs/${run_name}_extraction.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/${run_name}_extraction.stderr.txt"

  "$PYTHON" scripts/lvef_reconstruction_smoke.py embed-clips \
    --extraction-manifest "$restricted/extraction_manifest.csv" \
    --extraction-root "$restricted/extracted" \
    --checkpoint "$CHECKPOINT" \
    --expected-checkpoint-sha256 "$EXPECTED_CHECKPOINT_SHA256" \
    --expected-checkpoint-bytes "$EXPECTED_CHECKPOINT_BYTES" \
    --device cuda \
    --batch-size 8 \
    --seed 20260803 \
    --output-npz "$restricted/clip_embeddings_512.npz" \
    --restricted-output "$restricted/clip_embedding_manifest.csv" \
    --aggregate-output "$aggregate/embedding.json" \
    >"$RUN_ROOT/restricted/logs/${run_name}_embedding.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/${run_name}_embedding.stderr.txt"

  "$PYTHON" scripts/lvef_reconstruction_smoke.py pool-studies \
    --clip-embedding-npz "$restricted/clip_embeddings_512.npz" \
    --clip-embedding-manifest "$restricted/clip_embedding_manifest.csv" \
    --output-npz "$restricted/study_embeddings_512.npz" \
    --restricted-output "$restricted/study_embedding_manifest.csv" \
    --aggregate-output "$aggregate/pooling.json" \
    >"$RUN_ROOT/restricted/logs/${run_name}_pooling.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/${run_name}_pooling.stderr.txt"
}

run_smoke_once run_a
run_smoke_once run_b

"$PYTHON" scripts/lvef_reconstruction_smoke.py compare-runs \
  --extraction-pair \
  "extraction=$RUN_ROOT/restricted/run_a/extraction_manifest.csv,$RUN_ROOT/restricted/run_a/extracted,$RUN_ROOT/restricted/run_b/extraction_manifest.csv,$RUN_ROOT/restricted/run_b/extracted" \
  --manifest-pair \
  "clip_manifest=$RUN_ROOT/restricted/run_a/clip_embedding_manifest.csv,$RUN_ROOT/restricted/run_b/clip_embedding_manifest.csv" \
  --manifest-pair \
  "study_manifest=$RUN_ROOT/restricted/run_a/study_embedding_manifest.csv,$RUN_ROOT/restricted/run_b/study_embedding_manifest.csv" \
  --array-pair \
  "clip_embeddings=$RUN_ROOT/restricted/run_a/clip_embeddings_512.npz,$RUN_ROOT/restricted/run_b/clip_embeddings_512.npz" \
  --array-pair \
  "study_embeddings=$RUN_ROOT/restricted/run_a/study_embeddings_512.npz,$RUN_ROOT/restricted/run_b/study_embeddings_512.npz" \
  --restricted-output "$RUN_ROOT/restricted/reproducibility_details.json" \
  --aggregate-output "$RUN_ROOT/aggregate/reproducibility.json" \
  >"$RUN_ROOT/restricted/logs/reproducibility.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/reproducibility.stderr.txt"

SAFETY_GATE="$RUN_ROOT/aggregate/phase1e_a_smoke_safety_gate.json"
"$PYTHON" scripts/audit_lvef_reconstruction_smoke_run.py \
  --aggregate "source_manifest_summary=$RUN_ROOT/aggregate/source/reconstruction_source_manifest.summary.json" \
  --aggregate "source_manifest_safety=$RUN_ROOT/aggregate/source/reconstruction_source_manifest_safety_gate.json" \
  --aggregate "download=$RUN_ROOT/aggregate/download.json" \
  --aggregate "download_audit=$RUN_ROOT/aggregate/download_audit.json" \
  --aggregate "dicom_audit=$RUN_ROOT/aggregate/dicom_audit.json" \
  --aggregate "run_a_extraction=$RUN_ROOT/aggregate/run_a/extraction.json" \
  --aggregate "run_a_embedding=$RUN_ROOT/aggregate/run_a/embedding.json" \
  --aggregate "run_a_pooling=$RUN_ROOT/aggregate/run_a/pooling.json" \
  --aggregate "run_b_extraction=$RUN_ROOT/aggregate/run_b/extraction.json" \
  --aggregate "run_b_embedding=$RUN_ROOT/aggregate/run_b/embedding.json" \
  --aggregate "run_b_pooling=$RUN_ROOT/aggregate/run_b/pooling.json" \
  --aggregate "reproducibility=$RUN_ROOT/aggregate/reproducibility.json" \
  --output "$SAFETY_GATE" \
  >"$RUN_ROOT/restricted/logs/safety_gate.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/safety_gate.stderr.txt"

ENVIRONMENT_JSON="$RUN_ROOT/provenance/environment.json"
"$PYTHON" scripts/capture_lvef_reconstruction_environment.py \
  --source-commit "$EXPECTED_COMMIT" \
  --config "$CONFIG" \
  --checkpoint "$CHECKPOINT" \
  --smoke-source-manifest "$SMOKE_SOURCE" \
  --expected-smoke-source-manifest-sha256 "$EXPECTED_SMOKE_SOURCE_SHA256" \
  --output "$ENVIRONMENT_JSON" \
  >"$RUN_ROOT/restricted/logs/environment_capture.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/environment_capture.stderr.txt"

test "$(sha256sum "$SMOKE_SOURCE" | awk '{print $1}')" = "$EXPECTED_SMOKE_SOURCE_SHA256"
"$PYTHON" scripts/preserve_lvef_reconstruction_smoke.py \
  --run-root "$RUN_ROOT" \
  --restricted-output-dir "$PRESERVATION_ROOT" \
  --aggregate-output "$PRESERVATION_AGGREGATE" \
  --source-commit "$EXPECTED_COMMIT" \
  --config "$CONFIG" \
  --checkpoint "$CHECKPOINT" \
  --expected-checkpoint-sha256 "$EXPECTED_CHECKPOINT_SHA256" \
  --environment-json "$ENVIRONMENT_JSON" \
  --aggregate-safety-gate-json "$SAFETY_GATE" \
  --command-file "$COMMAND_FILE" \
  --scheduler SGE \
  --job-id "$JOB_ID" \
  --run-timestamp "$RUN_TIMESTAMP"

trap - ERR
printf '%s\n' 'phase1e_a_job_status=PASS'
