"""Tests for meraki_poller.

Every test names the defect it catches, per the workspace engineering standard section 7.
No network: the API is stubbed at the `api_get` seam or at `urlopen`, never dialled.

The fixtures are built from Meraki's published v1 schema. If a real response from Silk's
own organisation disagrees, FIX THE FIXTURE FIRST and let the test fail -- that failure is
the whole point of `meraki_poller.py --probe`.
"""

import json
import os
import sys
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import meraki_poller as mp

NETS = {"N_1": "Silk HQ"}


def events(lines):
    return [json.loads(line) for line in lines]


# --- the unit trap -----------------------------------------------------------------------


def test_uplinkusage_bytes_pass_through_unconverted(monkeypatch):
    """Catches the 1000x throughput bug -- which this file originally got WRONG.

    The first version assumed Meraki reports kilobytes everywhere and multiplied by 1000.
    The throughput panel then read 7.5 Gbps on a 7.5 Mbps link: a smooth, believable line,
    wrong by three orders of magnitude.

    The fixture below is a REAL bucket from Silk's own organisation, captured 2026-09-22:
    193,764,098 over 300 s. Read as kilobytes that is 193 GB in five minutes; read as bytes
    it is 5.17 Mbps, which is what that link carries. Bytes it is.
    """
    monkeypatch.setattr(
        mp,
        "api_get",
        lambda path, key, params=None: [
            {
                "startTime": "2026-09-22T14:20:00Z",
                "endTime": "2026-09-22T14:25:00Z",
                "byInterface": [{"interface": "wan1", "sent": 25627500, "received": 168136598}],
            }
        ],
    )
    ev = events(mp.uplinkusage_events("k", NETS, {}))[0]["event"]
    assert ev["sentBytes"] == 25627500, "no conversion: the endpoint already reports bytes"
    assert ev["receivedBytes"] == 168136598
    # The number the panel actually renders. This is the assertion that matters.
    mbps = (ev["sentBytes"] + ev["receivedBytes"]) * 8 / 1000000 / ev["intervalSec"]
    assert 5.0 < mbps < 5.5, f"a WAN link reading {mbps:.0f} Mbps means the units are wrong"


def test_client_usage_stays_in_kilobytes(monkeypatch):
    """Catches the inverse of the bug above.

    The top-talkers panel divides usageSentKb by 1024 to show MB. Converting to bytes here
    to 'be consistent' with uplinkusage would inflate every client by 1000x.
    """
    monkeypatch.setattr(
        mp,
        "api_get",
        lambda path, key, params=None: [
            {"mac": "aa:bb", "usage": {"sent": 2048, "recv": 4096}, "ssid": "SILK"}
        ],
    )
    ev = events(mp.client_events("k", NETS))[0]["event"]
    assert ev["usageSentKb"] == 2048
    assert round(ev["usageRecvKb"] / 1024, 1) == 4.0


def test_interval_seconds_never_zero():
    """Catches a divide-by-zero that renders as an empty panel, not as an error.

    intervalSec is a denominator in the throughput panel. Meraki can return a bucket whose
    endTime equals its startTime, and an unparseable timestamp must not yield 0 either.
    """
    assert mp._interval_seconds("2026-09-22T10:00:00Z", "2026-09-22T10:00:00Z") == 300
    assert mp._interval_seconds("garbage", "also garbage") == 300
    assert mp._interval_seconds(None, None) == 300
    assert mp._interval_seconds("2026-09-22T10:00:00Z", "2026-09-22T10:05:00Z") == 300


# --- field-name contract with the dashboard ----------------------------------------------


@pytest.mark.parametrize(
    "client,expected",
    [
        ({"mac": "a", "recentDeviceConnection": "Wireless", "ssid": "SILK"}, "wireless"),
        ({"mac": "b", "recentDeviceConnection": "Wired", "ssid": None}, "wired"),
        # Field absent entirely: infer from ssid rather than dropping the client.
        ({"mac": "c", "ssid": "SILK"}, "wireless"),
        ({"mac": "d", "ssid": None}, "wired"),
    ],
)
def test_connection_type_is_lowercase(monkeypatch, client, expected):
    """Catches a silently empty 'clients by SSID' panel.

    That panel filters `connectionType=wireless`, lowercase. Meraki answers "Wireless"
    capitalised, which matches nothing and raises no error anywhere.
    """
    monkeypatch.setattr(mp, "api_get", lambda path, key, params=None: [client])
    assert events(mp.client_events("k", NETS))[0]["event"]["connectionType"] == expected


