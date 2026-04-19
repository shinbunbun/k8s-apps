# scanopy — 物理/ネットワーク構成図の自動生成

[Scanopy](https://scanopy.net) Community Edition を k3s 上で self-host し、LAN (192.168.1.0/24 / 192.168.128.0/24) の物理ホスト・RouterOS・k3s ノードをディスカバリしてトポロジ図を自動生成する。

関連 Issue: [shinbunbun/dotfiles#509](https://github.com/shinbunbun/dotfiles/issues/509)

## アーキテクチャ

```
[Cloudflare Edge]
  └─ scanopy.shinbunbun.com
       └─ [cloudflared-k3s] → Service scanopy-server (ClusterIP :60072)
                                └─ Deployment scanopy-server (PVC /data 5Gi)
                                     └─ CNPG Cluster scanopy-db (PostgreSQL 17)
                                          └─ Barman Cloud Plugin → MinIO

[LAN 192.168.1.0/24]
  └─ Service scanopy-lan (LoadBalancer VIP 192.168.128.16:60072)
       └─ Deployment scanopy-server

[3 × k3s nodes]
  └─ DaemonSet scanopy-daemon (hostNetwork: true, cap NET_RAW + NET_ADMIN)
       └─ SCANOPY_SERVER_URL → scanopy-server.scanopy.svc.cluster.local:60072
       └─ LAN/RouterOS (SNMP v2c RO) discovery
```

## ファイル構成

| ファイル | 役割 |
|---|---|
| `postgres-cluster.yaml` | CNPG Cluster (instances: 2, storage 2Gi, Barman) |
| `objectstore.yaml` | Barman Cloud ObjectStore (MinIO) |
| `scheduled-backup.yaml` | 毎日 03:30 JST の base backup |
| `server-pvc.yaml` | /data 用 PVC (local-path, 5Gi) |
| `server-deployment.yaml` | Scanopy server Deployment (replicas: 1, Recreate) |
| `server-service.yaml` | ClusterIP Service (cloudflared 用) |
| `server-lan-service.yaml` | LoadBalancer Service (LAN VIP 192.168.128.16) |
| `daemon-daemonset.yaml` | Scanopy daemon DaemonSet (hostNetwork, 全ノード) |
| `secret-generator.yaml` | KSOPS generator |
| `secrets/scanopy-db-app.enc.yaml` | DB basic-auth secret (CNPG bootstrap 参照) |
| `secrets/scanopy-secrets.enc.yaml` | daemon API key + OIDC client_secret |
| `secrets/cnpg-minio-credentials.enc.yaml` | MinIO 資格情報 (他 CNPG アプリから複製) |

シークレットは**事前プロビジョン方式**で本リポジトリに暗号化コミット済み。
`daemon-api-key` / `oauth-client-secret` は openssl で生成した強ランダム値 (64B+) を
Authentik 側と Scanopy UI 側に**同値で入力**する運用。

事前値の取得:
```bash
sops -d apps/scanopy/secrets/scanopy-secrets.enc.yaml
```

## デプロイ手順

### Phase 0: RouterOS SNMP 有効化 (推奨)

Scanopy の RouterOS 検出は SNMP 情報で強化される。未設定の場合は以下を 192.168.1.1 で実行:

```rsc
/snmp community add name=scanopy addresses=192.168.1.0/24,192.168.128.0/24 read-access=yes
/snmp set enabled=yes contact="shinbunbun" location="home"
```

設定後は翌日の `routeros-backup` CronJob が `routeros-backups` リポに反映する。community name (`scanopy`) は Scanopy UI 側の RouterOS デバイス設定にも入力する。

### Phase 1: Authentik OIDC Provider 作成 (初回デプロイ前)

Authentik 管理 UI (`https://auth.shinbunbun.com/if/admin/`) で以下を作成。Client Secret は
事前プロビジョン済みの `scanopy-secrets.oauth-client-secret` を貼り付ける
(`sops -d apps/scanopy/secrets/scanopy-secrets.enc.yaml` で取得):

1. **Providers > Create > OAuth2/OpenID Provider**
   - Name: `scanopy`
   - Client type: Confidential
   - Client ID: `scanopy`
   - Client Secret: 事前値をコピペ
   - Redirect URIs: `https://scanopy.shinbunbun.com/oauth/callback`
   - Signing Key: authentik Self-signed Certificate
   - Scopes: openid / profile / email
2. **Applications > Create**
   - Name: `Scanopy`
   - Slug: `scanopy`
   - Provider: `scanopy`
   - Launch URL: `https://scanopy.shinbunbun.com`

### Phase 2: k8s デプロイ

PR マージ → ApplicationSet 自動検出 (namespace=scanopy) で server/daemon/CNPG が起動:

```bash
kubectl -n scanopy get cluster scanopy-db               # primary/standby ready
kubectl -n scanopy get deploy scanopy-server             # READY 1/1
kubectl -n scanopy get ds scanopy-daemon                 # DESIRED=3 READY=3
kubectl -n scanopy logs deploy/scanopy-server --tail=50  # migration 完了確認
```

### Phase 3: Scanopy 初期管理ユーザ + Daemon 登録

LAN 内ブラウザで http://192.168.128.16:60072 にアクセス → 初回ロード時の登録画面で管理者作成。
Settings > Daemons で API Key 登録時に**事前値 `daemon-api-key`** を入力
(UI が新規生成を要求する場合は生成値を Secret 側に反映して `sops -i` で再暗号化 → push)。

### Phase 4: Scanopy 側 OIDC 有効化

Scanopy UI > Settings > Authentication > OIDC で以下を入力:
- Issuer: `https://auth.shinbunbun.com/application/o/scanopy/`
- Client ID: `scanopy`
- Client Secret: 事前値 `scanopy-secrets.oauth-client-secret` (Phase 1 で Authentik に入れたものと同じ)
- Redirect URI: `https://scanopy.shinbunbun.com/oauth/callback`

保存後、ログアウトして OIDC ボタンからログインできることを確認。

## 検証

```bash
# すべてのリソース確認
kubectl -n scanopy get all,cluster,objectstore,scheduledbackup,pvc

# daemon が 3 ノードに配置されているか
kubectl -n scanopy get ds scanopy-daemon
# DESIRED=3 CURRENT=3 READY=3

# daemon → server 接続確認
kubectl -n scanopy logs ds/scanopy-daemon --tail=20 | grep -i register

# server ヘルスチェック (LAN VIP)
curl -s http://192.168.128.16:60072/api/health

# バックアップ実行確認
kubectl -n scanopy get backup
```

UI を開いて、以下が discovery に出現することを確認:
- `nixos-desktop` (192.168.1.4) / `homeMachine` (192.168.1.3)
- `macmini` (192.168.1.5) / `g3pro` (192.168.1.6)
- `RouterOS` (192.168.1.1) ← SNMP 有効時は機種名 MikroTik、未有効は Generic SNMP

## ライセンス

Scanopy Community Edition は **AGPL-3.0**。self-host で内部利用する限りソース開示義務は発生しないが、改変版を外部サービスとして提供する場合はソース公開が必要。現状は**無改変でコンテナイメージを使うのみ**。

## 関連リソース

- LAN VIP 一覧: Traefik 10 / Grafana 11 / vmselect 12 / Alertmanager 13 / Loki 14 / Hubble 15 / **scanopy 16**
- cloudflared route: `apps/cloudflared-k3s/configmap.yaml`
- 既存パターン参照: `apps/grafana-config/` / `apps/authentik-config/`
