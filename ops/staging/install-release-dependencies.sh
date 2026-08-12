#!/usr/bin/env bash
set -euo pipefail

checkout=${1:-/home/weidf/shenb/mito-data-studio-staging-20260731}
uv_bin=${UV_BIN:-/home/weidf/.local/bin/uv}
python_bin=${PYTHON_311:-/home/weidf/.local/share/uv/python/cpython-3.11-linux-x86_64-gnu/bin/python3.11}

"$uv_bin" venv --clear --python "$python_bin" "$checkout/venv"

# The lock contains hash-pinned packages from PyPI plus hash-pinned CUDA wheels
# from the PyTorch index. uv's default first-index policy cannot resolve common
# transitive packages once it sees their older PyTorch-index mirror. Considering
# both declared indexes is safe here because every accepted artifact is pinned
# by a cryptographic hash in requirements-release.txt.
"$uv_bin" pip sync \
  --python "$checkout/venv/bin/python" \
  --index-strategy unsafe-best-match \
  "$checkout/requirements-release.txt"

"$checkout/venv/bin/python" -c \
  'import django, numpy, torch; print(f"Python release environment OK: Django {django.get_version()}, NumPy {numpy.__version__}, Torch {torch.__version__}")'
