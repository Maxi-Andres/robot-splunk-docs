#!/usr/bin/env python3
"""
meraki_poller — polls the Meraki Dashboard API v1 and emits Splunk HEC event envelopes on
stdout, one JSON per line.

It is a PRODUCER, not a sender. Chain it into the shipper that already exists:

    meraki_poller.py | ../../robot-telemetry-agent/shipper/hec_shipper.py

Same shape as te-poller next door, and for the same reason: the shipper already owns the
disk spool, the batching, the retry backoff and the daily byte cap. Growing a second copy
of any of that is how the two drift. This process only asks Meraki a question and shapes
the answer.

WHAT IT FEEDS
    The `Silk HQ - Meraki` dashboard (view `silk_hq_meraki`): 21 searches over 8
    sourcetypes. SIX come from this poller. The other two, `meraki:syslog` and
    `meraki:webhook`, are pushed by the Meraki cloud straight at the HEC and never touch
    this code -- if those panels are empty, this poller is not the thing to debug.

THE FIELD NAMES ARE A CONTRACT
    Every field below is read BY NAME in a dashboard panel. A rename here is a blank panel
    there, with no error raised anywhere and nothing in any log. README.md carries the
    field-to-panel table; when you add a field, add the row.

THE UNIT TRAP -- read before touching any usage number
    Meraki uses DIFFERENT units on different usage endpoints, and its published schema does
    not say so. Measured against the real API, not read from the docs:
      * `meraki:api:client` and `:apptraffic` are KILOBYTES; those panels divide by 1024,
        so they pass through untouched.
      * `meraki:api:uplinkusage` is already BYTES. The throughput panel computes
        (sentBytes+receivedBytes)*8/1000000/intervalSec, which is Mbps only for bytes, so
        it also passes through untouched.
    The first version of this file assumed kilobytes everywhere and multiplied uplinkusage
    by 1000. The throughput panel then read 7.5 Gbps on a 7.5 Mbps link -- a smooth,
    entirely plausible-looking line, wrong by three orders of magnitude. That is the whole
    reason --probe exists: confirm units against the account, never against the docs.

WHY EVERY EMITTER IS DEFENSIVE
    `--probe` exists because this mapping was built from Meraki's published schema, not
    from Silk's own organisation. Confirm the shape against the real account before
    trusting a panel: a field Meraki omits for a given licence tier or device model must
    leave the panel blank, never crash the pass and take the other five families with it.

Standard library only, Python 3.8 syntax: nothing to install on the Splunk box.
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

API_BASE = os.environ.get("MERAKI_API_BASE", "https://api.meraki.com/api/v1")
KEY_FILE = os.environ.get("MERAKI_API_KEY_FILE", os.path.expanduser("~/.meraki_api_key"))
ORG_NAME = os.environ.get("MERAKI_ORG", "")
ORG_ID = os.environ.get("MERAKI_ORG_ID", "")
# Comma-separated network names. EMPTY MEANS EVERY NETWORK IN THE ORG, which on a large
# organisation is a lot of licence to spend by accident, so it is logged loudly at start.
NETWORKS = os.environ.get("MERAKI_NETWORKS", "")
INDEX = os.environ.get("MERAKI_INDEX", "meraki")
SOURCE = os.environ.get("MERAKI_SOURCE", "meraki:api")
STATE_FILE = os.environ.get("MERAKI_STATE_FILE", "/var/tmp/meraki-poller-state.json")
TIMEOUT = float(os.environ.get("MERAKI_HTTP_TIMEOUT", "30"))

# Cadence per family, in seconds. These are the numbers from the integration report:
# device/uplink/wireless every 5 min, clients every 15, application traffic hourly.
# They are not arbitrary -- see README.md "Why these intervals".
INTERVAL_DEVICE = float(os.environ.get("MERAKI_INTERVAL_DEVICE_S", "300"))
INTERVAL_UPLINK = float(os.environ.get("MERAKI_INTERVAL_UPLINK_S", "300"))
INTERVAL_WIRELESS = float(os.environ.get("MERAKI_INTERVAL_WIRELESS_S", "300"))
INTERVAL_CLIENT = float(os.environ.get("MERAKI_INTERVAL_CLIENT_S", "900"))
INTERVAL_APPTRAFFIC = float(os.environ.get("MERAKI_INTERVAL_APPTRAFFIC_S", "3600"))

# Meraki's documented limit is 5 requests/second per organisation, enforced with 429 plus a
# Retry-After header. We pace ourselves BELOW it rather than discovering it: a 429 storm on
# a shared org key would degrade anything else using the same key, not just this poller.
MAX_RPS = float(os.environ.get("MERAKI_MAX_RPS", "4"))
MAX_RETRIES = int(os.environ.get("MERAKI_MAX_RETRIES", "4"))
# Bound every input (engineering standard section 2). A runaway Link chain on a big network
# is the difference between one pass and an unbounded one holding the whole loop.
MAX_PAGES = int(os.environ.get("MERAKI_MAX_PAGES", "20"))
CLIENT_PER_PAGE = int(os.environ.get("MERAKI_CLIENT_PER_PAGE", "1000"))

# Meraki is NOT consistent about usage units across endpoints, and the published schema is
# not a reliable guide. Measured against the real organisation on 2026-09-22:
#   * /networks/{id}/clients          -> usage.sent/recv in KILOBYTES
#     (a laptop showed 37 MB over an hour read as KB; as bytes it would be 40 KB)
#   * /networks/{id}/traffic          -> sent/recv in KILOBYTES
#   * /networks/{id}/appliance/uplinks/usageHistory -> sent/received in BYTES
#     (a 5-minute bucket showed 193,764,098; as KB that is 193 GB in five minutes, as bytes
#     it is 5.17 Mbps, which is what that link actually carries)
# So uplinkusage needs NO conversion, and adding one was a real 1000x defect in the
# throughput panel. See THE UNIT TRAP above.

# Two hard ceilings the API enforces and answers 400 for. Both were found by running
# against the real organisation, not by reading the docs -- see README "Confirm the schema".
#   * uplinksLossAndLatency refuses a timespan over 300 s outright.
#   * channelUtilization/byDevice requires an explicit `interval` and a timespan at least
#     as large as it; left out, it defaults to interval=3600 and 400s anything shorter.
LOSS_LATENCY_MAX_TIMESPAN = 300
WIRELESS_INTERVAL = int(os.environ.get("MERAKI_WIRELESS_INTERVAL_S", "300"))

# api.meraki.com is a publicly trusted certificate: verify it. There is no lab-cert excuse
# here -- this leg leaves the building.
_ctx = ssl.create_default_context()

_last_request_at = 0.0


def log(msg):
    print(f"[meraki-poller] {msg}", file=sys.stderr, flush=True)


def read_api_key():
    """Env first, then a mode-600 file. Never logged, not even malformed."""
    key = os.environ.get("MERAKI_API_KEY", "")
    if not key and os.path.exists(KEY_FILE):
        with open(KEY_FILE) as fh:
            key = fh.read()
    key = key.strip()
    if not key:
        sys.exit(
            "No Meraki API key. Generate a READ-ONLY one at Meraki Dashboard -> My "
            "Profile -> API access -> Generate new API key (shown ONCE), then:\n"
            f"  printf '%s' 'YOUR-KEY' > {KEY_FILE}\n  chmod 600 {KEY_FILE}"
        )
    # Shape only. The standard is explicit: never echo a secret, report length and shape.
    # Meraki keys are 40 hex characters; saying so beats a 401 with no explanation.
    if len(key) != 40:
        log(f"warning: API key is {len(key)} chars, expected 40 -- check for a truncated paste")
    return key


def _throttle():
    global _last_request_at
    gap = 1.0 / MAX_RPS if MAX_RPS > 0 else 0.0
    wait = gap - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def api_get(path, key, params=None, _page_budget=None):
    """GET with pacing, 429 backoff and bounded Link pagination.

    Returns the decoded body. When the endpoint paginates, the pages are concatenated --
    every paginating endpoint we touch returns a list.
    """
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    budget = MAX_PAGES if _page_budget is None else _page_budget
    out = None
    while url and budget > 0:
        budget -= 1
        body, link = _get_once(url, key)
        if out is None:
            out = body
        elif isinstance(out, list) and isinstance(body, list):
            out.extend(body)
        else:
            break
        url = link
    if url and budget <= 0:
        log(f"pagination stopped at MERAKI_MAX_PAGES={MAX_PAGES} for {path} -- results are partial")
    return out


def _get_once(url, key):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "silk-meraki-poller/1.0",
        },
    )
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload, _next_link(resp.headers.get("Link", ""))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Retry-After is seconds. Trust it over a guess, but cap it: a header of
                # 3600 would silently park the whole loop for an hour.
                delay = min(float(e.headers.get("Retry-After", "1") or 1), 60.0)
                log(
                    f"429 rate limited, sleeping {delay:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            if attempt == MAX_RETRIES - 1:
                raise
            backoff = 2.0**attempt
            log(f"request failed ({type(e).__name__}), retrying in {backoff:.0f}s")
            time.sleep(backoff)
    raise RuntimeError(f"exhausted {MAX_RETRIES} retries")


def _next_link(header):
    """Extract rel="next" from an RFC 5988 Link header. Returns None when there is none."""
    for part in header.split(","):
        bits = part.split(";")
        if len(bits) < 2:
            continue
        target = bits[0].strip()
        if 'rel="next"' in part and target.startswith("<") and target.endswith(">"):
            return target[1:-1]
    return None


def envelope(sourcetype, host, event, ts=None):
    ev = {
        "time": round(ts if ts is not None else time.time(), 3),
        "source": SOURCE,
        "sourcetype": sourcetype,
        "index": INDEX,
        "host": host or "meraki",
        "event": event,
    }
    return json.dumps(ev, separators=(",", ":"))


def load_state():
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
        log(f"cannot persist state to {STATE_FILE}: {e}")


def resolve_org(key):
    if ORG_ID:
        return ORG_ID
    orgs = api_get("/organizations", key) or []
    if ORG_NAME:
        for o in orgs:
            if o.get("name") == ORG_NAME:
                return str(o.get("id"))
        sys.exit(
            "organisation {!r} not visible to this key. Visible: {}".format(
                ORG_NAME, ", ".join(repr(o.get("name")) for o in orgs) or "none"
            )
        )
    if len(orgs) == 1:
        return str(orgs[0].get("id"))
    # Fail safe, not fail open: picking one at random would silently index the wrong
    # customer's network into a shared licence.
    sys.exit(
        "this key sees {} organisations; set MERAKI_ORG or MERAKI_ORG_ID. Visible: {}".format(
            len(orgs), ", ".join(repr(o.get("name")) for o in orgs)
        )
    )


def resolve_networks(key, org_id):
    """Returns {networkId: networkName} for the networks we are allowed to poll."""
    nets = api_get(f"/organizations/{org_id}/networks", key) or []
    wanted = {n.strip() for n in NETWORKS.split(",") if n.strip()}
    out = {}
    for n in nets:
        name = n.get("name", "")
        if wanted and name not in wanted:
            continue
        out[str(n.get("id"))] = name
    if wanted:
        missing = wanted - set(out.values())
        if missing:
            log("configured network(s) not found in the org: {}".format(", ".join(sorted(missing))))
    else:
        log(f"MERAKI_NETWORKS is empty: polling ALL {len(out)} network(s) in the org")
    return out


def _f(value):
    """Meraki omits numeric fields rather than sending null. Absent stays absent."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def device_events(key, org_id, nets):
    """sourcetype=meraki:api:device -- panels: online KPI, down KPI, inventory table.

    Two endpoints merged on serial: statuses carries status/lanIp/publicIp, the device
    list carries firmware. The inventory panel shows both, so neither alone is enough.
    """
    statuses = api_get(f"/organizations/{org_id}/devices/statuses", key) or []
    try:
        details = api_get(f"/organizations/{org_id}/devices", key) or []
    except urllib.error.HTTPError as e:
        log(f"device detail unavailable (HTTP {e.code}), firmware column will be blank")
        details = []
    firmware = {d.get("serial"): d.get("firmware", "") for d in details}
    names = {d.get("serial"): d.get("name", "") for d in details}

    out = []
    for d in statuses:
        net_id = str(d.get("networkId", ""))
        if net_id not in nets:
            continue
        serial = d.get("serial", "")
        # deviceName: Meraki leaves `name` empty on an unnamed device, and the inventory
        # panel groups BY deviceName -- an empty one collapses every unnamed device into a
        # single row. Falling back to the serial keeps them distinct.
        name = d.get("name") or names.get(serial) or serial
        out.append(
            envelope(
                "meraki:api:device",
                name,
                {
                    "networkName": nets[net_id],
                    "networkId": net_id,
                    "serial": serial,
                    "deviceName": name,
                    "status": d.get("status", "unknown"),
                    "productType": d.get("productType", ""),
                    "model": d.get("model", ""),
                    "lanIp": d.get("lanIp", ""),
                    "publicIp": d.get("publicIp", ""),
                    "firmware": firmware.get(serial, ""),
                },
            )
        )
    return out


