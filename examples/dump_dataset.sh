#!/usr/bin/env bash
set -euo pipefail

DATASET="${1:-/Muon/Run2024C-PromptReco-v1/MINIAOD}"
OUTPUT_DIR="${2:-output}"

if ! command -v dbs2go-json >/dev/null 2>&1; then
  echo "dbs2go-json is not installed in the active environment" >&2
  exit 1
fi

if [[ -z "${X509_USER_PROXY:-}" ]] && command -v voms-proxy-info >/dev/null 2>&1; then
  export X509_USER_PROXY="$(voms-proxy-info -path)"
fi

dbs2go-json dump \
  "$DATASET" \
  --instance global \
  --output "$OUTPUT_DIR" \
  --workers 4
