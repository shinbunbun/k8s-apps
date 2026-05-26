#!/usr/bin/env bash
# gold layer backfill orchestrator: suspend → submit → wait → resume → verify
#
# 経緯: shinbunbun/dotfiles-private#201 Phase 6 backfill
# 依存: shinbunbun/logs-pipeline#26 (gold-build subcommand) + #27 (bulk first-seen)
#       shinbunbun/k8s-apps#XXX (本 PR、backfill manifests + 本 script)
#
# 【全体フロー】
# 0. 前提チェック (kubectl 接続、image SHA pin)
# 1. daily cron suspend (first-seen 3 source、bulk と race 防止)
# 2. 3 phase で Job 投入:
#    Phase A: bulk first_seen (3 source × 1 job、source 並列)
#    Phase B: volume (4 source × 全 day = ~625、xargs -P 10 で投入)
#    Phase C: template_rate (3 source × 全 day = ~404、xargs -P 5)
# 3. 全 Job 完了 wait (kubectl wait --selector tier=gold-backfill)
# 4. daily cron resume
# 5. coverage verify
#
# 【MAX_PARALLEL / 並列度ガイドライン (24-core node、2026-05-15 ハング事件踏まえ)】
# - 各 Job CPU limit 2000m (POLARS_MAX_THREADS=2)
# - volume: MAX_PARALLEL=10 → 20 core peak
# - rate:   MAX_PARALLEL=5  → 10 core peak
# - bulk first_seen: 3 並列 → 6 core peak
# - 同時実行で最大 36 core 但し limit、request ベースは 18 × 500m = 9 core
# - 実 peak は CPU 専有時間が短い (S3 I/O + Polars 集計) ので衝突少ない見込み
#
# 【usage】
#   DRY_RUN=1 ./backfill-gold-orchestrator.sh  # 投入せず print のみ
#   ./backfill-gold-orchestrator.sh             # 本実行
#
# 【想定実行時間】
# - bulk first_seen: systemd 35 分 (bottleneck)
# - volume backfill: ~30 分 (625 job × ~30 秒 ÷ 10 並列)
# - template_rate: ~25 分 (404 job × ~45 秒 ÷ 5 並列)
# 全 phase 並走、bottleneck = bulk first_seen ~35-40 分
set -euo pipefail

NS="logs-rust-pipeline"
TEMPLATE_DIR="apps/logs-rust-pipeline"
VOLUME_PARALLEL="${VOLUME_PARALLEL:-10}"
RATE_PARALLEL="${RATE_PARALLEL:-5}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-14400s}"  # 4h、bottleneck 35-40 分 + margin
DRY_RUN="${DRY_RUN:-0}"

# DATE_TO 既定値: 昨日 JST (daily cron が 5/25 を埋めた、5/26 dawn まで埋める)。
# DATE_FROM 既定値: source ごとの最古 silver date (Issue #201 closing comment §2 参照)。
# silver の date range は source 別、bulk first_seen は intersection で自動処理されるが
# volume / template_rate は (source, date) 明示が必要。
DATE_TO="${DATE_TO:-2026-05-24}"

# (source, start_date) のマッピング。end は DATE_TO 共通だが、macos-unified だけ Issue E で 5/21 まで。
declare -A SOURCE_START=(
  [systemd]="2025-10-15"
  [routeros]="2025-10-18"
  [k8s-pods]="2026-02-21"
  [macos-unified]="2026-02-22"
)
declare -A SOURCE_END=(
  [systemd]="$DATE_TO"
  [routeros]="$DATE_TO"
  [k8s-pods]="$DATE_TO"
  [macos-unified]="2026-05-21"  # silver Issue E で 5/22 以降 silver 不在
)

# template_rate / template_first_seen 対象 source (#201 設計で routeros 除外)
TEMPLATE_SOURCES=("systemd" "k8s-pods" "macos-unified")

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
do_or_print() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY_RUN: %s\n' "$*"
  else
    "$@"
  fi
}

