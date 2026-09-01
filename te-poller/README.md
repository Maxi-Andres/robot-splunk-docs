# te-poller

Pulls ThousandEyes network test results over the v7 API and feeds them to Splunk's HEC.

**This is a bridge, and it is meant to be deleted.** The supported way to get ThousandEyes
data into Splunk is a *streaming integration*, where ThousandEyes pushes OpenTelemetry
directly to the HEC. That path needs an endpoint reachable from the public internet on TCP
443, with a DNS name and a certificate from a publicly trusted CA. This Splunk is on
`192.168.20.200` with a self-signed certificate, so ThousandEyes refuses to create the
stream — it validates reachability at creation time.

The migration plan back to the supported path is `../LICENCIA-Y-THOUSANDEYES.md` §5. Read it
before adding a feature here: almost anything you might want is something the official
stream already gives for free.

## Why it looks like this

It is a **producer**, not a sender:

```
te_poller.py | ../../robot-telemetry-agent/shipper/hec_shipper.py
```

`hec_shipper` already solves the disk spool, the batching, the retry backoff and the daily
byte cap. Duplicating any of that would mean two copies to keep correct, so this process
only asks ThousandEyes a question and shapes the answer. `run.sh` wires the pipe.

## The one thing that makes the cutover free

The metric names, the dimension keys and the units are **exactly** what ThousandEyes' own
OpenTelemetry Data Model v2 exporter emits:

| Metric | Unit | Source field |
|---|---|---|
| `network.loss` | percent | `loss` |
| `network.latency` | **seconds** | `avgLatency` (ms) **÷ 1000** |
| `network.jitter` | milliseconds | `jitter` |

Dimensions: `thousandeyes.test.id`, `.test.name`, `.test.type`,
`thousandeyes.source.agent.id`, `.name`, `.location`, and `server.address`.

So the dashboard panels cannot tell the two sources apart. Cutting over is: stop this, start
the stream. No panel edits.

> The latency conversion is the trap. The v7 API reports milliseconds; OTel v2 reports
> seconds. Matching OTel — not the API being read — is what makes the paths interchangeable.
> Get it backwards and every latency panel is wrong by 1000x while still looking plausible.

## Setup

**1. Confirm the field names against your own account before trusting anything:**

```bash
printf '%s' 'YOUR-TE-TOKEN' > ~/.te_bearer_token && chmod 600 ~/.te_bearer_token
./te_poller.py --probe
```

`--probe` dumps the raw API response for the first network test. The fixtures in
`tests/test_te_poller.py` are built from the documented schema; if a real response disagrees,
**fix the fixture first and let the test fail.**

**2. The Splunk side** — the metrics index must be created as one, an events index cannot
hold metrics:

```bash
splunk add index thousandeyes -datatype metric
splunk add index thousandeyes_alerts
splunk http-event-collector create thousandeyes \
  -index thousandeyes -indexes thousandeyes,thousandeyes_alerts \
  -sourcetype thousandeyes:otel -uri https://localhost:8089
```

**3. Configure and run:**

```bash
cp .env.example .env      # then edit
./te_poller.py --once | head -3      # eyeball the envelopes
./run.sh                              # the real pipe
```

**4. Install** — it runs on the Splunk server, not the robot, so that the HEC hop is
localhost and nothing new is exposed:

```bash
sudo cp systemd/splunk-te-poller.service /etc/systemd/system/
sudo systemctl enable --now splunk-te-poller
```

## Verifying

```
| mcatalog values(metric_name) WHERE index=thousandeyes
| mstats avg(network.latency) WHERE index=thousandeyes span=5m
```

If `metric_name` comes back empty but the shipper logs 200s, the index was created as an
events index. There is no way to convert one: delete and recreate with `-datatype metric`.

## Licence cost

Three metrics per agent per round. At a 5-minute interval with a handful of tests this is
single-digit MB/day, and `DAILY_BYTE_CAP` (20 MB by default) is a hard stop rather than a
hope. The shared budget is 500 MB/day, of which Cisco telemetry already takes ~138 MB and
the robot agent is capped at 150 MB — see `../LICENCIA-Y-THOUSANDEYES.md` §2.1.g.

## Tests

```bash
python3 -m pytest tests/ -q
```

No account and no network: every test feeds a literal API response, so what is under test is
the mapping. The suite covers the unit conversion, the zero-vs-absent distinction, the
roundId watermark that stops the window overlap being billed twice, and that one failing
test does not abort the pass.