def uplink_events(key, org_id, nets, state):
    """sourcetype=meraki:api:uplink -- panels: active uplink, WAN loss, WAN latency, failover.

    status comes from the appliance uplink statuses; lossPercent/latencyMs come from a
    separate time series. They are emitted as ONE event per (network, uplink) because the
    panels correlate them: `status=active | stats latest(latencyMs)` only works if a single
    event carries both.
    """
    try:
        statuses = api_get(f"/organizations/{org_id}/appliance/uplink/statuses", key) or []
    except urllib.error.HTTPError as e:
        log(f"appliance uplink statuses unavailable (HTTP {e.code}): no MX in this org?")
        statuses = []
    try:
        series = (
            api_get(
                f"/organizations/{org_id}/devices/uplinksLossAndLatency",
                key,
                # Capped, not INTERVAL_UPLINK*2: the endpoint 400s above 300 s with
                # "'timespan' must be smaller than or equal to 300.0".
                {"timespan": min(int(INTERVAL_UPLINK * 2), LOSS_LATENCY_MAX_TIMESPAN)},
            )
            or []
        )
    except urllib.error.HTTPError as e:
        log(f"uplink loss/latency unavailable (HTTP {e.code}), loss and latency panels stay blank")
        series = []

    # Latest sample per (serial, uplink), and a watermark so a re-poll of an overlapping
    # window does not bill the same point twice.
    latest = {}
    seen = state.setdefault("uplink_ts", {})
    for row in series:
        serial, uplink = row.get("serial", ""), row.get("uplink", "")
        points = row.get("timeSeries") or []
        newest = None
        for p in points:
            if p.get("lossPercent") is None and p.get("latencyMs") is None:
                continue
            if newest is None or str(p.get("ts", "")) > str(newest.get("ts", "")):
                newest = p
        if newest is None:
            continue
        mark = f"{serial}/{uplink}"
        if seen.get(mark) == newest.get("ts"):
            continue
        seen[mark] = newest.get("ts")
        latest[mark] = newest

    out = []
    for s in statuses:
        net_id = str(s.get("networkId", ""))
        if net_id not in nets:
            continue
        serial = s.get("serial", "")
        for u in s.get("uplinks") or []:
            # `interface` is wan1/wan2/cellular. The loss panel filters uplink=wan*, so the
            # interface name is load-bearing -- do not normalise or prettify it.
            iface = u.get("interface", "")
            point = latest.get(f"{serial}/{iface}") or {}
            event = {
                "networkName": nets[net_id],
                "networkId": net_id,
                "serial": serial,
                "uplink": iface,
                "status": u.get("status", "unknown"),
                "ip": u.get("ip", ""),
                "gateway": u.get("gateway", ""),
                "publicIp": u.get("publicIp", ""),
            }
            loss, lat = _f(point.get("lossPercent")), _f(point.get("latencyMs"))
            if loss is not None:
                event["lossPercent"] = loss
            if lat is not None:
                event["latencyMs"] = lat
            out.append(envelope("meraki:api:uplink", serial or nets[net_id], event))
    return out


