#!/usr/bin/env python3
"""Minimal Cloudflare zone-analytics Prometheus exporter (stdlib only).

Cloudflare Free プランで唯一アクセスできる httpRequestsAdaptiveGroups だけを叩く。
lablabs/cloudflare-exporter は httpRequests1mGroups / firewall / healthcheck を
1 本の結合クエリで要求するため Free プランでは data:null で全滅する。本実装は
adaptive のみを使い、累積カウンタとして expose する。

挙動:
- LAG_SECONDS だけ過去を「確定済み」とみなし、前回処理位置 last から
  (now - LAG_SECONDS) までの新しいスライスを毎 SCRAPE_INTERVAL 秒で取得して
  カウンタへ加算する (重複なし)。
- 起動時に LOOKBACK_INIT_SECONDS だけ遡って backfill し、ダッシュボードに即値を出す
  (カウンタは 0 からその合計に立ち上がるだけなので rate() に偽スパイクは出ない)。
"""
import json
import os
import sys
import time
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID", "")
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "60"))
LAG_SECONDS = int(os.environ.get("LAG_SECONDS", "900"))
LOOKBACK_INIT_SECONDS = int(os.environ.get("LOOKBACK_INIT_SECONDS", "3600"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
CF_TIMEOUT = int(os.environ.get("CF_TIMEOUT", "20"))
GQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# (labelvalue tuple) -> number。スレッド間共有なので lock で保護。
_lock = threading.Lock()
requests_total = {}        # (host,status,cache) -> count
bandwidth_total = {}       # (host,cache) -> bytes
requests_country = {}      # (country,) -> count
_state = {"last_success_ts": 0.0, "scrape_errors": 0, "up": 0}
_poll_thread = None  # liveness (/healthz) でスレッド生存を確認するための参照

QUERY = """query($z:[string!],$min:Time!,$max:Time!,$lim:Int!){
  viewer{zones(filter:{zoneTag_in:$z}){
    byHost: httpRequestsAdaptiveGroups(limit:$lim,filter:{datetime_geq:$min,datetime_lt:$max}){
      count sum{edgeResponseBytes}
      dimensions{clientRequestHTTPHost edgeResponseStatus cacheStatus}
    }
    byCountry: httpRequestsAdaptiveGroups(limit:$lim,filter:{datetime_geq:$min,datetime_lt:$max}){
      count dimensions{clientCountryName}
    }
  }}
}"""


def _floor_minute(t):
    return t.replace(second=0, microsecond=0)


def fetch_slice(mintime, maxtime):
    """[mintime, maxtime) の adaptive データを取得して dict 群へ加算。例外は呼び元へ。"""
    body = json.dumps({
        "query": QUERY,
        "variables": {
            "z": [CF_ZONE_ID],
            "min": mintime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "max": maxtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lim": 10000,
        },
    }).encode()
    req = urllib.request.Request(GQL_URL, data=body, headers={
        "Authorization": "Bearer " + CF_API_TOKEN,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=CF_TIMEOUT) as r:
        payload = json.load(r)
    if payload.get("errors"):
        raise RuntimeError("graphql errors: %s" % payload["errors"])
    zones = (((payload.get("data") or {}).get("viewer") or {}).get("zones") or [])
    if not zones:
        return
    z = zones[0]
    # まずローカルへ集約する。途中で例外が出ても共有カウンタは未変更のままなので、
    # 呼び元が last を進めず同スライスを再取得しても二重カウントしない (原子的マージ)。
    loc_req, loc_bw, loc_country = {}, {}, {}
    for g in z.get("byHost", []):
        d = g.get("dimensions", {})
        key = (d.get("clientRequestHTTPHost", ""), str(d.get("edgeResponseStatus", "")), d.get("cacheStatus", ""))
        loc_req[key] = loc_req.get(key, 0) + g.get("count", 0)
        bkey = (d.get("clientRequestHTTPHost", ""), d.get("cacheStatus", ""))
        loc_bw[bkey] = loc_bw.get(bkey, 0) + (g.get("sum", {}) or {}).get("edgeResponseBytes", 0)
    for g in z.get("byCountry", []):
        ckey = (g.get("dimensions", {}).get("clientCountryName", ""),)
        loc_country[ckey] = loc_country.get(ckey, 0) + g.get("count", 0)
    with _lock:
        for k, v in loc_req.items():
            requests_total[k] = requests_total.get(k, 0) + v
        for k, v in loc_bw.items():
            bandwidth_total[k] = bandwidth_total.get(k, 0) + v
        for k, v in loc_country.items():
            requests_country[k] = requests_country.get(k, 0) + v


def poll_loop():
    now = datetime.now(timezone.utc)
    # 確定境界 = now - LAG。そこから LOOKBACK 遡った位置を起点に backfill。
    boundary = _floor_minute(now - timedelta(seconds=LAG_SECONDS))
    last = boundary - timedelta(seconds=LOOKBACK_INIT_SECONDS)
    while True:
        try:
            now = datetime.now(timezone.utc)
            boundary = _floor_minute(now - timedelta(seconds=LAG_SECONDS))
            if boundary > last:
                # Free プランは 1 クエリ最大 1 日。スライスが大きすぎる事はまずないが安全弁。
                end = min(boundary, last + timedelta(hours=23))
                fetch_slice(last, end)
                last = end
                with _lock:
                    _state["last_success_ts"] = time.time()
                    _state["up"] = 1
        except Exception as e:  # noqa: BLE001 - 失敗してもループは継続
            with _lock:
                _state["scrape_errors"] += 1
                _state["up"] = 0
            print("poll error: %s" % e, file=sys.stderr, flush=True)
        time.sleep(SCRAPE_INTERVAL)


def _esc(v):
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_metrics():
    out = []
    z = _esc(CF_ZONE_ID)
    with _lock:
        out.append("# HELP cloudflare_zone_requests_total Requests by host/status/cache (Cloudflare edge, adaptive).")
        out.append("# TYPE cloudflare_zone_requests_total counter")
        for (host, status, cache), v in requests_total.items():
            out.append('cloudflare_zone_requests_total{zone="%s",host="%s",status="%s",cache_status="%s"} %d'
                       % (z, _esc(host), _esc(status), _esc(cache), v))
        out.append("# HELP cloudflare_zone_bandwidth_bytes_total Edge response bytes by host/cache.")
        out.append("# TYPE cloudflare_zone_bandwidth_bytes_total counter")
        for (host, cache), v in bandwidth_total.items():
            out.append('cloudflare_zone_bandwidth_bytes_total{zone="%s",host="%s",cache_status="%s"} %d'
                       % (z, _esc(host), _esc(cache), v))
        out.append("# HELP cloudflare_zone_requests_country_total Requests by client country.")
        out.append("# TYPE cloudflare_zone_requests_country_total counter")
        for (country,), v in requests_country.items():
            out.append('cloudflare_zone_requests_country_total{zone="%s",country="%s"} %d'
                       % (z, _esc(country), v))
        out.append("# HELP cloudflare_zone_exporter_last_success_timestamp_seconds Unix ts of last successful fetch.")
        out.append("# TYPE cloudflare_zone_exporter_last_success_timestamp_seconds gauge")
        out.append('cloudflare_zone_exporter_last_success_timestamp_seconds{zone="%s"} %f' % (z, _state["last_success_ts"]))
        out.append("# HELP cloudflare_zone_exporter_scrape_errors_total Cumulative fetch errors.")
        out.append("# TYPE cloudflare_zone_exporter_scrape_errors_total counter")
        out.append('cloudflare_zone_exporter_scrape_errors_total{zone="%s"} %d' % (z, _state["scrape_errors"]))
        out.append("# HELP cloudflare_zone_exporter_up 1 if last fetch succeeded.")
        out.append("# TYPE cloudflare_zone_exporter_up gauge")
        out.append('cloudflare_zone_exporter_up{zone="%s"} %d' % (z, _state["up"]))
    return ("\n".join(out) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.rstrip("/")
        if path in ("/metrics", ""):
            data = render_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/healthz":
            # poll スレッドが死んでいたら 503。liveness probe がこれを見て Pod を再起動する
            # (/metrics は常に 200 を返すため、これが無いと wedged な exporter が検知されない)。
            alive = _poll_thread is not None and _poll_thread.is_alive()
            self.send_response(200 if alive else 503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n" if alive else b"poll thread dead\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # scrape ログは抑制
        pass


def main():
    if not CF_API_TOKEN or not CF_ZONE_ID:
        print("CF_API_TOKEN and CF_ZONE_ID are required", file=sys.stderr)
        sys.exit(1)
    global _poll_thread
    _poll_thread = threading.Thread(target=poll_loop, daemon=True)
    _poll_thread.start()
    srv = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print("serving metrics on :%d/metrics (zone=%s lag=%ds)" % (LISTEN_PORT, CF_ZONE_ID, LAG_SECONDS), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
