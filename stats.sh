#!/usr/bin/env bash
# colorburst fleet stats: query the Analytics Engine dataset and graph it.
#
#   ./stats.sh                 # daily active devices (last 30 days) + bar graph
#   ./stats.sh active [DAYS]   # same, explicit window
#   ./stats.sh installs [DAYS] # new installs per day
#   ./stats.sh versions        # active devices by client version, today
#   ./stats.sh boards          # active devices by board, today
#   ./stats.sh sql "SELECT ..."# run an arbitrary query, print the JSON rows
#   ./stats.sh html [DAYS]     # write stats.html (self-contained SVG charts)
#
# Reads the Cloudflare token from ~/.cloudflare/token (account_id:/api_token:).
# double2 = active ping (one per real device per day), double3 = first-ever
# ping (new install); telemetry is gated on real hardware, so VMs never count.
set -euo pipefail

TOKEN_FILE="${CB_TOKEN_FILE:-$HOME/.cloudflare/token}"
API_TOKEN="$(awk -F: '/^api_token/{print $2}' "$TOKEN_FILE" | tr -d '[:space:]')"
ACCOUNT="$(awk -F: '/^account_id/{print $2}' "$TOKEN_FILE" | tr -d '[:space:]')"
DATASET="colorburst_pings"
ENDPOINT="https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/analytics_engine/sql"

run_sql() { # sql -> JSON on stdout (retries: the SQL API rate-limits bursts)
  local out i
  for i in 1 2 3 4 5; do
    out="$(curl -s "$ENDPOINT" -H "Authorization: Bearer $API_TOKEN" -d "$1")"
    case "$out" in
      '{'*) printf '%s' "$out"; return 0 ;;   # got JSON
    esac
    sleep 1
  done
  echo "error: no response from Analytics Engine (rate limited or bad token)" >&2
  printf '{"data":[]}'
}

# render_bars: JSON {data:[{label,value},...]} as $2, draws a terminal graph
render_bars() { # title json
  python3 - "$1" "$2" <<'PY'
import json, sys, shutil
title = sys.argv[1]
doc = json.loads(sys.argv[2])
rows = doc.get("data", [])
if not rows:
    print(f"{title}: (no data yet)")
    sys.exit(0)
keys = list(rows[0].keys())
lab, val = keys[0], keys[1]
data = [(str(r[lab]), float(r[val] or 0)) for r in rows]
peak = max(v for _, v in data) or 1
width = min(shutil.get_terminal_size((80, 20)).columns, 100)
labw = max(len(l) for l, _ in data)
barw = max(10, width - labw - 12)
print(f"\n  {title}")
print("  " + "-" * (len(title)))
for l, v in data:
    n = int(round(v / peak * barw))
    num = f"{int(v)}" if v == int(v) else f"{v:.1f}"
    print(f"  {l:<{labw}}  {'#' * n}{'.' * (barw - n)}  {num}")
print()
PY
}

cmd="${1:-active}"; shift || true

case "$cmd" in
  active)
    days="${1:-30}"
    render_bars "Active devices/day (last $days days)" \
      "$(run_sql "SELECT toDate(timestamp) AS day, SUM(double2) AS devices FROM $DATASET WHERE timestamp > NOW() - INTERVAL '$days' DAY GROUP BY day ORDER BY day FORMAT JSON")"
    ;;
  installs)
    days="${1:-30}"
    render_bars "New installs/day (last $days days)" \
      "$(run_sql "SELECT toDate(timestamp) AS day, SUM(double3) AS installs FROM $DATASET WHERE timestamp > NOW() - INTERVAL '$days' DAY GROUP BY day ORDER BY day FORMAT JSON")"
    ;;
  versions)
    render_bars "Active devices by version (today)" \
      "$(run_sql "SELECT blob1 AS version, SUM(double2) AS devices FROM $DATASET WHERE timestamp > NOW() - INTERVAL '1' DAY GROUP BY version ORDER BY devices DESC FORMAT JSON")"
    ;;
  boards)
    render_bars "Active devices by board (today)" \
      "$(run_sql "SELECT blob3 AS board, SUM(double2) AS devices FROM $DATASET WHERE timestamp > NOW() - INTERVAL '1' DAY GROUP BY board ORDER BY devices DESC FORMAT JSON")"
    ;;
  sql)
    run_sql "${1:?usage: stats.sh sql \"SELECT ...\"} FORMAT JSON" \
      | python3 -m json.tool
    ;;
  html)
    days="${1:-30}"
    OUT="${CB_HTML_OUT:-stats.html}"
    ACT="$(run_sql "SELECT toDate(timestamp) AS day, SUM(double2) AS v FROM $DATASET WHERE timestamp > NOW() - INTERVAL '$days' DAY GROUP BY day ORDER BY day FORMAT JSON")"
    INS="$(run_sql "SELECT toDate(timestamp) AS day, SUM(double3) AS v FROM $DATASET WHERE timestamp > NOW() - INTERVAL '$days' DAY GROUP BY day ORDER BY day FORMAT JSON")"
    VER="$(run_sql "SELECT blob1 AS k, SUM(double2) AS v FROM $DATASET WHERE timestamp > NOW() - INTERVAL '1' DAY GROUP BY k ORDER BY v DESC FORMAT JSON")"
    ACT="$ACT" INS="$INS" VER="$VER" DAYS="$days" python3 - "$OUT" <<'PY'
import json, os, sys, datetime
out = sys.argv[1]
def rows(env): return json.loads(os.environ[env]).get("data", [])
act, ins, ver = rows("ACT"), rows("INS"), rows("VER")

def svg_bars(data, lab, val, color, unit):
    if not data: return "<p class=empty>chưa có dữ liệu</p>"
    vals = [(str(r[lab]), float(r[val] or 0)) for r in data]
    peak = max(v for _, v in vals) or 1
    W, H, pad, bw = 720, 220, 30, max(6, min(48, 680 // len(vals) - 6))
    step = 680 / len(vals)
    bars = []
    for i, (l, v) in enumerate(vals):
        h = (v / peak) * (H - 2*pad)
        x = pad + i*step; y = H - pad - h
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw}" height="{h:.1f}" fill="{color}"><title>{l}: {int(v)} {unit}</title></rect>')
        if len(vals) <= 16 or i % max(1,len(vals)//12) == 0:
            bars.append(f'<text x="{x+bw/2:.1f}" y="{H-pad+14:.0f}" font-size="9" text-anchor="middle" fill="var(--muted)">{l[-5:]}</text>')
    bars.append(f'<text x="{pad}" y="{pad-8:.0f}" font-size="11" fill="var(--muted)">đỉnh {int(peak)} {unit}</text>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%">{"".join(bars)}</svg>'

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
total_active = int(sum(float(r["v"] or 0) for r in act[-1:])) if act else 0
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
<p class=sub>Cập nhật {now}. Chỉ tính máy thật (VM không được đếm).</p>
<section><h2>Thiết bị hoạt động (ngày gần nhất)</h2><div class=big>{total_active}</div></section>
<section><h2>Thiết bị hoạt động mỗi ngày</h2>{svg_bars(act,'day','v','var(--a)','máy')}</section>
<section><h2>Lượt cài mới mỗi ngày</h2>{svg_bars(ins,'day','v','var(--b)','lượt')}</section>
<section><h2>Phân bố phiên bản (hôm nay)</h2>{svg_bars(ver,'k','v','var(--a)','máy')}</section>
</main></html>"""
open(out,"w").write(html)
print(f">>> wrote {out} ({len(html)} bytes) — open it in a browser")
PY
    ;;
  *)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