def uplinkusage_events(key, nets, state):
    """sourcetype=meraki:api:uplinkusage -- panel: throughput per uplink.

    BYTES IN, BYTES OUT, unconverted. This endpoint reports bytes even though the sibling
    usage endpoints report kilobytes; converting here is a 1000x error that still plots a
    smooth, believable line. Verified against the live API -- see the units note above.
    """
    out = []
    seen = state.setdefault("usage_ts", {})
    for net_id, net_name in nets.items():
        try:
            hist = (
                api_get(
                    f"/networks/{net_id}/appliance/uplinks/usageHistory",
                    key,
                    {"timespan": int(INTERVAL_UPLINK * 4), "resolution": 300},
                )
                or []
            )
        except urllib.error.HTTPError as e:
            if e.code not in (400, 404):
                log(f"uplink usage for {net_name} failed: HTTP {e.code}")
            continue
        for bucket in hist:
            start, end = bucket.get("startTime", ""), bucket.get("endTime", "")
            mark = f"{net_id}"
            if seen.get(mark) and str(start) <= str(seen[mark]):
                continue
            ts = _epoch(end) or _epoch(start) or time.time()
            interval = _interval_seconds(start, end)
            for iface in bucket.get("byInterface") or []:
                sent_b, recv_b = _f(iface.get("sent")) or 0.0, _f(iface.get("received")) or 0.0
                out.append(
                    envelope(
                        "meraki:api:uplinkusage",
                        net_name,
                        {
                            "networkName": net_name,
                            "networkId": net_id,
                            "uplink": iface.get("interface", ""),
                            # Already bytes. Do NOT multiply: see the units note above.
                            "sentBytes": sent_b,
                            "receivedBytes": recv_b,
                            "intervalSec": interval,
                        },
                        ts,
                    )
                )
            seen[mark] = start
    return out


