# meraki-hq

Feeds the **`Silk HQ — Meraki`** dashboard (Splunk view `silk_hq_meraki`).

The dashboard was built first and the ingestion never existed, so every panel read *"No
search results returned"*. This directory is the missing half.

## The integration has three legs, and only one is code

| Leg | Sourcetypes | Who sends it | Lives where |
|---|---|---|---|
| **Dashboard API** | `meraki:api:device`, `:uplink`, `:uplinkusage`, `:client`, `:apptraffic`, `:wireless` | `meraki_poller.py`, here | this repo |
| **Syslog** | `meraki:syslog` | the Meraki appliance, to `udp/5515` | Meraki Dashboard config |
| **Alert webhooks** | `meraki:webhook` | the Meraki cloud, to the HEC | Meraki Dashboard config |

**If the event, security or flow panels are empty, this poller is not the thing to
debug** — those two legs never touch this code. Setup for all three is in
`../INTEGRACION-MERAKI.md`.

## Why it looks like this

It is a **producer**, not a sender:

```
meraki_poller.py | ../../robot-telemetry-agent/shipper/hec_shipper.py
```

Exactly the shape of `../te-poller`, and for the same reason: `hec_shipper` already solves
the disk spool, the batching, the retry backoff and the daily byte cap. A second copy of
any of that is how the two drift. This process only asks Meraki a question and shapes the
answer. `run.sh` wires the pipe.

## The field names are a contract

Every field is read **by name** by a panel. Rename one and that panel goes blank with no
error, nothing in any log, and nothing in the poller's output to suggest it. When you add a
field, add the row.

| Sourcetype | Fields the panels read | Panels |
|---|---|---|
| `meraki:api:device` | `networkName` `serial` `deviceName` `status` `productType` `model` `lanIp` `publicIp` `firmware` | devices-online KPI, devices-down KPI, inventory table, the network dropdown |
| `meraki:api:uplink` | `networkName` `uplink` `status` `lossPercent` `latencyMs` | active-uplink KPI, WAN loss KPI, WAN latency KPI, loss/latency timecharts, failover chart |
| `meraki:api:uplinkusage` | `networkName` `uplink` `sentBytes` `receivedBytes` `intervalSec` | throughput per uplink |
| `meraki:api:client` | `networkName` `mac` `description` `ip` `ssid` `connectionType` `manufacturer` `recentDeviceName` `usageSentKb` `usageRecvKb` | clients by SSID, clients per AP, top talkers |
| `meraki:api:apptraffic` | `networkName` `application` `protocol` `port` `sentKb` `recvKb` `numClients` | top applications, traffic by protocol/port |
| `meraki:api:wireless` | `networkName` `apName` `utilizationTotal` | channel utilisation per AP |

Three of these are load-bearing in a way that is easy to undo by accident:

- **`uplink` must stay `wan1`/`wan2`/`cellular`** exactly as Meraki spells it. The loss
  panel filters `uplink=wan*`; prettifying it to "WAN 1" empties that panel.
- **`connectionType` must be lowercase.** Meraki answers `"Wireless"`; the clients-by-SSID
  panel filters `connectionType=wireless`, which matches nothing capitalised.
- **`status` must stay lowercase `active`.** The latency KPI filters on it.

## The unit trap

Meraki reports usage in **kilobytes**, and the panels want two different things:

| Sourcetype | Unit emitted | Why |
|---|---|---|
| `:client`, `:apptraffic` | kilobytes, untouched | those panels divide by 1024 themselves |
| `:uplinkusage` | **bytes** | the throughput panel computes `(sentBytes+receivedBytes)*8/1000000/intervalSec` and that is only Mbps if the inputs are bytes |

Get it backwards and the throughput panel is wrong by 1000x while still plotting a
perfectly plausible line. Same class of trap as `te_poller`'s ms-vs-seconds conversion,
which is why `KB_TO_BYTES` is a named constant rather than a literal at the call site, and
why `test_uplinkusage_kilobytes_become_bytes` asserts the derived Mbps and not just the
multiplication.

## Confirm the schema before trusting a panel

The field mapping was built from Meraki's **published** v1 schema, not from Silk's own
organisation. A licence tier or a device model can omit a field.

```bash
printf '%s' 'YOUR-MERAKI-KEY' > ~/.meraki_api_key && chmod 600 ~/.meraki_api_key
./meraki_poller.py --probe orgs        # which organisations does this key see?
./meraki_poller.py --probe networks    # spell MERAKI_NETWORKS exactly as this prints it
./meraki_poller.py --probe uplink      # the raw response, before any shaping
```

If a real response disagrees with `tests/test_meraki_poller.py`, **fix the fixture first
and let the test fail.**

## Run it

```bash
cp .env.example .env          # then edit; .env is gitignored
./meraki_poller.py --once --dry-run    # collects, counts, writes nothing to stdout
./run.sh                               # the real thing, piped into the shipper
```

`--dry-run` is safe against a live Splunk: it writes nothing to stdout, so the pipe into
`hec_shipper` carries nothing. `--family uplink` restricts a pass while you debug one set
of panels.

Deploy as a service on the **Splunk server** — that is where the HEC leg becomes localhost
and where the box stays up:

```bash
sudo cp systemd/meraki-poller.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meraki-poller
journalctl -u meraki-poller -f
```

## Why these intervals

| Family | Interval | Reason |
|---|---|---|
| device, uplink, wireless | 300 s | the loss/latency series Meraki keeps is 60 s granular and the failover panel buckets at 15 min — faster buys nothing |
| client | 900 s | the expensive endpoint (pages of up to 1000); the panels bucket at 30 min |
| apptraffic | 3600 s | Meraki aggregates it hourly upstream; polling faster returns the same numbers and spends licence |

Uplink loss/latency is requested over **twice** its interval so a late point is never
missed, and a per-`(serial, uplink)` watermark in `MERAKI_STATE_FILE` stops the overlap
being indexed twice.

## Licence cost

The six API families measure at roughly **8 MB/day**. `DAILY_BYTE_CAP` is set to 100 MB as
a ~12x runaway guard, not a budget.

That cap bounds **only** this poller. Syslog goes straight to `udp/5515` and webhooks
straight to the HEC, neither through the shipper — and syslog with *Flows* enabled is the
one thing here that can actually cost 0.3–1.5 GB/day. Turn Flows on deliberately.

## What is deliberately not here

`meraki:api:license` is defined in the server's `props.conf` but **no panel reads it**, so
nothing emits it. Adding a seventh family that no panel consumes would be dead code that
still spends licence. If a licensing panel is ever built, add the family then.
