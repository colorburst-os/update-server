# colorburst update server

An Omaha v3 responder on Cloudflare Workers. Devices POST their update
check to https://update.colorburst.net/update; payloads are served from
the `colorburst-updates` R2 bucket via https://dl.colorburst.net.

- `worker.js` — the responder (version-compared, unlike nebraska)
- `wrangler.toml` — routes, R2 binding, Analytics Engine dataset
- `releases.example.json` — goes into the bucket as `releases.json`;
  the fields are exactly what `cros_generate_update_payload` emits
- `test-request.xml` — a realistic update_engine request for local tests

Local test loop:

```bash
npx wrangler dev --local --port 8787
npx wrangler r2 object put colorburst-updates/releases.json --file releases.json --local
curl -s -X POST --data-binary @test-request.xml http://127.0.0.1:8787/update
```

Deploy (needs a token with Workers Scripts, R2, Workers Routes, DNS):

```bash
export CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=...
npx wrangler deploy
npx wrangler r2 object put colorburst-updates/releases.json --file releases.json --remote
```

## Fleet numbers

Each datapoint's doubles are [has_updatecheck, is_active_ping,
is_first_ping]. Because the Omaha client itself sends at most one
active ping per day and marks its first-ever ping with r=-1, fleet
arithmetic is identifier-free. Query via the Analytics Engine SQL API
(https://api.cloudflare.com/client/v4/accounts/<acct>/analytics_engine/sql):

```sql
-- daily active devices, by day
SELECT toDate(timestamp) AS day, SUM(double2) AS active_devices
FROM colorburst_pings GROUP BY day ORDER BY day;

-- new installs, by day
SELECT toDate(timestamp) AS day, SUM(double3) AS new_installs
FROM colorburst_pings GROUP BY day ORDER BY day;

-- version distribution among today's actives
SELECT blob1 AS version, SUM(double2) AS devices
FROM colorburst_pings WHERE timestamp > NOW() - INTERVAL '1' DAY
GROUP BY version;
```

Installed base = actives over a 7- or 28-day window (ages out retired
machines, unlike cumulative installs). Caveat: unofficial (dev) builds
never ping — the numbers begin with the first official release.

Publishing a release = upload the payload to
`payloads/<target_version>/<name>.bin` in the bucket, then upload the
updated `releases.json`. Telemetry tier 1 (version distribution, active
devices) accumulates in the `colorburst_pings` Analytics Engine dataset
with no device identifiers.

## Checking the numbers: stats.sh

`./stats.sh` queries the Analytics Engine dataset and graphs it in the
terminal (reads the token from `~/.cloudflare/token`):

```
./stats.sh              # active devices/day, last 30 days (ASCII bar graph)
./stats.sh active 90    # explicit window
./stats.sh installs     # new installs/day
./stats.sh versions     # active devices by version, today
./stats.sh boards       # active devices by board, today
./stats.sh sql "SELECT ..."   # raw query, pretty-printed JSON
./stats.sh html 30      # write stats.html — self-contained SVG charts,
                        #   dark-mode aware, open in a browser
```

Only real hardware is counted (the worker gates telemetry on a non-empty
`hardware_class`, which is empty under crosvm), so test VMs never appear.