def _epoch(iso):
    """Meraki timestamps are ISO-8601 with a Z. Returns None on anything unexpected."""
    if not iso:
        return None
    try:
        import datetime

        return datetime.datetime.strptime(
            str(iso).replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z"
        ).timestamp()
    except (ValueError, TypeError):
        return None


def _interval_seconds(start, end):
    a, b = _epoch(start), _epoch(end)
    # 300 matches the `resolution` we request. A zero would make the panel divide by zero
    # and render nothing, so the fallback is the requested resolution, never 0.
    if a is None or b is None or b <= a:
        return 300
    return b - a


def client_events(key, nets):
    """sourcetype=meraki:api:client -- panels: clients by SSID, clients per AP, top talkers.

    Usage stays in KILOBYTES: the panels divide by 1024 themselves.
    """
    out = []
    for net_id, net_name in nets.items():
        try:
            clients = (
                api_get(
                    f"/networks/{net_id}/clients",
                    key,
                    {"timespan": int(INTERVAL_CLIENT * 2), "perPage": CLIENT_PER_PAGE},
                )
                or []
            )
        except urllib.error.HTTPError as e:
            log(f"clients for {net_name} failed: HTTP {e.code}")
            continue
        for c in clients:
            usage = c.get("usage") or {}
            ssid = c.get("ssid") or ""
            # The wireless-clients panel filters connectionType=wireless, lowercase. Meraki
            # returns "Wireless"/"Wired" in recentDeviceConnection, so normalising here is
            # what makes that panel match at all.
            conn = (c.get("recentDeviceConnection") or "").lower()
            if not conn:
                conn = "wireless" if ssid else "wired"
            out.append(
                envelope(
                    "meraki:api:client",
                    net_name,
                    {
                        "networkName": net_name,
                        "networkId": net_id,
                        "mac": c.get("mac", ""),
                        "description": c.get("description") or c.get("mac", ""),
                        "ip": c.get("ip") or "",
                        "ssid": ssid,
                        "connectionType": conn,
                        "manufacturer": c.get("manufacturer") or "",
                        "recentDeviceName": c.get("recentDeviceName") or "",
                        "usageSentKb": _f(usage.get("sent")) or 0.0,
                        "usageRecvKb": _f(usage.get("recv")) or 0.0,
                    },
                )
            )
    return out


