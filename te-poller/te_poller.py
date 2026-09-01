#!/usr/bin/env python3
"""
te_poller — polls the ThousandEyes v7 API for network test results and emits Splunk HEC
metric envelopes on stdout, one JSON per line.

It is a PRODUCER, not a sender. Chain it into the shipper that already exists:

    te_poller.py | ../../robot-telemetry-agent/shipper/hec_shipper.py

That reuses the spool, the batching, the retry backoff and the daily byte cap instead of
growing a second copy of each. This process only knows how to ask ThousandEyes a question
and shape the answer.

WHY THIS EXISTS AT ALL — read before extending it:
    This is a BRIDGE. The supported way to get ThousandEyes data into Splunk is a streaming
    integration, where ThousandEyes pushes OpenTelemetry straight to the HEC. That path is
    blocked here only because it needs a publicly reachable endpoint (TCP 443, DNS name,
    certificate from a public CA) and this Splunk is on a private address with a self-signed
    certificate. The moment that infrastructure exists, this poller is deleted, not extended.
    The migration plan is `../LICENCIA-Y-THOUSANDEYES.md` §5.

So it is deliberately built to be thrown away, and that shaped two decisions:

  * The metric names and dimension keys are EXACTLY the ones ThousandEyes' own OpenTelemetry
    Data Model v2 exporter would produce. Same index, same names, same units. The dashboard
    panels cannot tell the two apart, so the cutover is: stop this, start the stream. No
    dashboard edit, no re-learning of field names, no parallel set of panels.
  * Latency is converted from milliseconds to SECONDS. The v7 API reports `avgLatency` in ms;
    OTel v2 reports `network.latency` in seconds. Matching OTel — not the API we read — is
    what makes the two paths interchangeable. Getting this backwards makes every latency
    panel wrong by 1000x, and it will look plausible.

Standard library only: nothing has to be installed on the Splunk box, and py38 syntax so it
also runs unchanged on the robot's Jetson if it ever has to.
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("TE_API_BASE", "https://api.thousandeyes.com/v7")
TOKEN_FILE = os.environ.get("TE_TOKEN_FILE", os.path.expanduser("~/.te_bearer_token"))
AID = os.environ.get("TE_ACCOUNT_GROUP_ID", "")
INDEX = os.environ.get("TE_METRIC_INDEX", "thousandeyes")
SOURCETYPE = os.environ.get("TE_SOURCETYPE", "thousandeyes:otel")
SOURCE = os.environ.get("TE_SOURCE", "thousandeyes:api")
INTERVAL = float(os.environ.get("TE_POLL_INTERVAL_S", "300"))
WINDOW = os.environ.get("TE_WINDOW", "10m")
STATE_FILE = os.environ.get("TE_STATE_FILE", "/var/tmp/te-poller-state.json")
TIMEOUT = float(os.environ.get("TE_HTTP_TIMEOUT", "20"))

# Test types that answer on /test-results/{id}/network. Anything else is skipped rather
# than requested: a 400 per test per cycle is noise that hides real failures.
NETWORK_TEST_TYPES = frozenset({"agent-to-server", "agent-to-agent"})

_ctx = ssl.create_default_context()     # api.thousandeyes.com is publicly trusted: verify.


def log(msg):
    print(f"[te-poller] {msg}", file=sys.stderr, flush=True)


def read_token():
    tok = os.environ.get("TE_BEARER_TOKEN", "")
    if not tok and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as fh:
            tok = fh.read()
    tok = tok.strip()
    if not tok:
        sys.exit(
            "No ThousandEyes bearer token. Create one at Account Settings -> Users and "
            "Roles -> Profile -> User API Tokens (it is shown ONCE), then:\n"
            f"  printf '%s' 'YOUR-TOKEN' > {TOKEN_FILE}\n  chmod 600 {TOKEN_FILE}"
        )
    return tok


def api_get(path, token, params=None):
    query = dict(params or {})
    if AID:
        query["aid"] = AID
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state():
    """Last roundId already emitted, keyed by "testId/agentId"."""
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = f"{STATE_FILE}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        # Losing the state file re-emits one window of points. Splunk metrics are keyed by
        # timestamp + dimensions, so duplicates collapse; it costs licence, not correctness.
        log(f"could not persist state to {STATE_FILE}: {e}")


def envelope(result, test):
    """One HEC metric event, shaped exactly like the OTel v2 exporter's output."""
    agent = result.get("agent") or {}
    fields = {
        "thousandeyes.test.id": str(test.get("testId", "")),
        "thousandeyes.test.name": test.get("testName", ""),
        "thousandeyes.test.type": test.get("type", ""),
        "thousandeyes.source.agent.id": str(agent.get("agentId", "")),
        "thousandeyes.source.agent.name": agent.get("agentName", ""),
        "thousandeyes.source.agent.location": agent.get("location", ""),
    }
    if result.get("serverIp"):
        fields["server.address"] = result["serverIp"]

    # Only metrics actually present are emitted. A test that reports loss but not jitter
    # must not publish jitter=0 — a fabricated zero is worse than a gap, because it averages
    # into the panel and hides the real value.
    if result.get("loss") is not None:
        fields["metric_name:network.loss"] = float(result["loss"])
    if result.get("avgLatency") is not None:
        fields["metric_name:network.latency"] = float(result["avgLatency"]) / 1000.0
    if result.get("jitter") is not None:
        fields["metric_name:network.jitter"] = float(result["jitter"])

    if not any(k.startswith("metric_name:") for k in fields):
        return None

    ev = {
        "time": int(result.get("roundId") or time.time()),
        "event": "metric",
        "source": SOURCE,
        "sourcetype": SOURCETYPE,
        "index": INDEX,
        "fields": fields,
    }
    return json.dumps(ev, separators=(",", ":"))


