# SCC Phase 1E-A prospective reconstruction smoke commands

This runbook is limited to the authorized four-study, training-only Phase 1E-A technical smoke. It constructs the restricted selected-cohort public-source authority, performs an exact remote-set and resource preflight, downloads only the four prespecified smoke studies, and submits two clean extraction/encoder/pooling runs on one allocated GPU. It does not authorize the full C3 reconstruction, model fitting, prediction generation, confirmatory-performance access, or modification of the accepted abstract or historical snapshot.

The literal `__PHASE1EA_COMMIT__` below is a fail-closed handoff placeholder. Replace it only after the Phase 1E-A implementation commit has been pushed to `origin/codex/lvef-multitask-revalidation`. Do not substitute a working-tree commit or continue when the SCC worktree is dirty.

All patient-, study-, DICOM-, clip-, checksum-row-, extracted-array-, and embedding-level outputs stay below the new restricted run root. Only the explicitly listed aggregate outputs at the end may be pasted back, and only after both the run-level aggregate safety gate and preservation verification report `PASS`.

## 1. Authority, interpreter, and fresh run root

Paste this block into the interactive SCC shell.

```bash
set -euo pipefail
umask 077

WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
BRANCH="codex/lvef-multitask-revalidation"
EXPECTED_COMMIT="__PHASE1EA_COMMIT__"
BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-mimic-iv-anesthesia}"

FULLSCALE_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all"
STAGE_D_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/stage_d_500study_scc"
PHASE1D_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1d_20260803T234842Z_912250174f99"

SELECTED_STUDIES="$FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
SPLIT_MAP="$FULLSCALE_ROOT/manifests/subject_split_map_v1.csv"
HISTORICAL_STUDY_MANIFEST="$FULLSCALE_ROOT/study_embeddings_512/study_embedding_manifest.csv"
STAGE_D_RECORDS="$STAGE_D_ROOT/manifests/selected_records.csv"
DUPLICATE_RESOLUTION="$PHASE1D_ROOT/restricted/duplicate_v2/duplicate_clip_resolution_v2_restricted.csv"
CANONICAL_INVENTORY="$PHASE1D_ROOT/restricted/canonical_inventory/canonical_selected_clip_inventory_restricted.csv"

CONFIG="$WORKTREE/configs/lvef_multitask_revalidation.yaml"
CHECKPOINT="/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt"
EXPECTED_CHECKPOINT_SHA256="7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b"
EXPECTED_CHECKPOINT_BYTES="138642379"
EXPECTED_PYTHON_SHA256="1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"

cd "$WORKTREE"
git fetch origin --prune
git merge --ff-only "origin/$BRANCH"
test "$EXPECTED_COMMIT" != "__PHASE1EA_COMMIT__"
test "$EXPECTED_COMMIT" = "$(git rev-parse "origin/$BRANCH")"
test "$(git branch --show-current)" = "$BRANCH"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"

RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${RUN_STAMP}_${EXPECTED_COMMIT:0:12}"
RUN_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1e_a_${RUN_ID}"
PRESERVATION_ROOT="${RUN_ROOT}_preservation"
PRESERVATION_AGGREGATE="${RUN_ROOT}_preservation.summary.json"
SCHEDULER_LOG_ROOT="${RUN_ROOT}_scheduler_logs"

test ! -e "$RUN_ROOT"
test ! -e "$PRESERVATION_ROOT"
test ! -e "$PRESERVATION_AGGREGATE"
test ! -e "$SCHEDULER_LOG_ROOT"
mkdir -p \
  "$RUN_ROOT/aggregate/source" \
  "$RUN_ROOT/provenance" \
  "$RUN_ROOT/restricted/source" \
  "$RUN_ROOT/restricted/logs" \
  "$SCHEDULER_LOG_ROOT"

export LVEF_SCC_PYTHON="/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
PYTHON="$(
  scripts/resolve_lvef_scc_python.sh \
    --record-json "$RUN_ROOT/provenance/python_resolution.json" \
    2>"$RUN_ROOT/restricted/logs/python_resolution.stderr.txt"
)"
test -x "$PYTHON"
test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256"
test "$(stat -c '%s' "$CHECKPOINT")" = "$EXPECTED_CHECKPOINT_BYTES"
test "$(sha256sum "$CHECKPOINT" | awk '{print $1}')" = "$EXPECTED_CHECKPOINT_SHA256"

for required in \
  "$SELECTED_STUDIES" \
  "$SPLIT_MAP" \
  "$HISTORICAL_STUDY_MANIFEST" \
  "$STAGE_D_RECORDS" \
  "$DUPLICATE_RESOLUTION" \
  "$CANONICAL_INVENTORY" \
  "$CONFIG" \
  "$CHECKPOINT"; do
  test -f "$required"
done

for index in {0..8}; do
  printf -v component 'batch_%03d' "$index"
  test -f "$FULLSCALE_ROOT/batches/${component}_records.csv"
done

"$PYTHON" scripts/run_phase1a_tests.py \
  >"$RUN_ROOT/restricted/logs/dependency_light_tests.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/dependency_light_tests.stderr.txt"
"$PYTHON" -c '
import cv2, pydicom, torch, torchvision
assert tuple(int(part) for part in pydicom.__version__.split(".")[:2]) >= (3, 0)
assert callable(getattr(getattr(pydicom, "pixels", None), "pixel_array", None))
'
```