def test_unnamed_device_falls_back_to_serial(monkeypatch):
    """Catches the inventory table collapsing every unnamed device into one row.

    The panel does `stats ... by deviceName`. Meraki returns an empty `name` for a device
    nobody named, and every such device would group together under "".
    """
    monkeypatch.setattr(
        mp,
        "api_get",
        lambda path, key, params=None: [
            {"serial": "Q2XX-1", "name": "", "networkId": "N_1", "status": "online"},
            {"serial": "Q2XX-2", "name": "", "networkId": "N_1", "status": "online"},
        ],
    )
    names = {e["event"]["deviceName"] for e in events(mp.device_events("k", "O_1", NETS))}
    assert names == {"Q2XX-1", "Q2XX-2"}


def test_wireless_reports_worst_band_not_average(monkeypatch):
    """Catches congestion being averaged away.

    An AP with a saturated 2.4 GHz and an idle 5 GHz is congested. Emitting both bands lets
    the panel's avg() report a comfortable middle and hide exactly what it exists to show.
    """
    monkeypatch.setattr(
        mp,
        "api_get",
        lambda path, key, params=None: [
            {
                "serial": "Q2MR-1",
                "name": "AP-Recepcion",
                "network": {"id": "N_1"},
                "byBand": [
                    {"band": "2.4", "total": {"percentage": 91.0}},
                    {"band": "5", "total": {"percentage": 7.0}},
                ],
            }
        ],
    )
    out = events(mp.wireless_events("k", "O_1", NETS))
    assert len(out) == 1, "one event per AP, not one per band"
    assert out[0]["event"]["utilizationTotal"] == 91.0
    assert out[0]["event"]["band"] == "2.4"


def test_uplink_status_and_metrics_ride_one_event(monkeypatch):
    """Catches the WAN-latency KPI returning nothing.

    That panel is `status=active | stats latest(latencyMs)`. Status comes from one endpoint
    and latency from another; emitting them as separate events means the filter and the
    metric never appear on the same row, so the KPI is permanently blank.
    """

    def fake(path, key, params=None):
        if "uplink/statuses" in path:
            return [
                {
                    "networkId": "N_1",
                    "serial": "Q2MX-1",
                    "uplinks": [
                        {"interface": "wan1", "status": "active"},
                        {"interface": "wan2", "status": "ready"},
                    ],
                }
            ]
        return [
            {
                "serial": "Q2MX-1",
                "uplink": "wan1",
                "timeSeries": [
                    {"ts": "2026-09-22T10:00:00Z", "lossPercent": 0.0, "latencyMs": 11.0},
                    {"ts": "2026-09-22T10:01:00Z", "lossPercent": 2.5, "latencyMs": 42.0},
                ],
            }
        ]

    monkeypatch.setattr(mp, "api_get", fake)
    out = [e["event"] for e in events(mp.uplink_events("k", "O_1", NETS, {}))]
    active = [e for e in out if e["status"] == "active"]
    assert len(active) == 1
    assert active[0]["latencyMs"] == 42.0, "newest sample, not the first one seen"
    assert active[0]["lossPercent"] == 2.5
    assert active[0]["uplink"] == "wan1", "interface name is load-bearing: panel filters wan*"
    # wan2 has no series: it must still be emitted, just without the metrics.
    ready = next(e for e in out if e["status"] == "ready")
    assert "latencyMs" not in ready


def test_uplink_watermark_suppresses_a_repeated_sample(monkeypatch):
    """Catches the same reading being billed to the licence on every pass.

    The loss/latency window is polled at 2x the interval so a late point is never missed.
    That overlap re-returns points already emitted; without the watermark each one is
    indexed again and `latest()` keeps reporting a stale value as if it were fresh.
    """

    def fake(path, key, params=None):
        if "uplink/statuses" in path:
            return [
                {
                    "networkId": "N_1",
                    "serial": "S1",
                    "uplinks": [{"interface": "wan1", "status": "active"}],
                }
            ]
        return [
            {
                "serial": "S1",
                "uplink": "wan1",
                "timeSeries": [{"ts": "T1", "lossPercent": 1.0, "latencyMs": 5.0}],
            }
        ]

    monkeypatch.setattr(mp, "api_get", fake)
    state = {}
    first = [e["event"] for e in events(mp.uplink_events("k", "O_1", NETS, state))]
    second = [e["event"] for e in events(mp.uplink_events("k", "O_1", NETS, state))]
    assert first[0]["latencyMs"] == 5.0
    assert "latencyMs" not in second[0], "an already-emitted sample must not be re-sent"