def list_network_tests(token):
    data = api_get("/tests", token)
    out = []
    for t in data.get("tests", []):
        if t.get("type") in NETWORK_TEST_TYPES:
            out.append(t)
    return out


def poll_once(token, state):
    """One full pass. Returns how many points were written to stdout."""
    try:
        tests = list_network_tests(token)
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace")
        log(f"cannot list tests: HTTP {e.code} {detail}")
        return 0
    except OSError as e:
        log(f"cannot list tests: {e}")
        return 0

    if not tests:
        log("no agent-to-server or agent-to-agent tests in this account group")
        return 0

    written = 0
    for test in tests:
        tid = test.get("testId")
        try:
            data = api_get(
                f"/test-results/{tid}/network", token, {"window": WINDOW}
            )
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "replace")
            log(f"test {tid}: HTTP {e.code} {detail}")
            continue
        except OSError as e:
            log(f"test {tid}: {e}")
            continue

        for result in data.get("results", []):
            agent = result.get("agent") or {}
            key = "{}/{}".format(tid, agent.get("agentId", "?"))
            round_id = int(result.get("roundId") or 0)
            # Windows overlap on purpose so a late round is never missed; the roundId
            # watermark is what stops the overlap from being re-billed to the licence.
            if round_id and round_id <= state.get(key, 0):
                continue
            line = envelope(result, test)
            if line is None:
                continue
            print(line, flush=True)
            written += 1
            if round_id:
                state[key] = round_id
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="print the raw API response for the first network test and exit. Use this "
        "FIRST, against the real account: it is how you confirm the field names this "
        "code maps before trusting a single panel.",
    )
    args = ap.parse_args()
    token = read_token()

    if args.probe:
        tests = list_network_tests(token)
        if not tests:
            sys.exit("no agent-to-server or agent-to-agent tests found")
        tid = tests[0].get("testId")
        log("probing test {} ({})".format(tid, tests[0].get("testName", "")))
        data = api_get(f"/test-results/{tid}/network", token, {"window": WINDOW})
        print(json.dumps(data, indent=2)[:8000])
        return

    state = load_state()
    log(f"up: base={API_BASE} interval={INTERVAL}s window={WINDOW} index={INDEX}")
    while True:
        n = poll_once(token, state)
        save_state(state)
        log(f"pass complete: {n} point(s)")
        if args.once:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("interrupted")
    except BrokenPipeError:
        # Downstream closed: `| head`, or hec_shipper died. Exiting quietly is correct —
        # the shipper owns delivery, and systemd's Restart=always brings the pair back.
        # The dup2 is not decoration: without it the interpreter tries to flush stdout at
        # shutdown, fails again, and prints "Exception ignored" AFTER this handler ran.
        log("downstream closed the pipe, exiting")
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
