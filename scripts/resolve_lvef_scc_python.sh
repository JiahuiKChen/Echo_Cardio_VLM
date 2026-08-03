#!/bin/bash
# Resolve and validate the only Python interpreter used by LVEF SCC commands.
#
# Stdout contains only the selected executable path so callers may safely use
# command substitution. A JSON validation record and a safe JSON summary on
# stderr contain the interpreter/package evidence.

set -u
set -o pipefail

PREFERRED_LVEF_SCC_PYTHON="/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
MINIMUM_PYTHON_MAJOR=3
MINIMUM_PYTHON_MINOR=10

usage() {
  printf '%s\n' \
    "Usage: $0 --record-json ABSOLUTE_PATH" \
    "" \
    "Optional environment override:" \
    "  LVEF_SCC_PYTHON=/absolute/path/to/python"
}

fail() {
  printf 'lvef_scc_python_status=FAIL\n' >&2
  printf 'lvef_scc_python_reason=%s\n' "$1" >&2
  return "${2:-2}"
}

record_json=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --record-json)
      if [[ "$#" -lt 2 ]]; then
        usage >&2
        exit 64
      fi
      record_json="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
done

if [[ -z "$record_json" || "$record_json" != /* ]]; then
  fail "record_json_must_be_an_absolute_path" 64
  exit $?
fi
if [[ -e "$record_json" ]]; then
  fail "record_json_already_exists" 73
  exit $?
fi
record_parent="${record_json%/*}"
[[ -n "$record_parent" ]] || record_parent="/"
if [[ ! -d "$record_parent" || ! -w "$record_parent" ]]; then
  fail "record_json_parent_unavailable" 73
  exit $?
fi

override_used=false
if [[ -n "${LVEF_SCC_PYTHON:-}" ]]; then
  candidate="$LVEF_SCC_PYTHON"
  override_used=true
else
  candidate="$PREFERRED_LVEF_SCC_PYTHON"
fi

if [[ "$candidate" != /* ]]; then
  fail "python_candidate_must_be_an_absolute_path" 64
  exit $?
fi
case "$candidate" in
  *$'\n'*|*$'\r'*|*$'\t'*)
    fail "python_candidate_contains_control_character" 64
    exit $?
    ;;
esac
if [[ ! -x "$candidate" || ! -f "$candidate" ]]; then
  fail "selected_python_is_not_an_executable_file" 69
  exit $?
fi

# Preserve the virtual-environment entry point rather than resolving its
# symlink to a base interpreter, which could discard pyvenv.cfg semantics.
candidate_dir="${candidate%/*}"
[[ -n "$candidate_dir" ]] || candidate_dir="/"
candidate_name="${candidate##*/}"
if ! selected_dir="$(cd "$candidate_dir" 2>/dev/null && pwd -P)"; then
  fail "selected_python_parent_cannot_be_resolved" 69
  exit $?
fi
selected_python="$selected_dir/$candidate_name"

# This probe is deliberately compatible with old Python. Reject unsupported
# versions before executing any project source that uses modern syntax.
version_probe='import sys; print("%d.%d.%d" % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))'
if ! version_text="$($selected_python -c "$version_probe" 2>/dev/null)"; then
  fail "selected_python_version_probe_failed" 69
  exit $?
fi
case "$version_text" in
  ''|*[!0-9.]*|*.*.*.*)
    fail "selected_python_reported_invalid_version" 69
    exit $?
    ;;
esac
version_major="${version_text%%.*}"
version_remainder="${version_text#*.}"
version_minor="${version_remainder%%.*}"
if (( version_major < MINIMUM_PYTHON_MAJOR )) || \
   (( version_major == MINIMUM_PYTHON_MAJOR && version_minor < MINIMUM_PYTHON_MINOR )); then
  printf 'lvef_scc_python_selected=%s\n' "$selected_python" >&2
  printf 'lvef_scc_python_version=%s\n' "$version_text" >&2
  fail "python_3_10_or_newer_required_no_fallback_attempted" 65
  exit $?
fi

temporary_record="$record_parent/.${record_json##*/}.tmp.$$"
if [[ -e "$temporary_record" ]]; then
  fail "temporary_record_collision" 73
  exit $?
fi

"$selected_python" - "$temporary_record" "$selected_python" "$override_used" <<'PY' >&2
import hashlib
import importlib
import json
import os
import sys

record_path = sys.argv[1]
selected_executable = sys.argv[2]
override_used = sys.argv[3].lower() == "true"
required = ("numpy", "pandas", "scipy", "sklearn", "yaml")

packages = {}
all_imports_ok = True
for name in required:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        packages[name] = {
            "import_ok": False,
            "version": None,
            "error_type": type(exc).__name__,
        }
        all_imports_ok = False
    else:
        packages[name] = {
            "import_ok": True,
            "version": str(getattr(module, "__version__", "UNKNOWN")),
            "error_type": None,
        }

runtime_executable = sys.executable
hash_value = None
hash_status = "UNAVAILABLE"
try:
    digest = hashlib.sha256()
    with open(runtime_executable, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    hash_value = digest.hexdigest()
    hash_status = "OK"
except OSError:
    pass

supported = sys.version_info >= (3, 10)
status = "PASS" if supported and all_imports_ok else "FAIL"
record = {
    "audit": "lvef_scc_python_resolution",
    "status": status,
    "selected_executable": selected_executable,
    "runtime_executable": runtime_executable,
    "python_version": ".".join(str(value) for value in sys.version_info[:3]),
    "minimum_python": "3.10",
    "version_supported": supported,
    "executable_sha256": hash_value,
    "executable_sha256_status": hash_status,
    "required_packages": packages,
    "all_required_imports_ok": all_imports_ok,
    "override_used": override_used,
    "fallback_attempted": False,
}
with open(record_path, "x", encoding="utf-8") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")

safe_summary = {
    "status": status,
    "selected_executable": selected_executable,
    "python_version": record["python_version"],
    "executable_sha256": hash_value,
    "executable_sha256_status": hash_status,
    "required_package_imports": {
        name: {
            "import_ok": details["import_ok"],
            "version": details["version"],
        }
        for name, details in packages.items()
    },
    "all_required_imports_ok": all_imports_ok,
    "fallback_attempted": False,
}
print(json.dumps(safe_summary, sort_keys=True))
raise SystemExit(0 if status == "PASS" else 5)
PY
probe_status=$?

if [[ ! -f "$temporary_record" ]]; then
  fail "python_validation_record_not_created" 70
  exit $?
fi
if ! mv "$temporary_record" "$record_json"; then
  fail "python_validation_record_cannot_be_finalized" 73
  exit $?
fi
chmod 600 "$record_json" 2>/dev/null || true

if [[ "$probe_status" -ne 0 ]]; then
  fail "required_package_import_validation_failed_no_fallback_attempted" 69
  exit $?
fi

printf '%s\n' "$selected_python"
