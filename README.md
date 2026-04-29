# k8s-apps

ArgoCD で管理する Kubernetes アプリケーション定義リポジトリです。

## アプリのデプロイ方法

### 1. テンプレートをコピー

```bash
cp -r apps/_templates/simple-web-app apps/my-app
```

### 2. ファイルを編集

`apps/my-app/deployment.yaml` を開いて、以下を変更します：

- `my-app` → 自分のアプリ名に変更
- `image: nginx:latest` → 使いたい Docker イメージに変更
- `containerPort: 80` → アプリのポートに変更

`apps/my-app/service.yaml` も同様に変更します。

### 3. Git push

```bash
git add apps/my-app/
git commit -m "my-appを追加"
git push
```

### 4. 自動デプロイ

ArgoCD が自動的に検出してデプロイします（約3分以内）。

進捗は [ArgoCD ダッシュボード](https://argocd.shinbunbun.com) で確認できます。

## ディレクトリ構成

```
k8s-apps/
├── bootstrap/          # ArgoCD 初期設定（触らないでOK）
├── projects/           # ArgoCD プロジェクト定義
├── apps/               # ★ ここにアプリを追加
│   ├── _templates/     # テンプレート（コピー元：simple-web-app / web-app-with-secrets）
│   └── example-nginx/  # サンプルアプリ
├── infrastructure/     # インフラ系設定
└── applicationsets/    # 自動検出設定
```

## Secret付きアプリのデプロイ方法

DB接続情報やAPIキーなどのSecretが必要なアプリには `web-app-with-secrets` テンプレートを使います。

### 1. テンプレートをコピー

```bash
cp -r apps/_templates/web-app-with-secrets apps/my-app
```

### 2. ファイルを編集

`deployment.yaml` と `service.yaml` のアプリ名・イメージ・ポートを変更します。

### 3. Secretを作成・暗号化

```bash
# 平文でSecretを作成
cat > apps/my-app/secrets/secret.yaml << 'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: my-app-secret
stringData:
  DATABASE_URL: "postgres://user:pass@host:5432/db"
  API_KEY: "sk-xxxx"
EOF

# SOPSで暗号化（リポジトリの.sops.yamlが自動的に鍵を選択）
sops -e apps/my-app/secrets/secret.yaml > apps/my-app/secrets/secret.enc.yaml

# 平文を削除（重要！）
rm apps/my-app/secrets/secret.yaml
```

### 4. Git push

```bash
git add apps/my-app/
git commit -m "my-appを追加"
git push
```

ArgoCD が KSOPS を使って暗号化 Secret を自動復号し、Kubernetes Secret を作成します。

## よく使う Docker イメージ

| イメージ | 説明 |
|---------|------|
| `nginx:stable-alpine` | Webサーバー |
| `httpd:alpine` | Apache Webサーバー |
| `python:3-slim` | Python |
| `node:lts-alpine` | Node.js |

## アプリの削除

`apps/` 配下のフォルダを削除して push すると、自動的にアプリも削除されます。

```bash
rm -rf apps/my-app
git add -A
git commit -m "my-appを削除"
git push
```

## ノードメンテナンス手順

ノード停止 (再起動 / シャットダウン / nixos-rebuild) を行う場合は **必ず drain してから** 行うこと。CoreDNS や Authentik など特定ノードに張り付いた状態で停止すると、クラスタ全体の DNS 解決が壊れて Authentik 経由の認証 (Grafana 等) が 502 になる事象が確認されている。

### 手順

```bash
# 1. drain (新規スケジュール拒否 + Pod 退避)
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data

# 2. drain 完了を待機 (PDB 違反があれば自動で待つ)
#    Pod が他ノードへ全て移動するまでブロック

# 3. メンテ作業 (物理 / nixos-rebuild / shutdown)

# 4. ノード復帰後、再びスケジュール可能にする
kubectl uncordon <node>
```

### 注意事項

- PDB が drain をブロックする場合は `kubectl get pdb -A` で該当 PDB の `ALLOWED DISRUPTIONS` を確認
- 3ノードクラスタなので **同時に複数ノードを drain しない** こと (etcd quorum 2/3)
- 計画外停止 (障害) 時は `node.kubernetes.io/unreachable:NoExecute` toleration の 5分経過後に Pod が他ノードへ自動再スケジュールされる
