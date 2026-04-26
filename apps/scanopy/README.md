# scanopy

[Scanopy](https://scanopy.net) Community Edition (AGPL-3.0) を k3s 上で self-host し、LAN / RouterOS / k3s ノードのトポロジ図を自動生成する。

- 外部 UI: https://scanopy.shinbunbun.com (cloudflared → Authentik SSO)
- LAN UI: http://192.168.128.16:60072

関連 Issue: [shinbunbun/dotfiles#509](https://github.com/shinbunbun/dotfiles/issues/509)

## 構成

- `postgres-*.yaml` : `postgres:17-alpine` 単一 Pod + Piraeus PVC 2Gi
  - upstream [scanopy/scanopy](https://github.com/scanopy/scanopy/blob/main/docker-compose.yml) の compose.yml が 17 を pin しており、scanopy server image が pg_dump 17 client を内蔵しているため、major は upstream 追従。Renovate でも major を抑止 (`renovate.json5`)
- `server-*.yaml` : server Deployment + PVC 5Gi + ClusterIP + LAN LB (192.168.128.16)
- `daemon-deployment.yaml` : hostNetwork Deployment replicas:1 (CAP_NET_RAW/ADMIN、同一 LAN なので 1 Pod で十分)
- `secrets/scanopy-postgres.enc.yaml` : DB username/password
- `secrets/scanopy-secrets.enc.yaml` : daemon API key / OIDC client_secret (pre-provisioned 強ランダム値)

discovery データは再スキャンで復元可能なので CNPG HA / Barman は不要。
