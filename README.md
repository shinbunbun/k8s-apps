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
│   ├── _templates/     # テンプレート（コピー元）
│   └── example-nginx/  # サンプルアプリ
├── infrastructure/     # インフラ系設定
└── applicationsets/    # 自動検出設定
```

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
