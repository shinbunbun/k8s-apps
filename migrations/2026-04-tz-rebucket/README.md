# logs-archive TZ 再 bucket migration (2026-04)

## 目的

Vector の `aws_s3` sink が `key_prefix` 内 `%Y-%m-%d` を **UTC** 評価していたバグの修正後始末。
既存の歪んだ `daily/` Parquet (約 9.8 GiB、2025-10-14 〜 2026-04-26 + 2015-01-01) を **JST 基準の partition** に再 bucket する。

修正済 (新規データのみ):
- PR #198 (commit 841b0f6): Vector `transforms.enrich` で `.date` / `.hour` を `Asia/Tokyo` で計算
- PR #199 (commit 8964f0a): aws_s3 sink に `framing.method: newline_delimited` を追加 (ndjson 化)
- 適用時刻: 2026-04-27 02:43:20 UTC (Vector pod rotate 完了)

このディレクトリは **Argo CD 管理外** (apps/ 配下ではない)。手動で `kubectl apply -k` する。

## 実行結果 (2026-04-27)

| 項目 | 値 |
|---|---|
| Migration Job 所要時間 | 171 分 (16Gi memory limit, parallelism 4) |
| Indexed Job completions | 196/196 (skip 2 日: 2025-12-09, 12-10 = 元データ無し) |
| Error event | 0 |
| 入力 row 合計 (UTC partition × 2 で重複計上) | 1,573,064,050 |
| 出力 row 合計 (JST partition) | 784,964,256 |
| timestamp 欠落で drop | 3,135,538 (= 0.2%) |
| 出力 partition 数 | 504 |
| 全 partition の JST date 整合性 | match_ratio >= 0.999 |
| Swap 所要時間 | 46 分 |
| daily/ → daily.bak/ 移動 | 29,527 objects |
| daily-jst/ → daily/ 移動 | 504 files |
| raw/.../date=2026-04-27/ 削除 | 476 objects |

## 構成

| ファイル | 役割 |
|---|---|
| `script-configmap.yaml` | `rebucket.py` (本体) と `swap.py` の 2 script を含む ConfigMap |
| `job.yaml` | Indexed Job、completions=196、parallelism=4。1 index = JST 1 日。 |
| `swap-job.yaml` | 検証完了後に手動 resume する swap Job (git 上は `suspend: true`) |
| `kustomization.yaml` | namespace `logs-compact-cronjob` で 3 リソースを束ねる |

### Index → JST date のマッピング

- index 0 = JST 2025-10-14
- index 195 = JST 2026-04-27
- 期間中 input が空白の日 (2026-04-22) は `event=skip` でログ出力後 0 完了
  (UTC 04-21 は Era B 末で JST 04-21 23:00 までしか含まず、UTC 04-22 は Vector 移行直前で出力なし、UTC 04-23 は Era A 開始で JST 04-23 00:00 以降のみ → JST 04-22 を構成する両 UTC partition が事実上空)
- 2015-01-01 は intentionally skip (test/junk 判定)、`daily/date=2015-01-01/` は swap で `daily.bak/` 経由で退避

### 入力 schema (実機調査で判明、3 系統が混在)

実機の `daily/date=*/source=*/*.parquet` は schema が 3 系統に分かれており、出現する partition は以下:

| 形式 | 構造 | 出現 partition |
|---|---|---|
| **MAP** | `json MAP<VARCHAR,VARCHAR>` + `date` + `source` (3 cols) | 2025-10-14, 2025-10-15, 2026-04-12 systemd (計 3 partition) |
| **FLAT** | `@timestamp` 等の通常列 (11..200+ 列、source ごとに大幅に異なる) | 大部分 (計 474 partition) |
| **STRUCT_LIST** | `json STRUCT(...)[]` + `date` + `hour` + `source` (4 cols) | 2026-04-23 .. 2026-04-26 (Era A、計 16 partition) |

各 (UTC date, source) の **最初のファイルの schema を `DESCRIBE` して動的に判定** (`detect_schema_kind()`)、それぞれに対応した SELECT を `build_per_partition_select()` で組む。

`FLAT` の multi-file partition では `union_by_name=true` で読むと file 間の型衝突が起きる (例: `headers STRUCT(host, accept)` vs `headers MAP(VARCHAR, VARCHAR)`)。これを避けるため、FLAT のみ **per-file SELECT を UNION ALL で結合** する (1 partition あたり 30〜200 個の SELECT を連結)。

### 出力 schema (統一)

- **`json` VARCHAR (= 1 record の全フィールドを JSON object 文字列としたもの) のみ 1 列**
- partition path で `date` / `source` を表現
- `timestamp` 抽出は `json_extract_string(json, '$."@timestamp"')` / `'$.timestamp'` / `'$.time'` の COALESCE

これにより、入力 era に関わらず読み手は同じ pipeline で扱える。