The dependency-light suite must exit zero. Any failure blocks source construction and download; do not rely on a hard-coded test count because the committed suite may grow.

## 2. Public release checksum authority and selected source manifest

This downloads the public release checksum text only. It does not download a DICOM yet. The builder joins those public SHA-256 values to all selected source rows, verifies exact `n_dicoms` counts against the 4,530-study authority, and derives the four technical roles from restricted Phase 1D provenance without reading outcomes, predictions, performance, or embedding arrays.

```bash
if ! command -v gsutil >/dev/null 2>&1; then
  module load google-cloud-sdk/455.0.0
fi
command -v gsutil >/dev/null 2>&1

RELEASE_CHECKSUMS="$RUN_ROOT/restricted/source/SHA256SUMS.txt"
gsutil -u "$BILLING_PROJECT" cp \
  "gs://mimic-iv-echo-1.0.physionet.org/SHA256SUMS.txt" \
  "$RELEASE_CHECKSUMS" \
  >"$RUN_ROOT/restricted/logs/release_checksum_download.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/release_checksum_download.stderr.txt"
test -s "$RELEASE_CHECKSUMS"

RECORD_ARGS=(--record-component "stage_d=$STAGE_D_RECORDS")
for index in {0..8}; do
  printf -v component 'batch_%03d' "$index"
  RECORD_ARGS+=(
    --record-component
    "$component=$FULLSCALE_ROOT/batches/${component}_records.csv"
  )
done

"$PYTHON" scripts/build_lvef_reconstruction_source_manifest.py \
  --selected-studies "$SELECTED_STUDIES" \
  --split-map "$SPLIT_MAP" \
  "${RECORD_ARGS[@]}" \
  --historical-study-manifest "$HISTORICAL_STUDY_MANIFEST" \
  --duplicate-resolution "$DUPLICATE_RESOLUTION" \
  --canonical-inventory "$CANONICAL_INVENTORY" \
  --release-checksums "$RELEASE_CHECKSUMS" \
  --restricted-output-dir "$RUN_ROOT/restricted/source" \
  --aggregate-output-dir "$RUN_ROOT/aggregate/source" \
  >"$RUN_ROOT/restricted/logs/source_manifest.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/source_manifest.stderr.txt"

SMOKE_SOURCE="$RUN_ROOT/restricted/source/technical_smoke_source_manifest_restricted.csv"
test -f "$RUN_ROOT/restricted/source/selected_source_manifest_restricted.csv"
test -f "$SMOKE_SOURCE"
EXPECTED_SMOKE_SOURCE_SHA256="$(
  "$PYTHON" -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
value = payload["technical_smoke_source_manifest_sha256"]
assert isinstance(value, str) and len(value) == 64
print(value)
' "$RUN_ROOT/aggregate/source/reconstruction_source_manifest.summary.json"
)"
test "$(sha256sum "$SMOKE_SOURCE" | awk '{print $1}')" = "$EXPECTED_SMOKE_SOURCE_SHA256"
```

The selected-source manifest is restricted even though it contains public object locators. Never copy it, the smoke manifest, the release checksum text, or a checksum-linked source row into Git.

## 3. Exact remote-set and resource preflight

The preflight lists the complete DICOM prefix for each of the four deterministically chosen training studies, requires exact equality with the restricted smoke manifest, and enforces the locked limits: exactly four studies/subjects, at most 1,000 objects, at most 5 GiB expected raw transfer, and at least 20 GiB free. It does not download DICOM bytes.

