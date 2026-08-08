#!/usr/bin/env bash
set -euo pipefail

# Read-only/build-only release gate for an isolated upgrade checkout. Database
# migrations and service restarts are deliberately outside this script.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
python_bin=${MITO_PYTHON:-python}

case "$repo_root" in
  */mito-data-agent-production-v1.0.0)
    echo "Refusing to run the upgrade gate in the frozen production checkout." >&2
    exit 2
    ;;
esac

if [[ ${MITO_UPGRADE_PROFILE:-} != webknossos ]]; then
  echo "MITO_UPGRADE_PROFILE=webknossos is required." >&2
  exit 2
fi
if [[ ${VITE_MITO_UPGRADE_PROFILE:-} != webknossos ]]; then
  echo "VITE_MITO_UPGRADE_PROFILE=webknossos is required." >&2
  exit 2
fi
if [[ ${VITE_FEATURE_CHUNK_PULL_QUEUE:-} != true ]] ||
   [[ ${VITE_FEATURE_CHUNK_RENDERER:-} != true ]]; then
  echo "Both frontend chunk feature flags must be true for the full candidate gate." >&2
  exit 2
fi

cd "$repo_root/backend"
"$python_bin" manage.py shell -c '
from django.conf import settings
names = [name for name in dir(settings) if name.startswith("FEATURE_")]
disabled = [name for name in names if not getattr(settings, name)]
if disabled:
    raise SystemExit("Full upgrade candidate has disabled backend flags: " + ", ".join(disabled))
'
"$python_bin" manage.py check --tag deployment
"$python_bin" manage.py makemigrations --check --dry-run
"$python_bin" manage.py verify_upgrade_readiness --strict --json

cd "$repo_root/frontend"
npm run test
npm run build:upgrade
npm run test:e2e:phase14

echo "Upgrade candidate gate passed for $repo_root"