# --- failure modes -----------------------------------------------------------------------


def test_a_failing_family_does_not_abort_the_others(monkeypatch):
    """Catches one dead endpoint blanking the whole dashboard.

    Traffic analytics being off 400s exactly one family. If that exception escapes, the
    pass dies and the five healthy families stop reporting too.
    """

    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(mp, "collect", boom)
    assert mp.run_family("apptraffic", "k", "O_1", NETS, {}, False) == 0


def test_multiple_orgs_without_config_exits_instead_of_guessing(monkeypatch):
    """Catches indexing the wrong customer.

    A key that sees several organisations and no MERAKI_ORG must fail closed. Picking the
    first is fail-open: it silently spends a shared licence on someone else's network.
    """
    monkeypatch.setattr(mp, "ORG_NAME", "")
    monkeypatch.setattr(mp, "ORG_ID", "")
    monkeypatch.setattr(
        mp, "api_get", lambda p, k, params=None: [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    )
    with pytest.raises(SystemExit):
        mp.resolve_org("k")


def test_single_org_is_resolved_without_config(monkeypatch):
    monkeypatch.setattr(mp, "ORG_NAME", "")
    monkeypatch.setattr(mp, "ORG_ID", "")
    monkeypatch.setattr(mp, "api_get", lambda p, k, params=None: [{"id": 7, "name": "Only"}])
    assert mp.resolve_org("k") == "7"


def test_429_is_retried_and_retry_after_is_capped(monkeypatch):
    """Catches the loop parking for an hour on a hostile header.

    Retry-After is trusted, but an upstream answering 3600 would silently stop the feed for
    an hour with nothing in the dashboard to explain the gap.
    """
    import urllib.error

    slept = []
    monkeypatch.setattr(mp.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(mp, "_throttle", lambda: None)

    calls = {"n": 0}

    class Resp:
        headers: ClassVar[dict] = {"Link": ""}

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                "u", 429, "Too Many Requests", {"Retry-After": "3600"}, None
            )
        return Resp()

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)
    body, _link = mp._get_once("https://api.meraki.com/api/v1/organizations", "k")
    assert body == {"ok": True}
    assert calls["n"] == 2, "the 429 must be retried, not raised"
    assert max(slept) <= 60.0, "Retry-After must be capped"


def test_pagination_is_bounded(monkeypatch):
    """Catches an unbounded Link chain holding the loop forever.

    A network that always advertises rel="next" would page until the process is killed.
    """
    monkeypatch.setattr(mp, "MAX_PAGES", 3)
    calls = {"n": 0}

    def fake_get_once(url, key):
        calls["n"] += 1
        return [{"i": calls["n"]}], "https://api.meraki.com/next"

    monkeypatch.setattr(mp, "_get_once", fake_get_once)
    out = mp.api_get("/whatever", "k")
    assert calls["n"] == 3
    assert len(out) == 3


def test_next_link_ignores_other_rels():
    """Catches paging backwards forever off a rel="prev"/"first" header."""
    header = (
        '<https://api.meraki.com/a?startingAfter=1>; rel="first", '
        '<https://api.meraki.com/a?endingBefore=9>; rel="prev", '
        '<https://api.meraki.com/a?startingAfter=9>; rel="next"'
    )
    assert mp._next_link(header) == "https://api.meraki.com/a?startingAfter=9"
    assert mp._next_link('<https://api.meraki.com/a>; rel="first"') is None
    assert mp._next_link("") is None


def test_api_key_is_never_printed(monkeypatch, capsys):
    """Catches a secret reaching the journal.

    The standard forbids echoing a secret even when it is malformed. A short key must be
    reported by LENGTH, never by value -- journald keeps it forever.
    """
    monkeypatch.setenv("MERAKI_API_KEY", "abc123-this-is-too-short")
    monkeypatch.setattr(mp, "KEY_FILE", "/nonexistent")
    key = mp.read_api_key()
    err = capsys.readouterr().err
    assert key == "abc123-this-is-too-short"
    assert "abc123" not in err
    assert "24 chars" in err


def test_missing_api_key_exits_with_instructions(monkeypatch):
    """Catches a silent no-op service: no key must stop the process, not poll nothing."""
    monkeypatch.delenv("MERAKI_API_KEY", raising=False)
    monkeypatch.setattr(mp, "KEY_FILE", "/nonexistent")
    with pytest.raises(SystemExit) as e:
        mp.read_api_key()
    assert "API access" in str(e.value)


