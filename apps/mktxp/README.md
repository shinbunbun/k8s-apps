# mktxp — RouterOS Prometheus Exporter

[akpw/mktxp](https://github.com/akpw/mktxp) を使って RouterOS (192.168.1.1) のメトリクスを Prometheus/VictoriaMetrics に収集する。SNMP から API ベースに移行。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `deployment.yaml` | Deployment (1 replica, non-root UID 1000) |
| `service.yaml` | ClusterIP Service :49090 (vmagent scrape 対象) |
| `configmap.yaml` | **ConfigMap `mktxp-config`**: feature toggles を含む mktxp.conf (plaintext、git 可視) |
| `secret-generator.yaml` | KSOPS で secrets/credentials.enc.yaml を復号 |
| `secrets/credentials.enc.yaml` | **Secret `mktxp-credentials`**: username/password のみ (SOPS-Age 暗号化、最小) |
| `kustomization.yaml` | リソース+ksops generator |

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
| `health` | x86_64 RouterOS は HW センサー非対応 (温度/電圧/ファン) |
| `wireless` / `wireless_clients` / `capsman` / `capsman_clients` / `w60g` | x86_64 に無線機能なし |
| `poe` | x86_64 に PoE 給電なし |
| `eoip` / `gre` | RouterOS 側で対応 interface 未設定 |
| `ipsec` / `lte` | 機能未使用 / LTE モデム非搭載 |
| `switch_port` | x86_64 に HW スイッチチップなし |
| `bridge_vlan` | `/interface bridge vlan` が空 (VLAN 機能未使用) |
| `kid_control_assigned` / `kid_control_dynamic` | Kid Control 機能未使用 |
| `container` | RouterOS container 機能未使用 (コンテナワークロードは k3s 側で運用) |
| `bfd` | RouterOS 側 `/routing bfd` 未定義 (BGP は keepalive のみ) |
