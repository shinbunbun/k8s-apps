# scanopy

[Scanopy](https://scanopy.net) Community Edition (AGPL-3.0) を k3s 上で self-host し、LAN / RouterOS / k3s ノードのトポロジ図を自動生成する。

- 外部 UI: https://scanopy.shinbunbun.com (cloudflared → Authentik SSO)
- LAN UI: http://192.168.128.16:60072

関連 Issue: [shinbunbun/dotfiles#509](https://github.com/shinbunbun/dotfiles/issues/509)

## 構成

- `postgres-*.yaml` : `postgres:17-alpine` 単一 Pod + Piraeus PVC 2Gi
- `server-*.yaml` : server Deployment + PVC 5Gi + ClusterIP + LAN LB (192.168.128.16)
- `daemon-daemonset.yaml` : hostNetwork DaemonSet (CAP_NET_RAW/ADMIN) 全ノード
- `secrets/scanopy-postgres.enc.yaml` : DB username/password
- `secrets/scanopy-secrets.enc.yaml` : daemon API key / OIDC client_secret (pre-provisioned 強ランダム値)

discovery データは再スキャンで復元可能なので CNPG HA / Barman は不要。

## 初回セットアップ

事前値の取得: `sops -d apps/scanopy/secrets/scanopy-secrets.enc.yaml`

1. **Authentik** で OAuth2/OIDC Provider `scanopy` を作成 (Client Secret に事前値を貼付、Redirect URI: `https://scanopy.shinbunbun.com/oauth/callback`)、Application `scanopy` を紐付け
2. **PR マージ** → ApplicationSet 自動検出 (namespace=scanopy)
3. **Scanopy UI** (http://192.168.128.16:60072) で初期管理ユーザ登録 → Daemon API Key 設定に事前値を入力 → Settings > Authentication > OIDC 有効化
4. **RouterOS SNMP** (任意、検出強化):
   ```
   /snmp community add name=scanopy addresses=192.168.1.0/24,192.168.128.0/24 read-access=yes
   /snmp set enabled=yes
   ```