def test_absent_numeric_field_stays_absent():
    """Catches a fabricated zero.

    Meraki omits a metric it does not have. Coercing that to 0.0 makes a dead uplink look
    like a perfect one: 0% loss and 0 ms latency is the best possible reading.
    """
    assert mp._f(None) is None
    assert mp._f("") is None
    assert mp._f("n/a") is None
    assert mp._f("42.5") == 42.5


def test_dry_run_writes_nothing_to_stdout(monkeypatch, capsys):
    """Catches --dry-run polluting a live Splunk.

    The flag exists to rehearse against production. If it still printed envelopes, the pipe
    into hec_shipper would index them.
    """
    monkeypatch.setattr(mp, "collect", lambda *a: ['{"event":"x"}'])
    n = mp.run_family("device", "k", "O_1", NETS, {}, True)
    assert n == 1
    assert capsys.readouterr().out == ""


# --- ceilings the real API enforces ------------------------------------------------------
# Both of these were found by running against Silk's own organisation, not by reading the
# docs. They 400 the whole family, so the panels they feed stay blank while every other
# panel works -- the hardest kind of failure to notice.


def test_loss_latency_timespan_respects_the_300s_ceiling(monkeypatch):
    """Catches HTTP 400 "'timespan' must be smaller than or equal to 300.0".

    The window is deliberately 2x the poll interval so a late sample is never missed. At the
    default 300 s interval that is 600, which this endpoint rejects outright -- and the loss
    and latency KPIs then stay blank with only a line in the journal to say why.
    """
    seen = {}

    def fake(path, key, params=None):
        if "uplinksLossAndLatency" in path:
            seen.update(params or {})
            return []
        return []

    monkeypatch.setattr(mp, "api_get", fake)
    monkeypatch.setattr(mp, "INTERVAL_UPLINK", 300.0)
    mp.uplink_events("k", "O_1", NETS, {})
    assert seen["timespan"] <= 300, "the API rejects anything larger"


def test_wireless_sends_an_interval_and_a_timespan_at_least_as_large(monkeypatch):
    """Catches HTTP 400 "Timespan being queried must be larger than interval".

    channelUtilization/byDevice defaults `interval` to 3600 when it is not sent, so any
    shorter timespan is rejected and the channel-utilisation panel stays empty.
    """
    seen = {}

    def fake(path, key, params=None):
        seen.update(params or {})
        return []

    monkeypatch.setattr(mp, "api_get", fake)
    mp.wireless_events("k", "O_1", NETS)
    assert "interval" in seen, "omitting interval makes the API default it to 3600"
    assert seen["timespan"] >= seen["interval"]


def test_ap_reporting_no_bands_is_skipped_not_emitted_as_zero(monkeypatch):
    """Catches a dormant AP being charted as 0% utilisation.

    The real organisation returns `byBand: []` for an AP that is not reporting. A zero there
    is indistinguishable from a genuinely idle radio, and it drags the panel's average down.
    """
    monkeypatch.setattr(
        mp,
        "api_get",
        lambda path, key, params=None: [
            {"serial": "Q2KD-1", "network": {"id": "N_1"}, "byBand": []},
            {
                "serial": "Q5AB-1",
                "network": {"id": "N_1"},
                "byBand": [{"band": "2.4", "total": {"percentage": 58.0}}],
            },
        ],
    )
    out = events(mp.wireless_events("k", "O_1", NETS))
    assert len(out) == 1, "the AP with no bands must be skipped entirely"
    assert out[0]["event"]["utilizationTotal"] == 58.0


def test_dry_run_does_not_persist_the_watermark(monkeypatch, tmp_path):
    """Catches a rehearsal silently eating the first real run.

    The watermarks advance inside the collectors, so a --dry-run that saves state makes the
    next real pass skip every sample the rehearsal already consumed. It happens once, leaves
    nothing in the log, and looks like the API simply had no data.
    """
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(mp, "STATE_FILE", str(state_file))
    monkeypatch.setattr(mp, "read_api_key", lambda: "k" * 40)
    monkeypatch.setattr(mp, "resolve_org", lambda key: "O_1")
    monkeypatch.setattr(mp, "resolve_networks", lambda key, org: NETS)
    monkeypatch.setattr(
        mp, "collect", lambda f, k, o, n, s: s.setdefault("uplink_ts", {}).update({"x": "T1"}) or []
    )

    mp.cli(["--once", "--dry-run"])
    assert not state_file.exists(), "a dry run must leave no watermark behind"

    mp.cli(["--once"])
    assert state_file.exists(), "a real run must persist it"
