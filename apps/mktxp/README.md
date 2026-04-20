# mktxp — RouterOS Prometheus Exporter

[akpw/mktxp](https://github.com/akpw/mktxp) を使って RouterOS (192.168.1.1) のメトリクスを Prometheus/VictoriaMetrics に収集する。SNMP から API ベースに移行。

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
# stringData 配下の値のみ暗号化 (apiVersion / kind / metadata は平文のまま)
# 既存 apps/google-calendar-bot/secrets/secret.enc.yaml と同じ方針
sops -e -i --encrypted-regex '^stringData$' secret.enc.yaml
rm secret.yaml
git add secret.enc.yaml
```

既存内容を編集するだけなら `sops secret.enc.yaml` で $EDITOR が開き、保存時に自動再暗号化 (sops metadata に `encrypted_regex` が記録されているため再指定不要)。

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
| (なし) | `mktxp_dhcp_lease_info` (per-lease detail) |
| (なし) | `mktxp_ip_connections_total` |
| (なし) | `mktxp_ip_pool_used` (DHCP プール占有) |
| (なし) | `mktxp_link_downs_total` (link flap 累計) |
| (なし) | `mktxp_bgp_established` / `_uptime` / `_prefix_count` / `_sessions_info` |
| (なし) | `mktxp_bgp_remote_messages_total` / `_local_messages_total` |
| (なし) | `mktxp_bgp_remote_bytes_total` / `_local_bytes_total` |
| (なし) | `mktxp_routes_total_routes` / `_protocol_count{protocol=bgp/connect/...}` (IPv4 / `_ipv6`) |
| (なし) | `mktxp_routing_stats_process_time_total` / `_private_mem` / `_shared_mem` (BGP daemon 可観測性) |
| (なし) | `mktxp_firewall_filter_total` / `_nat_total` / `_mangle_total` (IPv4 / `_ipv6_total`) |
| (なし) | `mktxp_firewall_address_list*` (WG admin/CI, deploy-servers) |
| `mtxrSystemRebootCount` | `resets(mktxp_system_uptime[1h])` で近似 |
| `mtxrSystemBadBlocks` | (mktxp に無し) — 廃止 |
| `mtxrSystemUSBPowerResets` | (mktxp に無し、x86 RouterOS では元々対象外) |

## 無効化されているコレクター (意図的)

| コレクター | 無効理由 |
|---|---|
| `wireguard_peers` | 上流バグ: `last_handshake=None` の peer で `parse_mkt_uptime()` が `TypeError`。1.2.18+ で修正されたら有効化 + ダッシュボード パネル 58 を table 型に復元。関連 issue は [akpw/mktxp](https://github.com/akpw/mktxp) を watch |
| `health` | x86 RouterOS は HW センサー非対応 |
| `wireless` / `capsman` / `poe` | x86 に該当機能なし |
| `netwatch` | RouterOS 側 `/tool netwatch` 未定義 |
| `public_ip` / `dns` / `certificate` / `bfd` | 未導入 (将来的に別 PR で段階的有効化) |