def apptraffic_events(key, nets):
    """sourcetype=meraki:api:apptraffic -- panels: top applications, traffic by protocol/port.

    Needs Traffic analytics with hostname detail enabled per network (Network-wide ->
    General -> Traffic analysis). Without it Meraki answers 400, and these two panels stay
    empty while everything else works -- which is the confusing part, so it is logged.
    """
    out = []
    for net_id, net_name in nets.items():
        try:
            rows = (
                api_get(
                    f"/networks/{net_id}/traffic",
                    key,
                    {"timespan": int(INTERVAL_APPTRAFFIC)},
                )
                or []
            )
        except urllib.error.HTTPError as e:
            if e.code == 400:
                log(
                    f"traffic analytics is OFF for {net_name} -- the application panels will stay empty"
                )
            else:
                log(f"app traffic for {net_name} failed: HTTP {e.code}")
            continue
        for r in rows:
            out.append(
                envelope(
                    "meraki:api:apptraffic",
                    net_name,
                    {
                        "networkName": net_name,
                        "networkId": net_id,
                        "application": r.get("application") or "unknown",
                        "destination": r.get("destination") or "",
                        "protocol": r.get("protocol") or "",
                        "port": r.get("port") if r.get("port") is not None else "",
                        "sentKb": _f(r.get("sent")) or 0.0,
                        "recvKb": _f(r.get("recv")) or 0.0,
                        "numClients": _f(r.get("numClients")) or 0.0,
                    },
                )
            )
    return out