### Caveats / 既知の注意点

- **timestamp フィールド欠落レコードの扱い**: `@timestamp` / `timestamp` / `time` のいずれも持たないレコードは `_ts_jst::DATE = ...` で false 判定になり drop される。`event=result` の `null_ts_rows` で観測可能。実測値: 全期間 1.5G 行のうち 3.1M (0.2%) が drop された。
- **Swap 再実行時の安全性**: `swap.py` は step ごとに `_meta/swap-2026-04/stepN-done` marker を bucket root 直下に書く。再実行時は marker 確認で完了 step を skip するため、Step 2 完了後に再起動して Step 1 が走り `daily.bak/` を上書きする事故は起きない。完全にやり直したい場合は `_meta/swap-2026-04/` prefix を全削除してから再実行。
- **`raw/source=*/date={JST_END}/` cleanup**: swap.py の Step 3 で `raw/.../date=2026-04-27/` を削除する。これは Era C の JST 04-27 が migration で完成しているため、CronJob が次回 raw → daily で上書きしないようにする。

## 実行手順

### 0. 前提

- k8s-apps repo の `feature/logs-archive-tz-rebucket` ブランチを merge 済
- nixos-desktop 上の k3s に `logs-compact-cronjob` namespace と `compact-garage-credentials` Secret が存在することを確認
- `kubectl get secret -n logs-compact-cronjob compact-garage-credentials` で Secret 確認

### 1. CronJob suspend (干渉回避)

```bash
kubectl patch cronjob -n logs-compact-cronjob logs-compact \
  -p '{"spec":{"suspend":true}}'
```

理由: migration の最終 index (JST 04-27) が **Era C の `daily/date=2026-04-27/` を read** するため、CronJob が同時刻に新ファイルを追記すると不整合が発生し得る。

### 2. Migration Job 投入

```bash
kubectl apply -k migrations/2026-04-tz-rebucket/
```

`swap-job` は `suspend: true` なのでこの段階では起動しない。

### 3. 進捗観測

```bash
kubectl get job -n logs-compact-cronjob logs-archive-rebucket-2026-04 -w
kubectl get pods -n logs-compact-cronjob -l app=logs-archive-rebucket -w

# JSON line stdout を集計
kubectl logs -n logs-compact-cronjob -l app=logs-archive-rebucket --tail=-1 \
  | jq -c 'select(.event=="result")' \
  | jq -s 'group_by(.jst_date) | map({d:.[0].jst_date, sources:length, in:(map(.in_rows_2partition)|add), out:(map(.out_rows)|add)})'

# エラー検出
kubectl logs -n logs-compact-cronjob -l app=logs-archive-rebucket --tail=-1 \
  | jq -c 'select(.event=="error")'
```

完了条件: `completions: 196 / 196`、`event=error` が 0 件、`event=skip` が `2026-04-22` のみ。

### 4. バリデーション

#### 4.a Job stdout (kubectl logs) 集計

`event=result` の各 record は `in_rows_2partition`, `null_ts_rows`, `jst_match_rows`, `out_rows`, `eras` を含む。
- `out_rows == jst_match_rows` を確認 (filter 結果と書き込み結果が一致)
- `null_ts_rows / in_rows_2partition` がゼロに近いことを確認 (大きいと timestamp 欠落を疑う)

#### 4.b DuckDB ad-hoc 検証

ad-hoc に DuckDB を起動 (例: `kubectl run duckdb-check --rm -it --image=python:3.14-slim ...`) して以下を実行。
**注意**: 全 partition を一度に scan すると 9.8 GiB を読むので、まずはサンプル日に絞って実行し、最終確認のみ全 scan を回す。

