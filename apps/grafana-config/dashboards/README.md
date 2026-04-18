# Dashboards

Grafana Helm chart の sidecar が `grafana_dashboard=1` ラベル付き ConfigMap を自動ロードする。
`kustomization.yaml` の `configMapGenerator` で追加・削除を行う。

## サードパーティ由来（Apache License 2.0）

以下は Apache License 2.0 (http://www.apache.org/licenses/LICENSE-2.0) の下で配布されている。
ライセンス本文は [LICENSE-APACHE-2.0](./LICENSE-APACHE-2.0) を参照。

| ファイル | 出典 | 著作権者 |
|----------|------|----------|
| `node-exporter-full.json` | [rfmoz/grafana-dashboards](https://github.com/rfmoz/grafana-dashboards) (Grafana.com ID 1860) | rfmoz |
| `k8s-views-global.json` | [dotdc/grafana-dashboards-kubernetes](https://github.com/dotdc/grafana-dashboards-kubernetes) (Grafana.com ID 15757) | David Calvert (dotdc) |
| `k8s-views-namespaces.json` | 同上 (ID 15758) | David Calvert (dotdc) |
| `k8s-views-nodes.json` | 同上 (ID 15759) | David Calvert (dotdc) |
| `k8s-views-pods.json` | 同上 (ID 15760) | David Calvert (dotdc) |
| `k8s-system-api-server.json` | 同上 (ID 15761) | David Calvert (dotdc) |
| `victoriametrics-cluster.json` | [VictoriaMetrics/VictoriaMetrics](https://github.com/VictoriaMetrics/VictoriaMetrics/tree/master/dashboards) | VictoriaMetrics, Inc. |
| `vmagent.json` | VictoriaMetrics 公式 (Grafana.com ID 12683) | VictoriaMetrics, Inc. |
| `vmalert.json` | VictoriaMetrics 公式 (Grafana.com ID 14950) | VictoriaMetrics, Inc. |

## 自作 / 社内配布

以下は本リポジトリで作成したダッシュボードで、外部ライセンス適用対象外。

- `overview.json`
- `nixos.json`
- `routeros.json`
- `k8s.json`
- `logs-overview.json` / `logs-errors.json` / `logs-explore.json`
- `argocd-application-overview.json` / `argocd-operational-overview.json`

## その他

- `cilium.json` / `hubble.json` / `hubble-network-overview-namespace.json` は
  [cilium/cilium](https://github.com/cilium/cilium) リポジトリ (Apache License 2.0) 由来。
