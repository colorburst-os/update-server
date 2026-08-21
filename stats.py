#!/usr/bin/env python3
"""colorburst fleet stats: query the Analytics Engine dataset and graph it.

  ./stats.py                  devices seen per day (last 30 days) + bar graph
  ./stats.py devices [DAYS]   same, explicit window (COUNT DISTINCT device id)
  ./stats.py active  [DAYS]   Omaha active pings per day (one per device per
                              UTC day -- meaningful from 2026-08-21 on, when
                              the worker started returning a real daystart)
  ./stats.py installs [DAYS]  first-ever pings per day (new installs)
  ./stats.py versions [DAYS]  devices by client version (default: last 1 day)
  ./stats.py boards   [DAYS]  devices by board
  ./stats.py hardware [DAYS]  raw rows by hardware_class (gate diagnosis)
  ./stats.py sql "SELECT ..." arbitrary query, pretty-printed JSON
  ./stats.py html [DAYS]      write stats.html (self-contained SVG charts)

Reads the Cloudflare token from ~/.cloudflare/token (account_id:/api_token:).

Datapoint schema (one row per update-server request from real hardware):
  blob1 client version   blob2 track     blob3 board
  blob4 offered version  blob5 device id blob6 hardware_class
  double1 has updatecheck  double2 active ping  double3 first-ever ping

Two device metrics, on purpose:
  * "devices" (primary) counts DISTINCT blob5 -- every install that talked to
    the server, ping or not. Misses only pre-device-id builds (blob5 = '').
  * "active" counts Omaha pings, the update_engine-cooperative definition of
    one-per-device-per-day. Zero before the daystart fix; use for trends only.
"""

import datetime
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

DATASET = "colorburst_pings"


def credentials():
    path = os.environ.get("CB_TOKEN_FILE",
                          os.path.expanduser("~/.cloudflare/token"))
    token = account = None
    with open(path) as f:
        for line in f:
            key, _, value = line.partition(":")
            if key.strip() == "api_token":
                token = value.strip()
            elif key.strip() == "account_id":
                account = value.strip()
    if not token or not account:
        sys.exit(f"error: no api_token/account_id in {path}")
    return token, account