```sql
INSTALL httpfs; LOAD httpfs;
SET s3_endpoint='garage.garage.svc.cluster.local:3900';
SET s3_access_key_id='...';
SET s3_secret_access_key='...';
SET s3_use_ssl=false;
SET s3_url_style='path';

-- (A) サンプル日 (2025-11-15) の partition 整合性
SELECT
  (COALESCE(TRY_CAST("@timestamp" AS TIMESTAMPTZ), TRY_CAST("timestamp" AS TIMESTAMPTZ))
   AT TIME ZONE 'Asia/Tokyo')::DATE jst_d,
  COUNT(*) n
FROM read_parquet('s3://logs-archive/daily-jst/date=2025-11-15/source=*/logs.parquet')
GROUP BY 1 ORDER BY 1;
-- 期待: jst_d = 2025-11-15 のみ (他の日付は 0)

-- (B) 全体整合性: 各 daily-jst partition 内で JST date が一致する割合
-- (重い、サンプル日確認後に最終チェック用)
SELECT
  regexp_extract(filename, 'date=([0-9-]+)', 1) part_date,
  regexp_extract(filename, 'source=([^/]+)', 1) source,
  COUNT(*) n_total,
  AVG(CASE WHEN
    (COALESCE(TRY_CAST("@timestamp" AS TIMESTAMPTZ), TRY_CAST("timestamp" AS TIMESTAMPTZ))
     AT TIME ZONE 'Asia/Tokyo')::DATE::VARCHAR
    = regexp_extract(filename, 'date=([0-9-]+)', 1)
  THEN 1.0 ELSE 0.0 END) match_ratio
FROM read_parquet('s3://logs-archive/daily-jst/date=*/source=*/logs.parquet', filename=true)
GROUP BY 1, 2 HAVING match_ratio < 0.999 ORDER BY match_ratio;
-- 期待: 0 行 (全 partition で match_ratio = 1.0)

-- (C) 全体 row 保存 (2015-01-01 を除外、これも全 scan で重い)
WITH
  input AS (
    SELECT COUNT(*) n FROM read_parquet('s3://logs-archive/daily/date=*/source=*/*.parquet', filename=true)
    WHERE filename NOT LIKE '%date=2015-01-01%'
  ),
  output AS (
    SELECT COUNT(*) n FROM read_parquet('s3://logs-archive/daily-jst/date=*/source=*/logs.parquet')
  )
SELECT input.n input_rows, output.n output_rows, input.n - output.n dropped FROM input, output;
-- 期待: dropped はほぼ 0 (Era 境界の bleed の合計)。大きくマイナス → 重複、大きくプラス → drop。
```

### 5. Swap 実行 (検証 OK 時のみ)

```bash
kubectl patch job -n logs-compact-cronjob logs-archive-swap-2026-04 \
  -p '{"spec":{"suspend":false}}'
# 9.8 GiB の object copy + delete を待つ。Garage が server-side copy で同 cluster 内
# 完結するため通常 数分 〜 30 分程度。Era B の小ファイル多発で 1h 超の可能性も。
kubectl wait --for=condition=complete -n logs-compact-cronjob job/logs-archive-swap-2026-04 --timeout=2h
kubectl logs -n logs-compact-cronjob -l app=logs-archive-swap --tail=-1
```

`swap.py` の動作 (各 step 完了時に `_meta/swap-2026-04/stepN-done` marker を bucket に書く):
1. `daily/` 全体を `daily.bak/` に server-side copy + delete (marker: `step1-daily-to-bak-done`)
2. `daily-jst/` を `daily/` に server-side copy + delete (marker: `step2-jst-to-daily-done`)
3. `raw/source=*/date=2026-04-27/` を削除 (marker: `step3-raw-jst-end-cleanup-done`)
   source は `raw/source=*/` を動的 list して導出 (新 source 追加時の取りこぼし防止)。

途中失敗時は marker 確認により未完了 step のみ再実行される。完全やり直しは
`_meta/swap-2026-04/` prefix を全削除してから再実行。

### 6. CronJob unsuspend

```bash
kubectl patch cronjob -n logs-compact-cronjob logs-compact \
  -p '{"spec":{"suspend":false}}'
```

### 7. 後始末 (1 週間後、daily.bak/ 不要を確認後)

```bash
# ad-hoc Job または mc/aws-cli で
# s3://logs-archive/daily.bak/ を削除
```

`migrations/2026-04-tz-rebucket/` ディレクトリは git に残す (履歴保持)。

## ロールバック

### Migration 中 / swap 前

```bash
kubectl delete job -n logs-compact-cronjob logs-archive-rebucket-2026-04
# daily-jst/ 配下を削除 (mc/aws-cli)
kubectl patch cronjob -n logs-compact-cronjob logs-compact -p '{"spec":{"suspend":false}}'
```

`daily/` は無傷。

### Swap 後 (daily.bak/ 残存中)

別 ad-hoc Job で:
1. `daily/` を `daily-jst/` にリネーム (server-side copy + delete)
2. `daily.bak/` を `daily/` にリネーム (server-side copy + delete)
3. `daily.bak/` 確認後削除

## 既存 CronJob との関係

- `logs-compact` CronJob (01:00 JST、`apps/logs-compact-cronjob/`) は通常 `raw/` → `daily/` を作成
- Migration 中は **suspend** にする (上記 step 1)
- Migration が触る範囲:
  - **Read**: `daily/date={UTC range}/` および `daily/date=2026-04-27/` (= Era C)
  - **Write**: `daily-jst/date={JST range}/`
- Migration 完了 + swap 完了後に CronJob を unsuspend する。CronJob は `raw/source=X/date={target_date}/` (target = JST 前日) のみを読むので、swap 後の `daily/` には影響しない
- `swap.py` step 3 で `raw/.../date=2026-04-27/` を削除しているので、04-28 0:00 JST 起動の CronJob は target 04-27 の入力なし → skip となり、merge 済みの `daily/date=2026-04-27/` が保持される
