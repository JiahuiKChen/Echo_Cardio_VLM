#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${VENV_PATH:-${PROJECT_ROOT}/.venv-echoprime}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TRANSFORMERS_VERSION="${TRANSFORMERS_VERSION:-4.57.0}"
OPENCV_VERSION="${OPENCV_VERSION:-4.10.0.84}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/setup_echoprime_env.sh [--repo-requirements]

Default behavior installs a minimal Apple-Silicon-safe inference stack that is
enough for EchoPrime import and encoder smoke tests on macOS.

Options:
  --repo-requirements   Try installing EchoPrime's raw requirements.txt after
                        the minimal stack. This may fail on Python 3.12.

Environment variables:
  PYTHON_BIN            Python executable to use (default: python3)
  VENV_PATH             Venv path (default: .venv-echoprime under project root)
  TRANSFORMERS_VERSION  Default: 4.57.0 to match repo pin
  OPENCV_VERSION        Default: 4.10.0.84
EOF
}

INSTALL_REPO_REQUIREMENTS=0
for arg in "$@"; do
  case "${arg}" in
    --repo-requirements)
      INSTALL_REPO_REQUIREMENTS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      usage
      exit 1
      ;;
  esac
done

echo "[info] Project root: ${PROJECT_ROOT}"
echo "[info] Python: $(${PYTHON_BIN} --version)"

"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required.")
print(f"[info] Python version OK: {sys.version.split()[0]}")
PY

if [[ ! -d "${VENV_PATH}" ]]; then
  echo "[info] Creating venv at ${VENV_PATH}"
  "${PYTHON_BIN}" -m venv "${VENV_PATH}"
else
  echo "[info] Reusing existing venv at ${VENV_PATH}"
fi

source "${VENV_PATH}/bin/activate"

python -m pip install --upgrade pip

echo "[info] Installing minimal EchoPrime stack"
python -m pip install \
  torch \
  torchvision \
  pandas \
  matplotlib \
  scikit-learn \
  tqdm \
  pydicom \
  "opencv-python-headless==${OPENCV_VERSION}" \
  "transformers==${TRANSFORMERS_VERSION}"

if [[ "${INSTALL_REPO_REQUIREMENTS}" -eq 1 ]]; then
  echo "[info] Attempting repo requirements install"
  python -m pip install -r "${PROJECT_ROOT}/EchoPrime/requirements.txt" || {
    echo "[warn] Repo requirements install failed."
    echo "[warn] This is expected on some Python/macOS combinations."
  }
fi

echo "[info] Final package versions"
python - <<'PY'
import torch, torchvision, transformers, cv2, pydicom
print("torch", torch.__version__)
print("torchvision", torchvision.__version__)
print("transformers", transformers.__version__)
print("opencv", cv2.__version__)
print("pydicom", pydicom.__version__)
print("cuda", torch.cuda.is_available())
print("mps", getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available())
PY

echo "[done] Activate with: source '${VENV_PATH}/bin/activate'"