def run_sql(query):
    """SQL -> list of row dicts. Retries: the SQL API rate-limits bursts."""
    token, account = credentials()
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           "/analytics_engine/sql")
    req = urllib.request.Request(
        url, data=query.encode(),
        headers={"Authorization": f"Bearer {token}"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
            return json.loads(body).get("data", [])
        except (urllib.error.URLError, json.JSONDecodeError):
            time.sleep(1 + attempt)
    sys.exit("error: no response from Analytics Engine "
             "(rate limited or bad token)")


def bars(title, rows, unit=""):
    """Render [{label, value}, ...] as a terminal bar graph."""
    print(f"\n  {title}")
    print("  " + "-" * len(title))
    if not rows:
        print("  (no data yet)\n")
        return
    keys = list(rows[0].keys())
    lab, val = keys[0], keys[1]
    data = [(str(r[lab]), float(r[val] or 0)) for r in rows]
    peak = max(v for _, v in data) or 1
    width = min(shutil.get_terminal_size((80, 20)).columns, 100)
    labw = max(len(l) for l, _ in data)
    barw = max(10, width - labw - 12)
    for l, v in data:
        n = round(v / peak * barw)
        num = str(int(v)) if v == int(v) else f"{v:.1f}"
        print(f"  {l:<{labw}}  {'#' * n}{'.' * (barw - n)}  {num} {unit}")
    print()


# --- the queries -----------------------------------------------------------
# All windows are UTC (Analytics Engine timestamps are UTC).

def q_devices(days):
    return run_sql(
        f"SELECT toDate(timestamp) AS day, COUNT(DISTINCT blob5) AS devices"
        f" FROM {DATASET}"
        f" WHERE timestamp > NOW() - INTERVAL '{days}' DAY AND blob5 <> ''"
        f" GROUP BY day ORDER BY day FORMAT JSON")


def q_active(days):
    return run_sql(
        f"SELECT toDate(timestamp) AS day, SUM(double2) AS devices"
        f" FROM {DATASET}"
        f" WHERE timestamp > NOW() - INTERVAL '{days}' DAY"
        f" GROUP BY day ORDER BY day FORMAT JSON")


def q_installs(days):
    return run_sql(
        f"SELECT toDate(timestamp) AS day, SUM(double3) AS installs"
        f" FROM {DATASET}"
        f" WHERE timestamp > NOW() - INTERVAL '{days}' DAY"
        f" GROUP BY day ORDER BY day FORMAT JSON")


def q_by_blob(blob, days):
    return run_sql(
        f"SELECT {blob} AS k, COUNT(DISTINCT blob5) AS devices"
        f" FROM {DATASET}"
        f" WHERE timestamp > NOW() - INTERVAL '{days}' DAY AND blob5 <> ''"
        f" GROUP BY k ORDER BY devices DESC FORMAT JSON")


def q_hardware(days):
    # Row counts, not device counts: this is the diagnostic view for the
    # telemetry gate (is hardware_class "unknown", "", or a real HWID?).
    return run_sql(
        f"SELECT blob6 AS hardware_class, COUNT() AS requests,"
        f" COUNT(DISTINCT blob5) AS devices"
        f" FROM {DATASET}"
        f" WHERE timestamp > NOW() - INTERVAL '{days}' DAY"
        f" GROUP BY hardware_class ORDER BY requests DESC FORMAT JSON")


# --- html report -----------------------------------------------------------

def svg_bars(rows, color, unit):
    if not rows:
        return "<p class=empty>chưa có dữ liệu</p>"
    keys = list(rows[0].keys())
    lab, val = keys[0], keys[1]
    vals = [(str(r[lab]), float(r[val] or 0)) for r in rows]
    peak = max(v for _, v in vals) or 1
    W, H, pad = 720, 220, 30
    bw = max(6, min(48, 680 // len(vals) - 6))
    step = 680 / len(vals)
    parts = []
    for i, (l, v) in enumerate(vals):
        h = (v / peak) * (H - 2 * pad)
        x = pad + i * step
        y = H - pad - h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw}"'
                     f' height="{h:.1f}" fill="{color}">'
                     f'<title>{l}: {int(v)} {unit}</title></rect>')
        if len(vals) <= 16 or i % max(1, len(vals) // 12) == 0:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{H - pad + 14}"'
                         f' font-size="9" text-anchor="middle"'
                         f' fill="var(--muted)">{l[-5:]}</text>')
    parts.append(f'<text x="{pad}" y="{pad - 8}" font-size="11"'
                 f' fill="var(--muted)">đỉnh {int(peak)} {unit}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" width="100%">'
            + "".join(parts) + "</svg>")


def write_html(days):
    out = os.environ.get("CB_HTML_OUT", "stats.html")
    dev = q_devices(days)
    ins = q_installs(days)
    ver = q_by_blob("blob1", 1)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    today = int(float(dev[-1][list(dev[-1])[1]])) if dev else 0
    html = f"""<!doctype html><html lang=vi><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>colorburst — số liệu</title>
<style>
:root{{--bg:#fdfdfb;--fg:#1a1a1a;--muted:#666;--card:#fff;--rule:#e5e5e2;--a:#1a5c3a;--b:#8a5a2b}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16151a;--fg:#e8e6e3;--muted:#9a978f;--card:#1f1e24;--rule:#33313a;--a:#5fbf8a;--b:#c9925a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}}
main{{max-width:780px;margin:0 auto;padding:2rem 1rem}}
h1{{font-size:1.3rem;margin:0}}.sub{{color:var(--muted);margin:.2rem 0 2rem}}
section{{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:1rem 1.2rem;margin:1rem 0}}
h2{{font-size:1rem;margin:0 0 .6rem}}.empty{{color:var(--muted)}}
.big{{font-size:2.4rem;font-weight:700;color:var(--a)}}
</style>
<main>
<h1>colorburst — số liệu thiết bị</h1>
<p class=sub>Cập nhật {stamp}. Chỉ tính máy thật (VM không được đếm).</p>
<section><h2>Thiết bị (ngày gần nhất)</h2><div class=big>{today}</div></section>
<section><h2>Thiết bị mỗi ngày</h2>{svg_bars(dev, 'var(--a)', 'máy')}</section>
<section><h2>Lượt cài mới mỗi ngày</h2>{svg_bars(ins, 'var(--b)', 'lượt')}</section>
<section><h2>Phân bố phiên bản (hôm nay)</h2>{svg_bars(ver, 'var(--a)', 'máy')}</section>
</main></html>"""
    with open(out, "w") as f:
        f.write(html)
    print(f">>> wrote {out} ({len(html)} bytes) — open it in a browser")


# --- entry -----------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "devices"
    arg = argv[1] if len(argv) > 1 else None

    def days(default):
        return int(arg) if arg else default

    if cmd == "devices":
        bars(f"Devices seen/day (last {days(30)} days, distinct ids)",
             q_devices(days(30)), "máy")
    elif cmd == "active":
        bars(f"Active pings/day (last {days(30)} days, Omaha semantics)",
             q_active(days(30)), "máy")
    elif cmd == "installs":
        bars(f"New installs/day (last {days(30)} days)",
             q_installs(days(30)), "lượt")
    elif cmd == "versions":
        bars(f"Devices by version (last {days(1)} day(s))",
             q_by_blob("blob1", days(1)), "máy")
    elif cmd == "boards":
        bars(f"Devices by board (last {days(1)} day(s))",
             q_by_blob("blob3", days(1)), "máy")
    elif cmd == "hardware":
        rows = q_hardware(days(30))
        print(f"\n  hardware_class over the last {days(30)} day(s)"
              " (blob6; empty until the 2026-08-21 worker)")
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    elif cmd == "sql":
        if not arg:
            sys.exit('usage: stats.py sql "SELECT ..."')
        print(json.dumps(run_sql(arg + " FORMAT JSON"),
                         indent=1, ensure_ascii=False))
    elif cmd == "html":
        write_html(days(30))
    else:
        sys.exit(__doc__.strip())


if __name__ == "__main__":
    main()
