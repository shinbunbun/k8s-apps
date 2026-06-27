# cloudflare-exporter — Cloudflare エッジ分析 Prometheus Exporter

[lablabs/cloudflare-exporter](https://github.com/lablabs/cloudflare-exporter) を使い、Cloudflare の
GraphQL Analytics API から `shinbunbun.com` ゾーンのエッジ分析 (リクエスト数 / 転送バイト /
ステータスコード / 国別 / キャッシュ状況 等) を収集して Prometheus メトリクスとして expose する。

dotfiles-private#381 Phase 2。Phase 1 (cloudflared 自身の `/metrics`、トークン不要) では取れない
「外から見たトラフィック」をエッジ側から取得する。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `deployment.yaml` | Deployment (1 replica, non-root UID 65534, readOnlyRootFilesystem) |
| `service.yaml` | ClusterIP Service :8080 (VMServiceScrape 対象) |
| `configmap.yaml` | **ConfigMap `cloudflare-exporter-config`**: CF_ZONES / FREE_TIER 等の非機密 env |
| `secret-generator.yaml` | KSOPS で secrets/cf-api-token.enc.yaml を復号 |
| `secrets/cf-api-token.enc.yaml` | **Secret `cloudflare-exporter-token`**: `CF_API_TOKEN` のみ (SOPS-Age 暗号化) |
| `vmservicescrape.yaml` | VMServiceScrape (`selectAllByDefault: true` で本 ns から拾われる) |
| `kustomization.yaml` | リソース + ksops generator |

## Cloudflare API トークン

- スコープは **`Zone > Analytics:Read` のみ** (読み取り専用、課金リスクなし)。
- 対象ゾーンは `Zone Resources: Include → Specific zone → shinbunbun.com` に限定。
- `CF_API_EMAIL` / `CF_API_KEY` のレガシー方式は使わない (全リソース書き込み権限で危険)。
- ローテーション手順:
  ```sh
  # 平文を書いて再暗号化 (.sops.yaml の creation_rules が k8s age 公開鍵を選択)
  sops --encrypt --encrypted-regex '^stringData$' --in-place \
    apps/cloudflare-exporter/secrets/cf-api-token.enc.yaml
  ```

## Free プランの制約 (重要)

`shinbunbun.com` は Cloudflare **Free プラン**。GraphQL Analytics API 自体は非課金だが:

- 取得対象は `httpRequestsAdaptiveGroups` 系のみ。WAF/Firewall 詳細・Worker・LB 等の
  premium データセットは取れない → `FREE_TIER: "true"` で premium クエリをスキップ
  (付けないと Paid 専用フィールド要求でエラーになる)。
- 1 クエリの時間範囲は最大 1 日、保持期間も短くサンプリングあり。exporter は短い窓を
  定期取得するため実運用上は問題ない。
- GraphQL クォータは 300 queries / 5min。`SCRAPE_DELAY=300` + scrape interval 60s で十分収まる。

## 主要メトリクス (lablabs cloudflare_zone_*)

| メトリクス | 用途 |
|---|---|
| `cloudflare_zone_requests_total` | ゾーン総リクエスト数 |
| `cloudflare_zone_requests_status` | ステータスコード別リクエスト (4xx/5xx 監視) |
| `cloudflare_zone_requests_country` | 国別リクエスト数 |
| `cloudflare_zone_requests_cached` / `_uncached` | キャッシュヒット可視化 |
| `cloudflare_zone_bandwidth_total` / `_cached` | 帯域 |
