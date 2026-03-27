#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
GPU environment smoke test for ECHO AI cloud runs.

Usage:
  smoke_gpu.sh \
    [--repo-root /workspace] \
    [--output-json /workspace/outputs/cloud_env_smoke/gpu_smoke.json] \
    [--run-echoprime true|false]
EOF
}

to_bool() {
  local value="${1:-}"
  local normalized
  normalized="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    true|1|yes|y) echo "true" ;;
    false|0|no|n) echo "false" ;;
    *)
      echo "[error] Invalid boolean: ${value}" >&2
      exit 1
      ;;
  esac
}

REPO_ROOT="/workspace"
OUTPUT_JSON="/workspace/outputs/cloud_env_smoke/gpu_smoke.json"
RUN_ECHOPRIME="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    --output-json) OUTPUT_JSON="$2"; shift 2 ;;
    --run-echoprime) RUN_ECHOPRIME="$(to_bool "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "${OUTPUT_JSON}")"

echo "[info] Running NVIDIA device check..."
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "[error] nvidia-smi not found; GPU runtime not active." >&2
  exit 1
fi
nvidia-smi

echo "[info] Running Python/CUDA import + forward-pass smoke..."
python3 - <<PY
import json
import os
import platform
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pydicom
import sklearn
import torch
import torchvision

out_path = Path("${OUTPUT_JSON}")
repo_root = Path("${REPO_ROOT}")
run_echoprime = "${RUN_ECHOPRIME}" == "true"

result = {
    "platform": platform.platform(),
    "python_version": platform.python_version(),
    "torch_version": torch.__version__,
    "torchvision_version": torchvision.__version__,
    "cuda_available": bool(torch.cuda.is_available()),
    "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
    "numpy_version": np.__version__,
    "pandas_version": pd.__version__,
    "pydicom_version": pydicom.__version__,
    "sklearn_version": sklearn.__version__,
}

if not torch.cuda.is_available():
    raise SystemExit("[error] torch.cuda.is_available() is False inside container.")

device = torch.device("cuda:0")
x = torch.randn(2, 3, 16, 224, 224, device=device)
model = torchvision.models.video.mvit_v2_s(weights=None).to(device).eval()
with torch.no_grad():
    y = model(x)
result["video_forward_shape"] = list(y.shape)
result["cuda_memory_allocated_bytes"] = int(torch.cuda.memory_allocated(device))
result["cuda_max_memory_allocated_bytes"] = int(torch.cuda.max_memory_allocated(device))

if run_echoprime:
    smoke_script = repo_root / "scripts" / "echoprime_smoke_test.py"
    if smoke_script.exists():
        cmd = [
            "python3",
            str(smoke_script),
            "--repo-root",
            str(repo_root / "EchoPrime"),
            "--device",
            "cuda",
            "--output-json",
            str(repo_root / "outputs" / "echoprime_smoke_gpu.json"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result["echoprime_smoke_returncode"] = proc.returncode
        result["echoprime_smoke_stdout_tail"] = proc.stdout[-1500:]
        result["echoprime_smoke_stderr_tail"] = proc.stderr[-1500:]
    else:
        result["echoprime_smoke_skipped_reason"] = "script not found"

out_path.write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
print(f"[written] {out_path}")
PY

echo "[done] GPU smoke test completed."
