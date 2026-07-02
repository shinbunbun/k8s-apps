# logs-rust-pipeline

Rust + Polars 実装 (`shinbunbun/logs-pipeline` repo) の logs archive pipeline を Argo Workflows で運用する k8s manifest 群。

## 構成

```
workflowtemplate-daily-pipeline.yaml         日次 DAG (4 source × 3 stage + access-attribution / egress-c2 gold)
cronworkflow-daily.yaml                       00:35 JST 起動 schedule
workflowtemplate-backfill.yaml                migrate / silver-backfill / gold-backfill / verify
workflowtemplate-gold-build.yaml              gold 共用 builder (埋め込み DuckDB, table パラメータ切替):
                                                access-attribution (netflow⋈passive-dns⋈ASN) / egress-c2 (C2 検知)
cronworkflow-asn-reference.yaml               IP→ASN/org reference parquet 週次ビルダー
```

## アーキテクチャ

```
            CronWorkflow (00:35 JST)
                    |
                    v
            daily-pipeline (steps, withItems)
                    |
        +-----------+-----------+-----------+
        |           |           |           |
   systemd     routeros    k8s-pods    macos-unified
        |           |           |           |
   source-pipeline (dag) ← per-source 並列
        |
   bronze -> silver -> gold-volume    (真の dependencies)
        |
        v  (全 source 完了後、直列 step group)
   gold-access-attribution -> gold-egress-c2   (templateRef gold-build, table 切替)
```

旧 12 CronJob (Phase 4 で削除) では bronze 00:35 / silver 01:35 / gold 05:00 の壁時計オフセットで stage 依存を表現していた。Phase 3 以降は真の DAG dependencies で連鎖。

## 日次運用 (自動)

CronWorkflow `logs-pipeline-daily` が 00:35 JST に起動。手動操作不要。

監視:
- `argo_workflows_gauge{phase=~"Failed|Error"}` メトリクスベースの VMRule alert `ArgoWorkflowFailed` (apps/victoriametrics-config/vmrule-alerts.yaml)
- Argo UI: `kubectl -n argo port-forward svc/argo-workflows-server 2746:2746` → http://localhost:2746
- `kubectl -n logs-rust-pipeline get workflows`

## 手動 backfill / verify

`logs-pipeline-backfill` WorkflowTemplate の 4 entrypoint を `argo submit` で叩く:

| entrypoint | binary subcommand | 用途 |
|------------|-------------------|------|
| `migrate` | `migrate` | 旧 `daily/` → 新 `daily-struct/` 再パース (idempotent skip) |
| `silver-backfill` | `silver --force` | silver immutable layer の強制再書き |
| `gold-backfill` | `gold-build --table volume --force` | gold/volume/ の強制再書き |
| `verify` | `verify --old-prefix daily --new-prefix daily-struct` | row_count 整合検証 (read-only) |

### 1-day backfill

```bash
argo submit -n logs-rust-pipeline \
  --from workflowtemplate/logs-pipeline-backfill \
  --entrypoint silver-backfill \
  -p source=macos-unified -p date=2026-04-01
```

`--entrypoint` を切り替えるだけで migrate / gold-backfill / verify も同じ syntax。

### Bulk backfill (4 source × N 日)

shell loop で `argo submit` を回す:

```bash
for src in systemd routeros k8s-pods macos-unified; do
  for d in $(seq 0 199 | xargs -I{} date -d "2025-10-14 +{} days" +%Y-%m-%d); do
    argo submit -n logs-rust-pipeline \
      --from workflowtemplate/logs-pipeline-backfill \
      --entrypoint migrate \
      -p source=${src} -p date=${d} \
      --wait   # serial 実行 (cluster 圧迫回避)
  done
done
```

並列実行する場合は `--wait` を外し、cluster の Pod 並列度と各 entrypoint の memory limit (migrate 32Gi / silver 12Gi / gold 4Gi) を踏まえて parallelism を調整。

### access-attribution / egress-c2 gold の backfill

RouterOS アクセス先統計 gold (`gold/access-attribution/`) と egress C2 検知 gold (`gold/egress-c2/`) は daily-pipeline が group 2/3 で自動生成するが、新規導入時や silver 再生成後は共用 WorkflowTemplate `gold-build` を `table` パラメータ付きで直接 submit して遡及生成する (`--force` で COPY 上書き、冪等):

```bash
# 1 day
argo submit -n logs-rust-pipeline \
  --from workflowtemplate/gold-build \
  -p table=access-attribution -p date=2026-06-20

# bulk (silver 期間 2026-06-16 以降を埋める例)
for d in $(seq 0 9 | xargs -I{} date -d "2026-06-16 +{} days" +%Y-%m-%d); do
  argo submit -n logs-rust-pipeline \
    --from workflowtemplate/gold-build \
    -p table=access-attribution -p date=${d} --wait
done
```

- `table` は必須 (`access-attribution` | `egress-c2`)。指定漏れは submit 時に validation error で落ちる。
- `date` 省略時 (daily 経路) は binary が yesterday-jst を解釈する。
- `deadline` / `tmp_size` は省略可: default は安全側の 1800 / 6Gi (egress-c2 相当の上限値。daily-pipeline は table 別実測値 access-attribution=1200/4Gi, egress-c2=1800/6Gi を渡す)。
- `table=egress-c2` の backfill は対象日の access-attribution gold が先に存在している必要がある (D1/D3 の入力)。

## ロールバック

| 問題 | 対応 |
|------|------|
| CronWorkflow 暴走 / 全 source 障害 | `kubectl -n logs-rust-pipeline patch cronworkflow logs-pipeline-daily --type=merge -p '{"spec":{"suspend":true}}'` で一時停止 |
| 1 source だけ問題 | WorkflowTemplate を編集して `withItems` から該当 source を一時除外 |
| WorkflowTemplate 設計バグ | 該当 commit を revert → ArgoCD selfHeal で前 spec に戻る |
| Argo Workflows 自体の障害 | `infrastructure/argo-workflows/` を suspend (ApplicationSet で manage されているため一時 sync 停止) — ただし日次 Workflow が失われる |

## image SHA bump

`logs-pipeline` repo の HEAD bump 時:

1. 該当 PR を merge し、build workflow が `ghcr.io/shinbunbun/logs-pipeline:<sha>` を push
2. 本 directory の以下 3 ファイルで SHA を新 SHA に一括置換 (`sed -i 's#:<旧>#:<新>#g'`):
   - `workflowtemplate-daily-pipeline.yaml` (ingest-stage / gold-volume の 2 image)
   - `workflowtemplate-backfill.yaml` (migrate / silver / gold / verify の 4 image)
   - `workflowtemplate-gold-build.yaml` (build の 1 image — access-attribution / egress-c2 共用)
3. PR を出して ArgoCD sync

なお `deployment-passive-dns.yaml` / `deployment-netflow.yaml` (常駐 collector) も同 repo の image を使うが、batch 系とは独立に pin しており本手順の対象外 (collector 側の変更を含む bump 時のみ個別に更新)。

Phase 2-3 時点で並行していた kustomize images transformer は Phase 4 で廃止済 (CRD path 標準対応外のため WorkflowTemplate に直接書く方式)。
