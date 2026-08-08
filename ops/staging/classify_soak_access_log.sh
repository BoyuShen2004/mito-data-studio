#!/usr/bin/env bash
set -euo pipefail

access_log=${1:?usage: classify_soak_access_log.sh ACCESS_LOG}
test -f "$access_log"

# Chromium can issue an opaque, credential-free duplicate while decoding a
# slice that was already fetched successfully with the token. It is security-
# preserving only when Django rejects it and the exact read-only URI has a 200
# peer in the same evidence window. Never classify mutation routes, arbitrary
# endpoints, malformed coordinates, or unpaired failures as benign.
awk '
  $9 == 200 { successful[$7] = 1 }
  $9 == 401 {
    rejected += 1
    uri = $7
    safe = ($6 == "\"GET" && uri ~ /^\/api\/volumes\/[0-9]+\/slice\/\?axis=[xyz]&index=[0-9]+$/)
    if (!safe) {
      unexpected += 1
    } else {
      safe_rejected[uri] += 1
    }
  }
  END {
    for (uri in safe_rejected) {
      if (successful[uri]) paired += safe_rejected[uri]
      else unexpected += safe_rejected[uri]
    }
    printf "rejected_credential_free_slice_probe_count=%d\n", paired + 0
    printf "unexpected_unauthorized_count=%d\n", unexpected + 0
    printf "total_unauthorized_count=%d\n", rejected + 0
  }
' "$access_log"
