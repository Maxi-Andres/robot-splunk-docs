#!/usr/bin/env bash
# Thin wrapper over the ThousandEyes v7 API for poking around from a terminal.
#
# It only adds the bearer header and pretty-prints. Everything else is the raw API, on
# purpose: what you see here is exactly what te_poller.py sees.
#
#   ./te-api.sh /tests                                  every test, raw
#   ./te-api.sh /tests | jq -r '.tests[] | "\(.testId)  \(.type)  \(.testName)"'
#   ./te-api.sh /test-results/<id>/network              loss / latency / jitter
#   ./te-api.sh /test-results/<id>/http-server          availability / response time
#   ./te-api.sh /test-results/<id>/network 'window=1h'  any query string
#
# Endpoints per test type: network, http-server, dns-server, dns-trace, path-vis,
# page-load, bgp, voice. A test only answers on the ones its type supports.
set -euo pipefail

TOKEN_FILE="${TE_TOKEN_FILE:-$HOME/.te_bearer_token}"
[[ -f "$TOKEN_FILE" ]] || { echo "no token at $TOKEN_FILE" >&2; exit 1; }

PATH_="${1:?usage: $0 <api-path> [query-string]}"
QS="${2:-}"
URL="https://api.thousandeyes.com/v7${PATH_}"
[[ -n "$QS" ]] && URL="${URL}?${QS}"

curl -sS --fail-with-body \
     -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
     -H "Accept: application/json" \
     "$URL" | jq .
