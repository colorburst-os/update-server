# colorburst update server

An Omaha v3 responder on Cloudflare Workers. Devices POST their update
check to https://update.colorburst.net/update; payloads are served from
the `colorburst-updates` R2 bucket via https://dl.colorburst.net.

- `worker.js` — the responder (version-compared, unlike nebraska)
- `wrangler.toml` — routes, R2 binding, Analytics Engine dataset
- `releases.example.json` — goes into the bucket as `releases.json`;
  the per-track fields are exactly what `cros_generate_update_payload` emits
- `test-request.xml` — a realistic update_engine request for local tests

## DLCs are not served from here

Crostini's DLCs (`termina-dlc`, `termina-tools-dlc`, `edk2-ovmf-dlc`) are
force-ota DLCs: update_engine's `InstallAction` downloads the raw `dlc.img`
directly from `https://dl.colorburst.net/dlc/dlc/<id>/package/dlc.img` (the
same R2 bucket, `dlc/` prefix) and verifies it against the imageloader
manifest hash baked into the signed rootfs. **Omaha is never consulted** for
these installs, so this worker plays no part in them. A DLC-install request
still reaches the endpoint carrying one `<app>` per DLC (composite appid
`{OS_APPID}_<dlc_id>`); each gets a well-formed `noupdate`.

Publish the DLC images with `release/publish-dlc-images.sh` in the chromium-os
repo — full explanation in its `release/DLC-RELEASE.md`. (An earlier
DLC-over-Omaha design shipped here briefly; it was removed once we learned
force-ota DLCs bypass Omaha, so its `dlcs` metadata key and `dlcs/` R2 payloads
are gone.)

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

One datapoint per update-server request from real hardware:
blobs = [version, track, board, offered_version, device_id, hardware_class],
doubles = [has_updatecheck, is_active_ping, is_first_ping].

**Primary metric — devices seen**: `COUNT(DISTINCT blob5) WHERE blob5 <> ''`
per day. The device id exists precisely for this: every install that talked
to the server counts, whether or not Omaha ping bookkeeping cooperated.

**Secondary — Omaha actives**: `SUM(double2)`. The client sends at most one
`<ping active>` per day, so this is the identifier-free active count — but it
only works because the worker returns a REAL `<daystart elapsed_seconds>`
(seconds since UTC midnight). The worker used to return the literal `0`,
which re-anchored every client's "last ping day" to the moment of each check;
any device checking more than once a day then never pinged again, and this
metric read zero for real, healthy hardware (found 2026-08-20 when the first
real device was invisible in stats). Meaningful from 2026-08-21 on.

```sql
-- devices seen, by day (primary)
SELECT toDate(timestamp) AS day, COUNT(DISTINCT blob5) AS devices
FROM colorburst_pings WHERE blob5 <> '' GROUP BY day ORDER BY day;

-- Omaha actives, by day
SELECT toDate(timestamp) AS day, SUM(double2) AS active_devices
FROM colorburst_pings GROUP BY day ORDER BY day;

-- new installs, by day
SELECT toDate(timestamp) AS day, SUM(double3) AS new_installs
FROM colorburst_pings GROUP BY day ORDER BY day;
```

Installed base = devices seen over a 7- or 28-day window (ages out retired
machines, unlike cumulative installs). Caveats: unofficial (dev) builds never
check in, and builds older than the device-id patch (pre-2026.32.7) have
`blob5 = ''` and are visible only in raw row counts.

Publishing a release = upload the payload to
`payloads/<target_version>/<name>.bin` in the bucket, then upload the
updated `releases.json`. Telemetry tier 1 (version distribution, active
devices) accumulates in the `colorburst_pings` Analytics Engine dataset
with no device identifiers.

## Checking the numbers: stats.py

`./stats.py` (Python 3, stdlib only; replaces the old stats.sh) queries the
Analytics Engine dataset and graphs it in the terminal (reads the token from
`~/.cloudflare/token`):

```
./stats.py              # devices seen/day, last 30 days (ASCII bar graph)
./stats.py devices 90   # explicit window
./stats.py active       # Omaha active pings/day (meaningful from 2026-08-21)
./stats.py installs     # new installs/day
./stats.py versions 7   # devices by client version, last 7 days
./stats.py boards       # devices by board
./stats.py hardware     # raw rows by hardware_class (telemetry-gate diagnosis)
./stats.py sql "SELECT ..."   # raw query, pretty-printed JSON
./stats.py html 30      # write stats.html — self-contained SVG charts,
                        #   dark-mode aware, open in a browser
```

Only real installs are counted (the worker gates telemetry on the device id,
falling back to a non-empty `hardware_class` for pre-device-id builds; both
are absent under crosvm), so test VMs never appear.
