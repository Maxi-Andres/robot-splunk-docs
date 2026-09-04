"""
Tests for te_poller. They run on a workstation with no ThousandEyes account: every test
feeds a literal API response, so what is under test is the mapping, not the network.

The fixtures below are shaped after the documented NetworkTestResult schema (Cisco DevNet,
ThousandEyes API v7): agent{agentId,agentName,location}, date, roundId, loss, avgLatency,
jitter, serverIp. If a real `--probe` ever disagrees with this shape, fix the fixture FIRST
and let the test fail — that is the point of it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import te_poller

TEST = {"testId": 8675309, "testName": "go2-uplink", "type": "agent-to-server"}

RESULT = {
    "agent": {"agentId": "1234", "agentName": "go2-jetson-01", "location": "Buenos Aires"},
    "date": "2026-08-31T17:00:00Z",
    "roundId": 1788195600,
    "loss": 0.0,
    "avgLatency": 167.04,
    "jitter": 0.076808,
    "serverIp": "8.8.8.8",
}


def _fields(result, test=TEST):
    return json.loads(te_poller.envelope(result, test))["fields"]


def test_latency_is_converted_to_seconds():
    """The v7 API reports ms; OTel v2 reports seconds. The dashboard is written for OTel."""
    assert _fields(RESULT)["metric_name:network.latency"] == 167.04 / 1000.0


def test_loss_and_jitter_keep_their_api_units():
    """loss is already a percentage and jitter is already ms in BOTH models: no scaling."""
    f = _fields(RESULT)
    assert f["metric_name:network.loss"] == 0.0
    assert f["metric_name:network.jitter"] == 0.076808


def test_dimension_keys_match_the_otel_v2_exporter():
    """The whole point of the bridge: a panel cannot tell this apart from the real stream."""
    f = _fields(RESULT)
    assert f["thousandeyes.test.name"] == "go2-uplink"
    assert f["thousandeyes.source.agent.name"] == "go2-jetson-01"
    assert f["thousandeyes.source.agent.location"] == "Buenos Aires"
    assert f["server.address"] == "8.8.8.8"


def test_envelope_is_a_splunk_metric_event_in_the_metric_index():
    ev = json.loads(te_poller.envelope(RESULT, TEST))
    assert ev["event"] == "metric"
    assert ev["index"] == te_poller.INDEX
    assert ev["time"] == 1788195600


def test_zero_loss_is_emitted_not_dropped():
    """0% loss is a measurement, not a missing value. Truthiness checks lose it."""
    assert "metric_name:network.loss" in _fields(dict(RESULT, loss=0.0))


def test_absent_metric_is_omitted_never_zero_filled():
    """A fabricated zero averages into the panel and hides the real value."""
    partial = dict(RESULT)
    del partial["jitter"]
    f = _fields(partial)
    assert "metric_name:network.jitter" not in f
    assert "metric_name:network.loss" in f


def test_result_with_no_metrics_yields_no_event():
    bare = {"agent": RESULT["agent"], "roundId": 1788195600}
    assert te_poller.envelope(bare, TEST) is None


def test_roundid_watermark_suppresses_the_window_overlap(monkeypatch, capsys):
    """Windows overlap so a late round is never missed; the watermark stops double billing."""
    monkeypatch.setattr(te_poller, "list_network_tests", lambda _t: [TEST])
    monkeypatch.setattr(te_poller, "api_get", lambda *a, **k: {"results": [RESULT]})

    state = {}
    assert te_poller.poll_once("tok", state) == 1
    assert state["8675309/1234"] == 1788195600
    capsys.readouterr()

    assert te_poller.poll_once("tok", state) == 0, "same round must not be emitted twice"
    assert capsys.readouterr().out == ""


def test_a_newer_round_still_gets_through(monkeypatch, capsys):
    monkeypatch.setattr(te_poller, "list_network_tests", lambda _t: [TEST])
    monkeypatch.setattr(te_poller, "api_get", lambda *a, **k: {"results": [RESULT]})
    state = {"8675309/1234": RESULT["roundId"] - 60}
    assert te_poller.poll_once("tok", state) == 1
    assert capsys.readouterr().out.strip() != ""


def test_one_failing_test_does_not_abort_the_pass(monkeypatch, capsys):
    """A single bad test must not cost the whole cycle: the others still report."""
    good = dict(TEST, testId=1, testName="good")
    bad = dict(TEST, testId=2, testName="bad")
    monkeypatch.setattr(te_poller, "list_network_tests", lambda _t: [bad, good])

    def fake_get(path, _token, _params=None):
        if "/2/" in path:
            raise OSError("connection reset")
        return {"results": [RESULT]}

    monkeypatch.setattr(te_poller, "api_get", fake_get)
    assert te_poller.poll_once("tok", {}) == 1
    assert "connection reset" in capsys.readouterr().err


def test_output_is_one_json_object_per_line(monkeypatch, capsys):
    """hec_shipper reads NDJSON on stdin: a pretty-printed event would break the pipe."""
    monkeypatch.setattr(te_poller, "list_network_tests", lambda _t: [TEST])
    two = [RESULT, dict(RESULT, roundId=RESULT["roundId"] + 60)]
    monkeypatch.setattr(te_poller, "api_get", lambda *a, **k: {"results": two})
    te_poller.poll_once("tok", {})
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln]
    assert len(lines) == 2
    for ln in lines:
        assert json.loads(ln)["event"] == "metric"


def test_only_network_capable_tests_are_requested(monkeypatch):
    """Asking /network about an HTTP test returns 400 every cycle and buries real errors."""
    mixed = {
        "tests": [
            {"testId": 1, "type": "agent-to-server"},
            {"testId": 2, "type": "http-server"},
            {"testId": 3, "type": "agent-to-agent"},
            {"testId": 4, "type": "bgp"},
        ]
    }
    monkeypatch.setattr(te_poller, "api_get", lambda *a, **k: mixed)
    assert [t["testId"] for t in te_poller.list_network_tests("tok")] == [1, 3]


AGENTS = {
    "agents": [
        {"agentId": "562949953438687", "agentName": "go2-jetson-01", "agentState": "offline",
         "lastSeen": "2026-08-22T00:07:54Z", "hostname": "go2-jetson-01", "enabled": True,
         "location": "Buenos Aires, Argentina", "countryId": "AR", "agentType": "enterprise",
         "ipAddresses": ["172.17.0.2"], "publicIpAddresses": ["153.67.181.145"],
         "network": "SpaceX Starlink (AS 14593)"},
        {"agentId": "562949953426606", "agentName": "TE-ENTERPRISE-SILK", "agentState": "online",
         "lastSeen": "2026-09-04T12:00:00Z", "enabled": True, "agentType": "enterprise"},
    ]
}


def _agents(monkeypatch, only=""):
    monkeypatch.setattr(te_poller, "api_get", lambda *a, **k: AGENTS)
    monkeypatch.setattr(te_poller, "AGENTS", only)
    return [json.loads(x) for x in te_poller.agent_envelopes("tok")]


def test_agent_state_is_an_event_in_the_events_index(monkeypatch):
    """Agent state is inventory. A metrics index cannot hold an event, so it goes elsewhere."""
    ev = _agents(monkeypatch)[0]
    assert ev["index"] == te_poller.EVENT_INDEX
    assert ev["index"] != te_poller.INDEX, "would be rejected by Splunk with a 400"
    assert ev["sourcetype"] == "thousandeyes:agent"
    assert "event" in ev and "fields" not in ev


def test_offline_agent_is_reported_not_omitted(monkeypatch):
    """The whole point: when the robot is down its metrics stop, so the gap needs a reason."""
    go2 = next(e for e in _agents(monkeypatch) if e["event"]["agent_name"] == "go2-jetson-01")
    assert go2["event"]["agent_state"] == "offline"
    assert go2["event"]["online"] == 0
    assert go2["event"]["last_seen"] == "2026-08-22T00:07:54Z"
    assert go2["event"]["network"] == "SpaceX Starlink (AS 14593)"
    assert go2["event"]["public_ip"] == "153.67.181.145"


def test_online_flag_tracks_the_state_string(monkeypatch):
    """A single-value panel cannot colour on a string, so a 1/0 rides alongside it."""
    by = {e["event"]["agent_name"]: e["event"] for e in _agents(monkeypatch)}
    assert by["TE-ENTERPRISE-SILK"]["online"] == 1
    assert by["go2-jetson-01"]["online"] == 0


def test_agent_filter_selects_by_name(monkeypatch):
    """TE_AGENTS keeps the Go2 dashboard to the Go2 without filtering in every panel."""
    got = _agents(monkeypatch, only="go2-jetson-01")
    assert [e["event"]["agent_name"] for e in got] == ["go2-jetson-01"]


def test_empty_filter_means_every_agent(monkeypatch):
    assert len(_agents(monkeypatch, only="")) == 2


def test_agent_listing_failure_does_not_abort_the_pass(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("dns failure")
    monkeypatch.setattr(te_poller, "api_get", boom)
    monkeypatch.setattr(te_poller, "AGENTS", "")
    assert te_poller.agent_envelopes("tok") == []
    assert "cannot list agents" in capsys.readouterr().err


def test_broken_pipe_is_handled_by_the_entry_point(tmp_path):
    """`te_poller.py | head` must exit quietly, with no traceback.

    Reproduces the crash from the first real run: a closed downstream raised
    BrokenPipeError out of print() and dumped a traceback. In production the downstream is
    hec_shipper, so a dead shipper would have looked like a poller bug.

    No network and no token: the child IMPORTS te_poller and patches it, then calls
    main(). Running `te_poller.py` as a script instead would load it as __main__ — a
    different module object — and the patches would be silently ignored, which is what an
    earlier version of this test did.
    """
    import subprocess

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    driver = (
        "import sys, te_poller\n"
        f"te_poller.read_token = lambda: 'stub'\n"
        f"te_poller.list_network_tests = lambda _t: [{TEST!r}]\n"
        "te_poller.api_get = lambda *a, **k: {'results': ["
        f"dict({RESULT!r}, roundId={RESULT['roundId']} + i) for i in range(5000)]}}\n"
        "te_poller.main(['--once'])\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", driver],
        cwd=here, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=here, TE_STATE_FILE=str(tmp_path / "state.json")),
    )
    first = proc.stdout.readline()
    proc.stdout.close()               # this is the `| head -1` moment
    _, err = proc.communicate(timeout=60)

    assert json.loads(first)["event"] == "metric", "the stub never reached stdout"
    assert "BrokenPipeError" not in err, err
    assert "Traceback" not in err, err
    assert "downstream closed" in err, "the guard in main() did not run: " + err
