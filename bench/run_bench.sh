#!/usr/bin/env bash
# k6 comparison, one target at a time: bash bench/run_bench.sh old|new [files] [rounds]
# The other service is stopped first, so each target runs alone on the machine.
set -euo pipefail
export MSYS_NO_PATHCONV=1

abspath() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
HERE=$(abspath "$(dirname "${BASH_SOURCE[0]}")")
NEW=${NEW:-$(abspath "$HERE/..")}
TARGET=${1:?old or new}
FILES=${2:-A,B,C,D,E}
ROUNDS=${3:-3}
RESULTS=$NEW/bench/results
RUN_ID=${RUN_ID:-$TARGET-$(date +%Y%m%d-%H%M%S)}
NET=${SHARED_NETWORK:-synchrono-shared}
mkdir -p "$RESULTS"
chmod a+rwx "$RESULTS" 2>/dev/null || true

if [ "$TARGET" = old ]; then
  bash "$HERE/stack.sh" stop-new
  docker start srb-old >/dev/null
  BASE=http://srb-old:8000
  WATCH="srb-old srb-s3"
  PROCS=""
else
  bash "$HERE/stack.sh" stop-old
  bash "$HERE/stack.sh" new >/dev/null
  BASE=http://srb-new-api:8000
  WATCH="srb-new-api srb-new-worker-grading srb-new-worker-matching srb-new-valkey srb-s3"
  # A StarRocks installed on this host (not in Docker) is sampled per process, in cores and MB.
  PROCS=${SAMPLE_PROCESSES:-sr-fe=com.starrocks.StarRocksFE,sr-be=lib/starrocks_be}
fi
sleep 10

docker rm -f srb-sampler srb-k6 >/dev/null 2>&1 || true
docker run -d --name srb-sampler --network "$NET" --pid=host --env-file "$NEW/.env.bench" \
  -e SAMPLE_PROCESSES="$PROCS" -e SAMPLE_BACKENDS="${SAMPLE_BACKENDS:-}" \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$NEW:/srv:ro" -v "$RESULTS:/results" \
  synchrono-service-starrocks:dev python /srv/bench/sampler.py \
  "/results/sampler-$TARGET-$RUN_ID.csv" $WATCH >/dev/null

docker run -d --name srb-k6 --network "$NET" -v "$NEW/bench/k6:/scripts:ro" \
  -v "$RESULTS:/results" -e TARGET="$TARGET" -e BASE="$BASE" -e FILES="$FILES" -e ROUNDS="$ROUNDS" \
  -e RUN_ID="$RUN_ID" -e NOISE_RPS="${NOISE_RPS:-2}" grafana/k6:latest run --quiet \
  /scripts/compare.js >/dev/null
echo "[bench] $TARGET running (k6 container srb-k6) ..."
until [ "$(docker inspect srb-k6 --format '{{.State.Status}}' 2>/dev/null)" = "exited" ]; do sleep 10; done
docker logs srb-k6 > "$RESULTS/k6-$TARGET-$RUN_ID.log" 2>&1
grep -E "RESULT|level=error" "$RESULTS/k6-$TARGET-$RUN_ID.log" | sed 's/.*RESULT //' | cut -c1-200 || true
docker rm -f srb-k6 srb-sampler >/dev/null 2>&1 || true
echo "[bench] $TARGET done: $RESULTS/k6-$TARGET-$RUN_ID.log"