```bash
DOWNLOAD_ROOT="$RUN_ROOT/restricted/downloads"

"$PYTHON" scripts/download_lvef_reconstruction_smoke.py \
  --source-manifest "$SMOKE_SOURCE" \
  --expected-source-manifest-sha256 "$EXPECTED_SMOKE_SOURCE_SHA256" \
  --download-root "$DOWNLOAD_ROOT" \
  --restricted-report "$RUN_ROOT/restricted/download_preflight_report.json" \
  --aggregate-output "$RUN_ROOT/aggregate/download_preflight.json" \
  --billing-project "$BILLING_PROJECT" \
  --release-checksums "$RELEASE_CHECKSUMS" \
  --preflight-only \
  >"$RUN_ROOT/restricted/logs/download_preflight.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/download_preflight.stderr.txt"

"$PYTHON" -c '
import json, sys
source = json.load(open(sys.argv[1], encoding="utf-8"))
preflight = json.load(open(sys.argv[2], encoding="utf-8"))
assert source["status"] == "PASS"
assert source["n_selected_subjects"] == 4530
assert source["n_selected_studies"] == 4530
assert source["n_source_studies"] == 4530
assert source["source_object_counts_match_selected_authority"] is True
assert source["all_selected_objects_have_release_sha256"] is True
assert source["candidate_construction_mode"] == "phase1d_restricted_provenance"
assert source["restricted_input_authority_hash_set_exact"] is True
assert source["locked_split_counts_match"] is True
assert source["split_counts"] == {"train": 3171, "val": 679, "test": 680}
assert source["selection_salt"] == "lvef-multitask-phase1e-a-smoke4-v1"
assert source["smoke_n_studies"] == 4
assert source["smoke_n_roles"] == 4
assert source["smoke_all_train"] is True
assert preflight["status"] == "PASS_PREFLIGHT_ONLY"
assert preflight["n_studies"] == 4
assert preflight["n_subjects"] == 4
assert preflight["n_expected_objects"] <= 1000
assert preflight["total_remote_bytes"] <= 5 * 1024**3
assert preflight["free_bytes_before"] >= 20 * 1024**3
assert preflight["exact_remote_set"] is True
assert preflight["all_sizes_verified"] is True
assert preflight["source_manifest_sha256_verified"] is True
assert preflight["source_manifest_sha256"] == source["technical_smoke_source_manifest_sha256"]
' \
  "$RUN_ROOT/aggregate/source/reconstruction_source_manifest.summary.json" \
  "$RUN_ROOT/aggregate/download_preflight.json"
```

If this block fails, do not substitute another study. Inspect only the restricted log/report on SCC and return an aggregate error code if one exists.

## 4. Restricted job environment and direct SGE submission

The committed `scripts/scc_run_lvef_reconstruction_smoke.sh` is the only submitted job body. Do not generate or edit a temporary job script. It performs the bounded download, checksum and DICOM audits, two clean runs on the same allocated GPU, the exact five-artifact comparison, aggregate safety gating, environment capture through `scripts/capture_lvef_reconstruction_environment.py`, and second-pass preservation.

Environment capture is fail-closed and restricted. It must record the complete installed-distribution inventory as nonempty `{name, version}` entries, the pinned Python/config/checkpoint/script identities, repository authority, CUDA/cuDNN and allocated-GPU metadata, and SGE metadata. Preservation validates that package-inventory schema and reconciles the scheduler exactly: the captured scheduler must be `SGE`, and its numeric `scheduler_job_id` must equal the `JOB_ID` passed to preservation. A missing or inconsistent inventory, scheduler, or job identity blocks preservation.

The SGE log directory is deliberately outside `RUN_ROOT`; otherwise the preservation verifier could hash a log while SGE is still appending to it.

