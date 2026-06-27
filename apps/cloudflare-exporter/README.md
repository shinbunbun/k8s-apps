# cloudflare-exporter — Cloudflare Free プラン zone 分析 exporter (自作)

`shinbunbun.com` (Cloudflare **Free プラン**) の zone エッジ分析を GraphQL Analytics API
(`httpRequestsAdaptiveGroups`) から取得し Prometheus メトリクスとして expose する自作 exporter。

dotfiles-private#381 Phase 2。

## なぜ自作か

既製の Prometheus exporter は **Cloudflare Free プランの zone analytics に非対応**:

- **lablabs/cloudflare-exporter**: `fetchZoneTotals` が `httpRequests1mGroups` +
  `firewallEventsAdaptiveGroups` + `healthCheckEventsAdaptiveGroups` を**1 本の結合クエリ**で
  要求する。Free プランはこれらに `does not have access to the path` を返すため、クエリ全体が
  `data:null` で失敗しメトリクスが一切出ない。`FREE_TIER=true` はゾーン除外の有無を変えるだけで
  クエリ構造は変えない (実機・ソース両方で確認済み)。
- **cloudflare/cloudflare-prometheus-exporter (公式)**: Free ゾーンの analytics を自動スキップ。
  かつ Cloudflare Workers + Durable Objects 上で動くため別軸の課金リスクがある。

一方 **`httpRequestsAdaptiveGroups` だけは Free でも使える** (実機確認済み)。そこで「adaptive のみを
叩く」最小 exporter を自作した。GraphQL Analytics API 自体は非課金、k3s 自己ホストなので
追加課金は一切なし。

## 実装方針

- **stdlib のみ** (urllib / json / http.server / threading)。pip 依存なし → 専用イメージ不要、
  素の `python:3.12-slim` + ConfigMap マウントで動く ([[feedback_language_boundary_minimize]] の
  例外: 単機能・小規模なため self-contained script を許容)。
- 累積カウンタ方式: `last` から `now - LAG_SECONDS` までの新スライスを `SCRAPE_INTERVAL` 毎に
  取得しカウンタへ加算 (重複なし)。`LAG_SECONDS=900` で Free の確定ラグ (可変 2〜11 分) を超過。
- 起動時に `LOOKBACK_INIT_SECONDS` 遡って backfill しダッシュボードに即値を出す
  (カウンタを 0 からその合計に立ち上げるだけなので rate() に偽スパイクは出ない)。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `exporter.py` | exporter 本体 (stdlib only)。configMapGenerator で `cloudflare-exporter-code` に格納 |
| `deployment.yaml` | `python:3.12-slim` で `exporter.py` を実行 (non-root, readOnlyRootFilesystem) |
| `service.yaml` | ClusterIP Service :8080 (VMServiceScrape 対象) |
| `kustomization.yaml` | configMapGenerator (code + env、**ハッシュ付与で自動ロール**) + ksops generator |
| `secret-generator.yaml` / `secrets/cf-api-token.enc.yaml` | `CF_API_TOKEN` (SOPS-Age 暗号化) |
| `vmservicescrape.yaml` | VMServiceScrape (`selectAllByDefault` で本 ns から拾われる) |

ConfigMap を **configMapGenerator (ハッシュ付与)** にしてあるため、`exporter.py` や env を変更すると
ConfigMap 名が変わり Deployment が自動でロールする (`envFrom` 直参照だと config 変更で Pod が
再起動しない問題を恒久回避)。

## Cloudflare API トークン

`Zone > Analytics:Read` のみ (読み取り専用、課金リスクなし)、対象ゾーンは shinbunbun.com に限定。
ローテーション:
```sh
sops --encrypt --encrypted-regex '^stringData$' --in-place \
  apps/cloudflare-exporter/secrets/cf-api-token.enc.yaml
```

## 公開メトリクス

| メトリクス | 型 | ラベル |
|---|---|---|
| `cloudflare_zone_requests_total` | counter | `zone, host, status, cache_status` |
| `cloudflare_zone_bandwidth_bytes_total` | counter | `zone, host, cache_status` |
| `cloudflare_zone_requests_country_total` | counter | `zone, country` |
| `cloudflare_zone_exporter_last_success_timestamp_seconds` | gauge | `zone` (staleness 監視用) |
| `cloudflare_zone_exporter_scrape_errors_total` | counter | `zone` |
| `cloudflare_zone_exporter_up` | gauge | `zone` (直近取得の成否) |

## Free プランの制約

- 取得は `httpRequestsAdaptiveGroups` のみ (WAF/Worker/LB/health 等は権限なし)。
- データは約 15 分遅延 (`LAG_SECONDS`)、バースト型 (無トラフィックの分は 0)。
- GraphQL クォータ 300 queries/5min。1 query/min なので十分余裕。
- メトリクスが出ない場合はまず `LAG_SECONDS` を上げる。