# Phase 1 で suspend した daily first-seen CronJob を resume する。
# trap EXIT で wait loop 失敗 / kubectl エラー時にも確実に呼ばれる。
resume_crons() {
  log "trap/resume: daily first-seen CronJobs を resume"
  for source in "${TEMPLATE_SOURCES[@]}"; do
    cj="logs-rust-pipeline-gold-first-seen-$source"
    do_or_print kubectl -n "$NS" patch cronjob "$cj" -p '{"spec":{"suspend":false}}' || true
  done
}
trap resume_crons EXIT

# date 範囲を YYYY-MM-DD で出力 (start, end inclusive)
date_range() {
  local start=$1 end=$2
  local days
  days=$(( ($(date -d "$end" +%s) - $(date -d "$start" +%s)) / 86400 + 1 ))
  for i in $(seq 0 $((days - 1))); do
    date -d "$start +$i days" +%Y-%m-%d
  done
}

# 1 つの Job template を sed 置換して投入
submit_job() {
  local template=$1 source=$2 date=$3
  local resolved
  resolved=$(sed -e "s/{{SOURCE}}/$source/g" -e "s/{{DATE}}/$date/g" "$template")
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY_RUN: submit %s source=%s date=%s\n' "$template" "$source" "$date"
  else
    echo "$resolved" | kubectl create -f -
  fi
}

# xargs から呼ばれる per-job wrapper (export 必要)
submit_volume_job() {
  submit_job "$TEMPLATE_DIR/backfill-gold-volume-job-template.yaml" "$1" "$2"
}
submit_rate_job() {
  submit_job "$TEMPLATE_DIR/backfill-gold-template-rate-job-template.yaml" "$1" "$2"
}
export -f submit_job submit_volume_job submit_rate_job
export DRY_RUN TEMPLATE_DIR

# =============================================================================
# 0. 前提チェック
# =============================================================================
log "Phase 0: prerequisite check"
if ! kubectl -n "$NS" get cronjob logs-rust-pipeline-gold-first-seen-systemd >/dev/null 2>&1; then
  echo "ERROR: gold CronJobs not found in namespace $NS. Apply k8s-apps#415 first." >&2
  exit 1
fi
log "  kubectl OK, gold CronJobs exist in $NS"

# =============================================================================
# 1. daily cron suspend (first-seen 3 source、bulk と race 防止)
# =============================================================================
log "Phase 1: suspend daily first-seen CronJobs"
for source in "${TEMPLATE_SOURCES[@]}"; do
  cj="logs-rust-pipeline-gold-first-seen-$source"
  do_or_print kubectl -n "$NS" patch cronjob "$cj" -p '{"spec":{"suspend":true}}'
done

# =============================================================================
# Phase A: bulk first_seen (3 source、source 並列)
# =============================================================================
log "Phase A: submit bulk first-seen Jobs (3 source、parallel)"
for source in "${TEMPLATE_SOURCES[@]}"; do
  manifest="$TEMPLATE_DIR/backfill-gold-first-seen-bulk-${source}.yaml"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY_RUN: kubectl create -f %s\n' "$manifest"
  else
    kubectl create -f "$manifest"
  fi
done

# =============================================================================
# Phase B: volume (4 source × all date、MAX_PARALLEL=10)
# =============================================================================
log "Phase B: submit volume backfill Jobs (parallel=$VOLUME_PARALLEL)"
volume_count=0
for source in "${!SOURCE_START[@]}"; do
  start="${SOURCE_START[$source]}"
  end="${SOURCE_END[$source]}"
  for d in $(date_range "$start" "$end"); do
    echo "$source $d"
    volume_count=$((volume_count + 1))
  done
done | xargs -P "$VOLUME_PARALLEL" -I {} sh -c 'submit_volume_job $1' -- {}
log "  submitted ~$volume_count volume Jobs"

