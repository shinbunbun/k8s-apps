#!/usr/bin/env python3
"""apps/grafana-config/kustomization.yaml の configMapGenerator セクションを生成する。

ダッシュボード JSON ごとに同型 8 行の configMapGenerator ブロックを手書きすると
追加のたびにコピペが必要で漏れ・typo の温床になる。本スクリプトは下記 DASHBOARDS
テーブル (1 行 = 1 ダッシュボード) からブロックを生成し、kustomization.yaml を
出力する。出力は従来の手書き版と byte 単位で等価。

ダッシュボード追加時は DASHBOARDS にタプルを 1 行足して再実行する:
    ./gen-kustomization.py        # kustomization.yaml を上書き生成
    ./gen-kustomization.py --check # 既存ファイルとの差分が無いか確認 (CI 向け)

各タプルのフィールド:
    file   : dashboards/ 配下の JSON ファイル名 (拡張子込み)
    name   : 生成する ConfigMap 名。None なら "grafana-dashboard-<basename>"
    folder : grafana_folder annotation。None なら付与しない
    comment: このブロック直前に出力するコメント行のリスト (先頭ブロック等の注記用)

注意: kustomization.yaml は ArgoCD repo-server が直接読むため、このスクリプトを
実行したら生成結果を必ずコミットすること。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KUSTOMIZATION = HERE / "kustomization.yaml"

# kustomization.yaml の configMapGenerator より前の固定ヘッダ。
HEADER = """\
# Grafana Config - Kustomize エントリポイント
#
# ApplicationSet (applicationsets/apps-appset.yaml) が apps/grafana-config を
# 自動検出して ArgoCD Application を生成する。
# すべてのリソースを grafana namespace にデプロイ。
#
# configMapGenerator で dashboards/*.json を ConfigMap 化し、
# label grafana_dashboard=1 を付与することで Helm chart の dashboard sidecar が
# 自動ロードする (infrastructure/grafana/application.yaml の
# sidecar.dashboards.label / labelValue / searchNamespace 設定と対応)。
#
# commonAnnotations で argocd.argoproj.io/sync-options: ServerSideApply=true を
# 付与。理由: nixos.json ダッシュボードは 474KB あり、ArgoCD の client-side apply
# では kubectl.kubernetes.io/last-applied-configuration annotation に全データが
# コピーされ metadata.annotations 256KiB 上限を超過して ConfigMap apply が失敗する。
# server-side apply は last-applied-configuration を使わないためこの制限を回避できる。
#
# 注意: configMapGenerator セクションは gen-kustomization.py が生成する。
# ダッシュボードの追加・変更は同スクリプトの DASHBOARDS を編集して再実行すること。
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: grafana

commonAnnotations:
  argocd.argoproj.io/sync-options: ServerSideApply=true

resources:
  - postgres-cluster.yaml
  - datasources-configmap.yaml
  - lan-service.yaml
  - objectstore.yaml
  - scheduled-backup.yaml

generators:
  - secret-generator.yaml

configMapGenerator:
"""

# (file, name, folder, comment)
DASHBOARDS = [
    ("overview.json", None, None, None),
    ("nixos.json", None, None, None),
    ("routeros.json", None, None, None),
    ("k8s.json", None, "Kubernetes", None),
    ("logs-overview.json", None, None, None),
    ("logs-errors.json", None, None, None),
    ("logs-explore.json", None, None, None),
    ("routeros-archive.json", "grafana-dashboard-logs-archive-routeros", "Logs Archive", [
        "# Logs Archive 系 (gold layer dashboard) - MyDuck (DuckDB over Garage S3) datasource 経由",
        "# shinbunbun/dotfiles-private#263 Issue で作成、 source ごとに 1 dashboard",
    ]),
    ("systemd-archive.json", "grafana-dashboard-logs-archive-systemd", "Logs Archive", None),
    ("k8s-pods-archive.json", "grafana-dashboard-logs-archive-k8s-pods", "Logs Archive", None),
    ("macos-unified-archive.json", "grafana-dashboard-logs-archive-macos-unified", "Logs Archive", None),
    ("cilium.json", None, "Kubernetes", None),
    ("hubble.json", None, "Kubernetes", None),
    ("hubble-network-overview-namespace.json", None, "Kubernetes", None),
    ("node-exporter-full.json", None, "Kubernetes", None),
    ("k8s-views-global.json", None, "Kubernetes", None),
    ("k8s-views-namespaces.json", None, "Kubernetes", None),
    ("k8s-views-nodes.json", None, "Kubernetes", None),
    ("k8s-views-pods.json", None, "Kubernetes", None),
    ("k8s-system-api-server.json", None, "Kubernetes", None),
    ("victoriametrics-cluster.json", None, "Kubernetes", None),
    ("vmagent.json", None, "Kubernetes", None),
    ("vmalert.json", None, "Kubernetes", None),
    ("argocd-application-overview.json", None, None, None),
    ("argocd-operational-overview.json", None, None, None),
    ("storage-capacity.json", None, None, None),
    ("smartctl-exporter.json", None, None, None),
    ("processes.json", None, None, None),
]


def block(file, name, folder, comment):
    basename = file[:-len(".json")] if file.endswith(".json") else file
    if name is None:
        name = f"grafana-dashboard-{basename}"
    lines = []
    if comment:
        lines.extend(f"  {c}" for c in comment)
    lines.append(f"  - name: {name}")
    lines.append(f"    files:")
    lines.append(f"      - dashboards/{file}")
    lines.append(f"    options:")
    lines.append(f"      labels:")
    lines.append(f'        grafana_dashboard: "1"')
    if folder is not None:
        lines.append(f"      annotations:")
        lines.append(f"        grafana_folder: {folder}")
    lines.append(f"      disableNameSuffixHash: false")
    return "\n".join(lines)


def render():
    parts = [HEADER.rstrip("\n")]
    for d in DASHBOARDS:
        parts.append(block(*d))
    return "\n".join(parts) + "\n"


def main():
    out = render()
    if "--check" in sys.argv:
        cur = KUSTOMIZATION.read_text()
        if cur != out:
            sys.stderr.write(
                "kustomization.yaml が gen-kustomization.py の出力と一致しません。"
                "`./gen-kustomization.py` を実行して再生成してください。\n"
            )
            sys.exit(1)
        print("kustomization.yaml is up to date.")
        return
    KUSTOMIZATION.write_text(out)
    print(f"wrote {KUSTOMIZATION} ({len(DASHBOARDS)} dashboards)")


if __name__ == "__main__":
    main()