def wireless_events(key, org_id, nets):
    """sourcetype=meraki:api:wireless -- panel: channel utilisation per AP.

    One event per AP carrying the WORST band, not one per band. The panel averages
    utilizationTotal by apName; emitting both bands would average a congested 2.4 GHz with
    an idle 5 GHz and hide exactly the condition the panel exists to show.
    """
    try:
        rows = (
            api_get(
                f"/organizations/{org_id}/wireless/devices/channelUtilization/byDevice",
                key,
                {
                    # `interval` is REQUIRED in practice: omitted, it defaults to 3600 and
                    # the call 400s with "Timespan being queried must be larger than
                    # interval". timespan must be >= interval, hence the max().
                    "timespan": max(int(INTERVAL_WIRELESS), WIRELESS_INTERVAL),
                    "interval": WIRELESS_INTERVAL,
                    "networkIds[]": list(nets.keys()),
                },
            )
            or []
        )
    except urllib.error.HTTPError as e:
        log(f"channel utilisation unavailable (HTTP {e.code}): no wireless licence, or no APs")
        return []

    out = []
    for r in rows:
        net_id = str((r.get("network") or {}).get("id", ""))
        if net_id and net_id not in nets:
            continue
        worst = None
        worst_band = ""
        for band in r.get("byBand") or []:
            total = _f((band.get("total") or {}).get("percentage"))
            if total is not None and (worst is None or total > worst):
                worst, worst_band = total, band.get("band", "")
        if worst is None:
            continue
        name = r.get("name") or r.get("serial", "")
        out.append(
            envelope(
                "meraki:api:wireless",
                name,
                {
                    "networkName": nets.get(net_id, ""),
                    "networkId": net_id,
                    "serial": r.get("serial", ""),
                    "apName": name,
                    "band": worst_band,
                    "utilizationTotal": worst,
                },
            )
        )
    return out


FAMILIES = ("device", "uplink", "uplinkusage", "client", "apptraffic", "wireless")

INTERVALS = {
    "device": INTERVAL_DEVICE,
    "uplink": INTERVAL_UPLINK,
    "uplinkusage": INTERVAL_UPLINK,
    "client": INTERVAL_CLIENT,
    "apptraffic": INTERVAL_APPTRAFFIC,
    "wireless": INTERVAL_WIRELESS,
}


def collect(family, key, org_id, nets, state):
    if family == "device":
        return device_events(key, org_id, nets)
    if family == "uplink":
        return uplink_events(key, org_id, nets, state)
    if family == "uplinkusage":
        return uplinkusage_events(key, nets, state)
    if family == "client":
        return client_events(key, nets)
    if family == "apptraffic":
        return apptraffic_events(key, nets)
    if family == "wireless":
        return wireless_events(key, org_id, nets)
    raise ValueError(f"unknown family {family!r}")


def run_family(family, key, org_id, nets, state, dry_run):
    """One family's pass. A failure here must not take the other five down with it."""
    try:
        lines = collect(family, key, org_id, nets, state)
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace")
        log(f"{family}: HTTP {e.code} {detail}")
        return 0
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
        log(f"{family}: {type(e).__name__}: {e}")
        return 0
    if dry_run:
        log(f"{family}: {len(lines)} event(s) [dry-run, nothing written]")
        if lines:
            log(f"  sample: {lines[0][:400]}")
        return len(lines)
    for line in lines:
        print(line, flush=False)
    sys.stdout.flush()
    return len(lines)


