#!/usr/bin/env bash
set -euo pipefail

phase=${MITO_VERIFICATION_PHASE:?set MITO_VERIFICATION_PHASE to private or public}
case "$phase" in
  private|public) ;;
  *) echo "MITO_VERIFICATION_PHASE must be private or public" >&2; exit 2 ;;
esac

checkout=${MITO_PRODUCTION_ROOT:-/home/weidf/shenb/mito-data-agent-production-v1.1.0}
service_user=${MITO_PRODUCTION_USER:-mito-production-v11}
service_home=${MITO_PRODUCTION_HOME:-/home/mito-production-v11}
credentials=${MITO_PUBLIC_VERIFICATION_CREDENTIALS:-$checkout/run/.env.public-verification}
base_url=${MITO_VERIFICATION_BASE_URL:?set the private TLS or public HTTPS URL}
evidence_dir=${MITO_VERIFICATION_EVIDENCE_DIR:?set an unused evidence directory outside the repository}

sudo test -s "$credentials"
test -d "$checkout/frontend/node_modules"
test ! -e "$evidence_dir"
install -d -m 0700 "$evidence_dir"
runtime_results="$checkout/run/playwright-results-$phase-$$"
sudo test ! -e "$runtime_results"
sudo install -d -o "$service_user" -g "$service_user" -m 0700 \
  "$runtime_results"

set +e
sudo -u "$service_user" env HOME="$service_home" bash -c '
  set -euo pipefail
  set -a
  source "'$credentials'"
  set +a
  export MITO_STAGING_TEST_USERNAME="$MITO_PUBLIC_MANAGER_USERNAME"
  export MITO_STAGING_TEST_PASSWORD="$MITO_PUBLIC_MANAGER_PASSWORD"
  export MITO_STAGING_WORKFLOW_PROJECT_ID="$MITO_PUBLIC_PROJECT_ID"
  export MITO_STAGING_REGION_TASK_ID="$MITO_PUBLIC_PRIVATE_TASK_ID"
  export MITO_STAGING_WORKFLOW_ORGANIZATION="Release Operations v1.1.0"
  export MITO_STAGING_WORKFLOW_TEAM="Release Verification"
  export MITO_STAGING_WORKER_A_USERNAME="$MITO_PUBLIC_PRIVATE_ANNOTATOR_USERNAME"
  export MITO_STAGING_WORKER_A_PASSWORD="$MITO_PUBLIC_PRIVATE_ANNOTATOR_PASSWORD"
  export MITO_STAGING_WORKER_A_TASK_ID="$MITO_PUBLIC_PRIVATE_TASK_ID"
  export MITO_STAGING_WORKER_A_VOLUME_ID="$MITO_PUBLIC_PRIVATE_VOLUME_ID"
  export MITO_STAGING_BASE_URL="'$base_url'"
  export MITO_STAGING_EXPECT_CHUNK_RENDERER=0
  export MITO_STAGING_PLAYWRIGHT_OUTPUT_DIR="'$runtime_results'"
  cd "'$checkout'/frontend"

  if [[ "'$phase'" == private ]]; then
    export MITO_STAGING_ASSIGNEE_USERNAME="$MITO_PUBLIC_PRIVATE_ANNOTATOR_USERNAME"
    export MITO_STAGING_ASSIGNEE_PASSWORD="$MITO_PUBLIC_PRIVATE_ANNOTATOR_PASSWORD"
    export MITO_STAGING_ASSIGNED_TASK_ID="$MITO_PUBLIC_PRIVATE_TASK_ID"
    export MITO_STAGING_WORKER_B_USERNAME="$MITO_PUBLIC_PRIVATE_ANNOTATOR_USERNAME"
    export MITO_STAGING_WORKER_B_PASSWORD="$MITO_PUBLIC_PRIVATE_ANNOTATOR_PASSWORD"
    export MITO_STAGING_WORKER_B_TASK_ID="$MITO_PUBLIC_PRIVATE_TASK_ID"
    export MITO_STAGING_WORKER_B_VOLUME_ID="$MITO_PUBLIC_PRIVATE_VOLUME_ID"
    MITO_STAGING_INTEGRATED_WORKFLOWS=1 \
    MITO_STAGING_AI_WORKFLOWS=1 \
      npx playwright test --config=playwright.staging.config.ts --grep \
        "manager collaboration|integrated organization|full-task share|disabled chunk renderer|enabled EfficientSAM"
  else
    export MITO_STAGING_WORKER_B_USERNAME="$MITO_PUBLIC_PUBLIC_ANNOTATOR_USERNAME"
    export MITO_STAGING_WORKER_B_PASSWORD="$MITO_PUBLIC_PUBLIC_ANNOTATOR_PASSWORD"
    export MITO_STAGING_WORKER_B_TASK_ID="$MITO_PUBLIC_PUBLIC_TASK_ID"
    export MITO_STAGING_WORKER_B_VOLUME_ID="$MITO_PUBLIC_PUBLIC_VOLUME_ID"
    MITO_STAGING_PUBLIC_ASSIGNMENT=1 \
    MITO_STAGING_SOAK_WORKFLOWS=1 \
    MITO_STAGING_ALLOW_WRITES=1 \
    MITO_STAGING_AI_WORKFLOWS=1 \
    MITO_STAGING_FAILURE_WORKFLOWS=1 \
      npx playwright test --config=playwright.staging.config.ts --grep \
        "manager collaboration|reserved second annotator|full-task share|disabled chunk renderer|restored annotators|same-task stale|staging brush Save|autosave retries|enabled EfficientSAM"
  fi
' 2>&1 | tee "$evidence_dir/playwright.log"
status=${PIPESTATUS[0]}
set -e
sudo mv "$runtime_results" "$evidence_dir/playwright-results"
printf 'phase=%s\nbase_url=%s\nplaywright_status=%s\n' \
  "$phase" "$base_url" "$status" > "$evidence_dir/summary.txt"
exit "$status"
