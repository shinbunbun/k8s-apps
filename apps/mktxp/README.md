# mktxp — RouterOS Prometheus Exporter

[akpw/mktxp](https://github.com/akpw/mktxp) を使って RouterOS (192.168.1.1) のメトリクスを Prometheus/VictoriaMetrics に収集する。SNMP から API ベースに移行。

## License

mktxp 本体は GPLv2+。本リポジトリは upstream の公式コンテナイメージ `ghcr.io/akpw/mktxp:1.2.17` を利用するだけで、コード改変/再配布は行わないため制約なし。参考にした Grafana Dashboard #13679 は Apache-2.0 (`apps/grafana-config/dashboards/LICENSE-APACHE-2.0` 参照)。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `deployment.yaml` | Deployment (1 replica, non-root UID 1000) |
| `service.yaml` | ClusterIP Service :49090 (vmagent scrape 対象) |
| `secret-generator.yaml` | KSOPS で secrets/secret.enc.yaml を復号 |
| `secrets/secret.enc.yaml` | mktxp.conf (password 含む) を SOPS-Age 暗号化したもの |
| `kustomization.yaml` | リソース+ksops generator |

## デプロイ手順

### Phase 0: RouterOS 側 (手動)

```rsc
/user group add name=mktxp policy=api,read,rest-api,!write,!policy,!test,!sensitive,!romon,!sniff,!ftp,!telnet
/user add name=mktxp group=mktxp password=<RANDOM_32CHAR>
/ip service set api-ssl disabled=no address=192.168.1.0/24
# API-SSL を使うため api (8728) は基本有効のままで address 制限を追加推奨:
/ip service set api address=192.168.1.0/24
```

検証: `ssh admin@192.168.1.1 "/user print where name=mktxp"` で group=mktxp 確認。翌日の `routeros-backup` CronJob で `routeros-backups/routeros.rsc` に `/ip service` 等が反映される。

### Phase 1: Secret 暗号化 (パスワードを更新する場合)

リポジトリルートの `.sops.yaml` で `apps/*/secrets/*.enc.yaml` の age recipient が設定済み。平文 Secret を用意して sops で in-place 暗号化する:

```bash
cd k8s-apps/apps/mktxp/secrets
cat > secret.yaml <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: mktxp-config
type: Opaque
stringData:
  mktxp.conf: |
    [home-router]
        enabled = True
        hostname = 192.168.1.1
        port = 8729
        username = mktxp
        password = <RouterOS の mktxp ユーザのパスワード>
        use_ssl = True
        # ... (feature toggles は既存の secret.enc.yaml を sops -d で参照)
EOF
cp secret.yaml secret.enc.yaml
sops -e -i secret.enc.yaml   # in-place 暗号化 (.sops.yaml の creation_rules を使用)
rm secret.yaml               # 平文は .gitignore で無視されるが手動削除
git add secret.enc.yaml
```

既存内容を編集するだけなら `sops secret.enc.yaml` で $EDITOR が開き、保存時に自動再暗号化。

### Phase 2: ArgoCD sync

PR マージ後 ApplicationSet が自動検出 (namespace=mktxp)。確認:

```bash
kubectl -n mktxp get deploy,svc,pod
kubectl -n mktxp logs deploy/mktxp --tail=50
kubectl -n mktxp port-forward svc/mktxp 49090:49090
curl -s http://localhost:49090/metrics | grep -c '^mktxp_'
```

## Scrape 設定

`apps/victoriametrics-config/scrape-config-secret.yaml` の `routeros` job を mktxp 向けに書き換える (別 commit で実施)。

## 主要メトリクス対応表 (SNMP → mktxp)

| SNMP | mktxp |
|---|---|
| `hrProcessorLoad` | `mktxp_system_cpu_load` |
| `sysUpTime` | `mktxp_system_uptime` |
| `hrStorageUsed` (mem) | `mktxp_system_total_memory - mktxp_system_free_memory` |
| `ifHCInOctets / ifHCOutOctets` (by `ifDescr`) | `mktxp_interface_rx_byte / _tx_byte` (by `name`) |
| `ifOperStatus` | `mktxp_interface_running` |
| `mtxrInterfaceRxDrop / TxDrop` | `mktxp_interface_rx_drop / _tx_drop` |
| `mtxrInterfaceRxError / TxError` | `mktxp_interface_rx_error / _tx_error` |
| `mtxrDHCPLeaseCount` | `mktxp_dhcp_lease_active_count` |
| (なし) | `mktxp_last_handshake` (WireGuard peer) |
| (なし) | `mktxp_dhcp_lease_info` (per-lease detail) |
| (なし) | `mktxp_ip_connections_total` |
| (なし) | `mktxp_bgp_established` / `_uptime` (BGP) |
| `mtxrSystemRebootCount` | `resets(mktxp_system_uptime[1h])` で近似 |
| `mtxrSystemBadBlocks` | (mktxp に無し) — 廃止 |
| `mtxrSystemUSBPowerResets` | (mktxp に無し、x86 RouterOS では元々対象外) |
