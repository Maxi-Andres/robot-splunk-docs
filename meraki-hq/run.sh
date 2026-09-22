#!/usr/bin/env bash
# meraki_poller -> hec_shipper, the same pipe shape as te-poller next door and as the
# robot agent's run.sh.
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

# /services/collector/event, NOT /services/collector: everything this poller emits is an
# EVENT. te-poller uses the bare endpoint because it emits metrics; copying its URL here
# makes Splunk reject every batch with a 400 that reads like a token problem.
export HEC_URL="${HEC_URL:-https://localhost:8088/services/collector/event}"
export HEC_INDEX="${HEC_INDEX:-meraki}"
export ROBOT_NAME="${ROBOT_NAME:-meraki-poller}"
# 100 MB/day. Measured shape is ~8 MB/day for the six API families, so this is a ~12x
# runaway guard, not a budget. Syslog and webhooks do NOT pass through here -- they go
# straight to udp/5515 and the HEC, so this cap does not bound them. See README.md.
export DAILY_BYTE_CAP="${DAILY_BYTE_CAP:-104857600}"
export SPOOL_DIR="${SPOOL_DIR:-/var/tmp/meraki-poller-spool}"

if [[ -z "${HEC_TOKEN:-}" ]]; then
  TOKEN_FILE="${TOKEN_FILE:-$HOME/.splunk_hec_meraki_token}"
  [[ -f "$TOKEN_FILE" ]] || { echo "no HEC token: set HEC_TOKEN or write $TOKEN_FILE" >&2; exit 1; }
  HEC_TOKEN="$(cat "$TOKEN_FILE")"
  export HEC_TOKEN
fi

# python3 explicitly, never the exec bit: hec_shipper.py lives in another repo and is
# committed mode 644 there, so relying on it fails with a bare "Permission denied".
# "$@" is forwarded so `./run.sh --once` and `./run.sh --family uplink` behave the way the
# README says. Without it every invocation is the continuous loop, and a one-shot check
# quietly becomes a service you then have to find and kill.
exec python3 "$HERE/meraki_poller.py" "$@" | python3 "$SHIPPER"