def cli(argv=None):
    ap = argparse.ArgumentParser(description="Meraki Dashboard API -> Splunk HEC envelopes")
    ap.add_argument("--once", action="store_true", help="one pass over every family, then exit")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="collect and count, but write nothing to stdout (safe against a live Splunk)",
    )
    ap.add_argument(
        "--probe",
        metavar="FAMILY",
        choices=(*FAMILIES, "orgs", "networks"),
        help="dump the raw API response for one family and exit -- confirm the real field "
        "names against this account before trusting a panel",
    )
    ap.add_argument(
        "--family", action="append", choices=FAMILIES, help="restrict to these families"
    )
    args = ap.parse_args(argv)

    key = read_api_key()

    if args.probe == "orgs":
        print(json.dumps(api_get("/organizations", key), indent=2)[:8000])
        return
    org_id = resolve_org(key)
    if args.probe == "networks":
        print(json.dumps(api_get(f"/organizations/{org_id}/networks", key), indent=2)[:8000])
        return

    nets = resolve_networks(key, org_id)
    if not nets:
        sys.exit("no networks selected: check MERAKI_NETWORKS against --probe networks")

    state = load_state()

    if args.probe:
        raw = {
            "device": lambda: api_get(f"/organizations/{org_id}/devices/statuses", key),
            "uplink": lambda: api_get(f"/organizations/{org_id}/appliance/uplink/statuses", key),
            "uplinkusage": lambda: api_get(
                f"/networks/{next(iter(nets))}/appliance/uplinks/usageHistory",
                key,
                {"timespan": 1200, "resolution": 300},
            ),
            "client": lambda: api_get(
                f"/networks/{next(iter(nets))}/clients", key, {"timespan": 1800, "perPage": 10}
            ),
            "apptraffic": lambda: api_get(
                f"/networks/{next(iter(nets))}/traffic", key, {"timespan": 3600}
            ),
            "wireless": lambda: api_get(
                f"/organizations/{org_id}/wireless/devices/channelUtilization/byDevice",
                key,
                {"timespan": max(600, WIRELESS_INTERVAL), "interval": WIRELESS_INTERVAL},
            ),
        }[args.probe]()
        print(json.dumps(raw, indent=2)[:8000])
        return

    families = args.family or list(FAMILIES)
    log(
        "up: org={} networks={} families={} index={}".format(
            org_id, ",".join(sorted(nets.values())), ",".join(families), INDEX
        )
    )

    due = {f: 0.0 for f in families}
    while True:
        now = time.monotonic()
        total = 0
        for f in families:
            if now < due[f]:
                continue
            total += run_family(f, key, org_id, nets, state, args.dry_run)
            due[f] = now + INTERVALS[f]
        # NOT under --dry-run. The watermarks are advanced inside the collectors, so
        # persisting them after a rehearsal makes the first REAL run skip everything the
        # rehearsal already saw -- silently, and only once, which is the worst shape of bug
        # to reproduce. A dry run must leave no trace.
        if not args.dry_run:
            save_state(state)
        if total:
            log(f"pass complete: {total} event(s)")
        if args.once:
            return
        # Wake at the next due family rather than a fixed tick, so the hourly family does
        # not keep the process spinning every 5 minutes for nothing.
        sleep_for = max(1.0, min(due[f] for f in families) - time.monotonic())
        time.sleep(min(sleep_for, 300.0))


def main(argv=None):
    """Entry point WITH the process-level guards.

    Deliberately not inside `if __name__ == "__main__"`, for the same reason te_poller.py
    says so: a script run as __main__ is a different module object from the imported one,
    so a test that imports this file could never reach guards that lived there.
    """
    try:
        cli(argv)
    except KeyboardInterrupt:
        log("interrupted")
    except BrokenPipeError:
        # Downstream closed: `| head`, or hec_shipper died. Exiting quietly is correct --
        # the shipper owns delivery and systemd's Restart=always brings the pair back. The
        # dup2 stops the interpreter flushing stdout at shutdown and printing "Exception
        # ignored" after this handler already ran.
        log("downstream closed the pipe, exiting")
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


if __name__ == "__main__":
    main()
