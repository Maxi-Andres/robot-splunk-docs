#!/usr/bin/env bash
# te_poller -> hec_shipper, the same pipe shape as the robot agent's run.sh.
#
# The shipper lives in robot-telemetry-agent because that is where it was written; it is
# generic (stdin NDJSON -> HEC) and reusing it is the point. Clone that repo next to this
# one, or point SHIPPER at wherever it is.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIPPER="${SHIPPER:-$HERE/../../robot-telemetry-agent/shipper/hec_shipper.py}"

if [[ ! -f "$SHIPPER" ]]; then
  echo "shipper not found at $SHIPPER" >&2
  echo "clone robot-telemetry-agent beside robot-splunk-docs, or set SHIPPER=/path/to/hec_shipper.py" >&2
  exit 1
fi

# HEC_INDEX is what the shipper stamps on its OWN health events, and those are ordinary
# events. It must NOT be the metrics index: Splunk rejects an event in a metric index, so
# every health beat would 400 and the log would fill with failures that look like the
# poller's. The metric envelopes carry their own index and are unaffected by this.
export HEC_URL="${HEC_URL:-https://localhost:8088/services/collector}"
export HEC_INDEX="${HEC_INDEX:-thousandeyes_alerts}"
export ROBOT_NAME="${ROBOT_NAME:-te-poller}"
export DAILY_BYTE_CAP="${DAILY_BYTE_CAP:-$((20 * 1024 * 1024))}"
export SPOOL_DIR="${SPOOL_DIR:-/var/tmp/te-poller-spool}"

if [[ -z "${HEC_TOKEN:-}" ]]; then
  TOKEN_FILE="${TOKEN_FILE:-$HOME/.splunk_hec_te_token}"
  [[ -f "$TOKEN_FILE" ]] || { echo "no HEC token: set HEC_TOKEN or write $TOKEN_FILE" >&2; exit 1; }
  HEC_TOKEN="$(cat "$TOKEN_FILE")"
  export HEC_TOKEN
fi

# python3 explicitly, never the exec bit: hec_shipper.py lives in another repo and is
# committed mode 644 there, so relying on it fails with a bare "Permission denied".
# The agent's own run.sh does the same.
exec python3 "$HERE/te_poller.py" | python3 "$SHIPPER"