# =============================================================================
# Phase C: template_rate (3 source × all date、MAX_PARALLEL=5)
# =============================================================================
log "Phase C: submit template_rate backfill Jobs (parallel=$RATE_PARALLEL)"
rate_count=0
for source in "${TEMPLATE_SOURCES[@]}"; do
  start="${SOURCE_START[$source]}"
  end="${SOURCE_END[$source]}"
  for d in $(date_range "$start" "$end"); do
    echo "$source $d"
    rate_count=$((rate_count + 1))
  done
done | xargs -P "$RATE_PARALLEL" -I {} sh -c 'submit_rate_job $1' -- {}
log "  submitted ~$rate_count template_rate Jobs"

# =============================================================================
# 3. 全 Job 完了 wait
# =============================================================================
log "Phase 3: wait all backfill Jobs (selector tier=gold-backfill, timeout=$WAIT_TIMEOUT)"
if [[ "$DRY_RUN" == "1" ]]; then
  printf 'DRY_RUN: skip wait\n'
else
  # gold-backfill tier Job が全部 condition Complete/Failed のいずれかになるまで poll。
  # status.succeeded >= 1 (= Complete) または status.failed >= backoffLimit+1 (= Failed) で完了扱い。
  # ttlSecondsAfterFinished=600/1200 で完了 Job が消えるので、残数は減少のみ。
  # WAIT_TIMEOUT (default 4h) を超えたら抜けて trap で resume + 失敗扱い exit。
  deadline=$(( $(date +%s) + ${WAIT_TIMEOUT%s} ))
  while true; do
    if [[ $(date +%s) -gt $deadline ]]; then
      log "ERROR: WAIT_TIMEOUT ($WAIT_TIMEOUT) 経過、未完了 Job が残っています"
      kubectl get jobs -n "$NS" -l tier=gold-backfill \
        -o jsonpath='{range .items[?(@.status.succeeded<1)]}{.metadata.name}{"\n"}{end}' >&2 || true
      exit 1
    fi
    # active = まだ実行中 / pending な Job 数 (status.active が >= 1)
    # ttl で消えた Job + 未完了 Job のみ remaining としてカウントしないため、active のみ見る
    active=$(kubectl get jobs -n "$NS" -l tier=gold-backfill \
      -o jsonpath='{.items[*].status.active}' 2>/dev/null \
      | tr ' ' '\n' | awk '{s+=$1} END {print s+0}')
    log "  active Jobs: $active"
    if [[ "$active" -eq 0 ]]; then break; fi
    sleep 60
  done
  log "  all active Jobs settled"

  # 失敗 Job をリスト (informational、ttl で消えてなければ取れる)
  failed=$(kubectl get jobs -n "$NS" -l tier=gold-backfill \
    -o jsonpath='{range .items[?(@.status.failed>=1)]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
  if [[ -n "$failed" ]]; then
    log "WARNING: failed Jobs (要手動再投入):"
    echo "$failed" | sed 's/^/    /' >&2
  fi
fi

# =============================================================================
# 4. daily cron resume は trap EXIT で実行される (resume_crons 関数)
# =============================================================================
log "Phase 4: daily first-seen CronJobs resume は trap EXIT で行われます"

# =============================================================================
# 5. coverage verify (informational、別途 DuckDB MCP 等で詳細確認)
# =============================================================================
log "Phase 5: done. Verify coverage via DuckDB MCP query:"
cat <<'EOF'
SELECT
  regexp_extract(file, 'gold/([^/]+)/source=([^/]+)/date=([^/]+)/', 1) AS table,
  regexp_extract(file, 'gold/([^/]+)/source=([^/]+)/date=([^/]+)/', 2) AS source,
  count(DISTINCT regexp_extract(file, 'gold/([^/]+)/source=([^/]+)/date=([^/]+)/', 3)) AS days
FROM glob('s3://logs-archive/gold/**/*.parquet')
GROUP BY table, source ORDER BY table, source;
EOF