```bash
JOB_ENV="$RUN_ROOT/restricted/phase1e_a_job.env"
test ! -e "$JOB_ENV"
{
  printf 'WORKTREE=%q\n' "$WORKTREE"
  printf 'EXPECTED_COMMIT=%q\n' "$EXPECTED_COMMIT"
  printf 'PYTHON=%q\n' "$PYTHON"
  printf 'RUN_ROOT=%q\n' "$RUN_ROOT"
  printf 'BILLING_PROJECT=%q\n' "$BILLING_PROJECT"
  printf 'SMOKE_SOURCE=%q\n' "$SMOKE_SOURCE"
  printf 'EXPECTED_SMOKE_SOURCE_SHA256=%q\n' "$EXPECTED_SMOKE_SOURCE_SHA256"
  printf 'RELEASE_CHECKSUMS=%q\n' "$RELEASE_CHECKSUMS"
  printf 'DOWNLOAD_ROOT=%q\n' "$DOWNLOAD_ROOT"
  printf 'CONFIG=%q\n' "$CONFIG"
  printf 'CHECKPOINT=%q\n' "$CHECKPOINT"
  printf 'EXPECTED_CHECKPOINT_SHA256=%q\n' "$EXPECTED_CHECKPOINT_SHA256"
  printf 'EXPECTED_CHECKPOINT_BYTES=%q\n' "$EXPECTED_CHECKPOINT_BYTES"
  printf 'PRESERVATION_ROOT=%q\n' "$PRESERVATION_ROOT"
  printf 'PRESERVATION_AGGREGATE=%q\n' "$PRESERVATION_AGGREGATE"
} >"$JOB_ENV"
chmod 600 "$JOB_ENV"
test "$(stat -c '%a' "$JOB_ENV")" = "600"

bash -n scripts/scc_run_lvef_reconstruction_smoke.sh
export LVEF_E1A_ENV_FILE="$JOB_ENV"
qsub \
  -P mimicecho \
  -N lvef_e1a_smoke \
  -j y \
  -o "$SCHEDULER_LOG_ROOT" \
  -l h_rt=6:00:00 \
  -l gpus=1 \
  -l gpu_c=8.0 \
  -l gpu_memory=48G \
  -pe omp 4 \
  -l mem_per_core=4G \
  -v LVEF_E1A_ENV_FILE \
  scripts/scc_run_lvef_reconstruction_smoke.sh

printf 'phase1e_a_run_root=%s\n' "$RUN_ROOT"
printf 'phase1e_a_scheduler_log_root=%s\n' "$SCHEDULER_LOG_ROOT"
```

Monitor with `qstat -u "$USER"`. Do not paste scheduler logs. A completed job must end with `phase1e_a_job_status=PASS`; any other status is blocking and must be investigated locally without exporting restricted stderr.

## 5. Aggregate-only verification and safe outputs

Run this only after the batch job has completed successfully. The first command independently requires the run safety gate and preservation pack to pass before any aggregate file is printed.

```bash
"$PYTHON" -c '
import json, sys
safety = json.load(open(sys.argv[1], encoding="utf-8"))
preservation = json.load(open(sys.argv[2], encoding="utf-8"))
assert safety["status"] == "PASS"
assert safety["aggregate_safety_gate_passed"] is True
assert safety["n_aggregate_artifacts"] == 12
assert safety["models_fitted"] is False
assert safety["predictions_generated"] is False
assert safety["confirmatory_performance_accessed"] is False
assert preservation["status"] == "PASS"
assert preservation["exact_file_set"] is True
assert preservation["all_sizes_match"] is True
assert preservation["all_sha256_match"] is True
assert preservation["no_symlinks"] is True
assert preservation["environment_recorded"] is True
assert preservation["job_metadata_recorded"] is True
assert preservation["aggregate_safety_gate_recorded"] is True
' \
  "$RUN_ROOT/aggregate/phase1e_a_smoke_safety_gate.json" \
  "$PRESERVATION_AGGREGATE"

cat "$RUN_ROOT/aggregate/source/reconstruction_source_manifest.summary.json"
cat "$RUN_ROOT/aggregate/source/reconstruction_source_manifest_by_component.csv"
cat "$RUN_ROOT/aggregate/source/reconstruction_source_manifest_safety_gate.json"
cat "$RUN_ROOT/aggregate/download_preflight.json"
cat "$RUN_ROOT/aggregate/download.json"
cat "$RUN_ROOT/aggregate/download_audit.json"
cat "$RUN_ROOT/aggregate/dicom_audit.json"
cat "$RUN_ROOT/aggregate/run_a/extraction.json"
cat "$RUN_ROOT/aggregate/run_a/embedding.json"
cat "$RUN_ROOT/aggregate/run_a/pooling.json"
cat "$RUN_ROOT/aggregate/run_b/extraction.json"
cat "$RUN_ROOT/aggregate/run_b/embedding.json"
cat "$RUN_ROOT/aggregate/run_b/pooling.json"
cat "$RUN_ROOT/aggregate/reproducibility.json"
cat "$RUN_ROOT/aggregate/phase1e_a_smoke_safety_gate.json"
cat "$PRESERVATION_AGGREGATE"
```

Never paste or copy back any file beneath `restricted/`, `provenance/`, the detailed preservation directory, or the scheduler-log directory. This includes source or smoke manifests, the release checksum text, identifiers, locators, per-object hashes, DICOMs, extracted NPZs, embedding arrays/manifests, detailed download or reproducibility reports, environment JSON, command copies, stdout/stderr, and preservation inventory/metadata. A passing four-study smoke validates only the prospective implementation and reproducibility controls; it does not authorize or establish the full selected-cohort C3 authority or confirmatory modeling.
