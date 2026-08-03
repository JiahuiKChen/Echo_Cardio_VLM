#!/bin/bash
# Extract, syntax-check, and execute one marked Bash block as a subprocess.
# Audit stdout/stderr never reach the parent terminal; only aggregate runner
# status and allowlisted aggregate-output paths are printed.

set -u
set -o pipefail

usage() {
  printf '%s\n' \
    "Usage: $0 --document FILE --block-id ID --run-dir DIR --label LABEL" \
    "          [--env-file FILE] [--safe-output aggregate/RELATIVE_PATH ...]"
}

fail_usage() {
  usage >&2
  exit 64
}

document=""
block_id=""
run_dir=""
label=""
env_file=""
safe_outputs=()
safe_output_count=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --document|--block-id|--run-dir|--label|--env-file|--safe-output)
      [[ "$#" -ge 2 ]] || fail_usage
      option="$1"
      value="$2"
      shift 2
      case "$option" in
        --document) document="$value" ;;
        --block-id) block_id="$value" ;;
        --run-dir) run_dir="$value" ;;
        --label) label="$value" ;;
        --env-file) env_file="$value" ;;
        --safe-output)
          safe_outputs+=("$value")
          safe_output_count=$((safe_output_count + 1))
          ;;
      esac
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *) fail_usage ;;
  esac
done

[[ -f "$document" ]] || fail_usage
[[ "$run_dir" = /* && -d "$run_dir" ]] || fail_usage
[[ "$block_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail_usage
[[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail_usage
if [[ -n "$env_file" && ! -f "$env_file" ]]; then
  fail_usage
fi

aggregate_dir="$run_dir/aggregate"
restricted_dir="$run_dir/restricted"
[[ -d "$aggregate_dir" && -d "$restricted_dir" ]] || fail_usage

if [[ "$safe_output_count" -gt 0 ]]; then
  for relative in "${safe_outputs[@]}"; do
    [[ "$relative" == aggregate/* ]] || fail_usage
    [[ "$relative" != /* && "$relative" != *".."* ]] || fail_usage
    [[ "$relative" =~ ^[A-Za-z0-9._/-]+$ ]] || fail_usage
  done
fi

status_path="$aggregate_dir/${label}.runner_status.json"
syntax_stdout="$restricted_dir/${label}.syntax.stdout.log"
syntax_stderr="$restricted_dir/${label}.syntax.stderr.log"
audit_stdout="$restricted_dir/${label}.stdout.log"
audit_stderr="$restricted_dir/${label}.stderr.log"
for target in "$status_path" "$syntax_stdout" "$syntax_stderr" "$audit_stdout" "$audit_stderr"; do
  if [[ -e "$target" ]]; then
    printf '{"label":"%s","runner_status":"REFUSED_EXISTING_OUTPUT"}\n' "$label"
    exit 73
  fi
done
: >"$syntax_stdout"
: >"$syntax_stderr"
: >"$audit_stdout"
: >"$audit_stderr"

temporary_script="$(mktemp "$restricted_dir/${label}.block.XXXXXX.sh")" || exit 73
chmod 600 "$temporary_script" 2>/dev/null || true
cleanup() {
  rm -f "$temporary_script"
}
trap cleanup EXIT HUP INT TERM

marker="<!-- lvef-scc-block:${block_id} -->"
awk -v marker="$marker" '
  BEGIN { state = 0; count = 0; fatal = 0 }
  $0 == marker {
    count += 1
    if (count > 1 || state != 0) { fatal = 1; exit }
    state = 1
    next
  }
  state == 1 {
    if ($0 == "```bash") { state = 2; next }
    if ($0 ~ /^[[:space:]]*$/) { next }
    fatal = 1
    exit
  }
  state == 2 {
    if ($0 == "```") { state = 3; next }
    print
    next
  }
  END {
    if (fatal || count != 1 || state != 3) { exit 65 }
  }
' "$document" >"$temporary_script" 2>"$syntax_stderr"
extract_status=$?

syntax_status=125
execution_status=125
subprocess_executed=false
if [[ "$extract_status" -eq 0 ]]; then
  /bin/bash -n "$temporary_script" >"$syntax_stdout" 2>>"$syntax_stderr"
  syntax_status=$?
fi

environment_args=(
  "PATH=/usr/bin:/bin"
  "LC_ALL=C"
  "LANG=C"
  "TZ=UTC"
  "HOME=/tmp"
  "TMPDIR=/tmp"
  "LVEF_SCC_RUN_DIR=$run_dir"
)

if [[ -n "$env_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || { extract_status=65; break; }
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      LVEF_SCC_PYTHON|LVEF_PHASE1D_EXPECTED_COMMIT)
        case "$value" in
          *$'\n'*|*$'\r'*|*$'\t'*) extract_status=65; break ;;
        esac
        environment_args+=("$key=$value")
        ;;
      *) extract_status=65; break ;;
    esac
  done <"$env_file"
fi

if [[ "$extract_status" -eq 0 && "$syntax_status" -eq 0 ]]; then
  subprocess_executed=true
  env -i "${environment_args[@]}" /bin/bash "$temporary_script" \
    >"$audit_stdout" 2>"$audit_stderr"
  execution_status=$?
fi

if command -v sha256sum >/dev/null 2>&1; then
  block_sha256="$(sha256sum "$temporary_script" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  block_sha256="$(shasum -a 256 "$temporary_script" | awk '{print $1}')"
else
  block_sha256="UNAVAILABLE"
fi

if [[ "$extract_status" -ne 0 ]]; then
  final_status="$extract_status"
elif [[ "$syntax_status" -ne 0 ]]; then
  final_status="$syntax_status"
else
  final_status="$execution_status"
fi

safe_ready=0
if [[ "$safe_output_count" -gt 0 ]]; then
  for relative in "${safe_outputs[@]}"; do
    [[ -f "$run_dir/$relative" ]] && safe_ready=$((safe_ready + 1))
  done
fi

printf '{\n' >"$status_path"
printf '  "audit": "lvef_scc_bash_block_runner",\n' >>"$status_path"
printf '  "label": "%s",\n' "$label" >>"$status_path"
printf '  "block_id": "%s",\n' "$block_id" >>"$status_path"
printf '  "block_sha256": "%s",\n' "$block_sha256" >>"$status_path"
printf '  "extract_status": %s,\n' "$extract_status" >>"$status_path"
printf '  "syntax_status": %s,\n' "$syntax_status" >>"$status_path"
printf '  "subprocess_executed": %s,\n' "$subprocess_executed" >>"$status_path"
printf '  "execution_status": %s,\n' "$execution_status" >>"$status_path"
printf '  "final_status": %s,\n' "$final_status" >>"$status_path"
printf '  "stdout_log_restricted": true,\n' >>"$status_path"
printf '  "stderr_log_restricted": true,\n' >>"$status_path"
printf '  "safe_outputs_requested": %s,\n' "$safe_output_count" >>"$status_path"
printf '  "safe_outputs_ready": %s\n' "$safe_ready" >>"$status_path"
printf '}\n' >>"$status_path"
chmod 600 "$status_path" "$syntax_stdout" "$syntax_stderr" "$audit_stdout" "$audit_stderr" 2>/dev/null || true

cat "$status_path"
if [[ "$safe_output_count" -gt 0 ]]; then
  for relative in "${safe_outputs[@]}"; do
    if [[ -f "$run_dir/$relative" ]]; then
      printf 'safe_output_ready=%s\n' "$run_dir/$relative"
    fi
  done
fi

exit "$final_status"
