#!/usr/bin/env bash
# Run the k6 comparison for one target at a time: bash run_bench.sh old|new [files] [rounds]
# Each target runs alone on the laptop: the other service is stopped first.
set -euo pipefail
export MSYS_NO_PATHCONV=1

TARGET=${1:?old or new}
FILES=${2:-A,B,C,D,E}
ROUNDS=${3:-3}
NEW=${NEW:-D:/ISGS/PROJECT/synchrono/synchrono-service-starrocks}
RESULTS=$NEW/bench/results
RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$RESULTS"

if [ "$TARGET" = old ]; then
  bash "$NEW/bench/stack.sh" stop-new
  docker start srb-old >/dev/null
  BASE=http://srb-old:8000
  WATCH="srb-old"
else
  docker stop srb-old >/dev/null 2>&1 || true
  bash "$NEW/bench/stack.sh" new >/dev/null
  BASE=http://srb-new-api:8000
  WATCH="srb-new-api srb-new-worker-grading srb-new-worker-matching srb-new-valkey"
fi
sleep 10

docker rm -f srb-sampler >/dev/null 2>&1 || true
docker run -d --name srb-sampler --network synchrono-shared --env-file "$NEW/.env" \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$NEW:/srv:ro" -v "$RESULTS:/results" \
  synchrono-service-starrocks:dev python /srv/bench/sampler.py \
  "/results/sampler-$TARGET-$RUN_ID.csv" $WATCH srb-s3 >/dev/null

docker rm -f srb-k6 >/dev/null 2>&1 || true
docker run -d --name srb-k6 --network synchrono-shared -v "$NEW/bench/k6:/scripts:ro" \
  -v "$RESULTS:/results" -e TARGET="$TARGET" -e BASE="$BASE" -e FILES="$FILES" -e ROUNDS="$ROUNDS" \
  -e RUN_ID="$RUN_ID" -e NOISE_RPS="${NOISE_RPS:-2}" grafana/k6:latest run --quiet \
  /scripts/compare.js >/dev/null
until [ "$(docker inspect srb-k6 --format '{{.State.Status}}' 2>/dev/null)" = "exited" ]; do sleep 10; done
docker logs srb-k6 > "$RESULTS/k6-$TARGET-$RUN_ID.log" 2>&1
grep -E "RESULT|level=error" "$RESULTS/k6-$TARGET-$RUN_ID.log" | sed 's/.*RESULT //' | cut -c1-260 || true
docker rm -f srb-k6 >/dev/null 2>&1 || true

docker rm -f srb-sampler >/dev/null 2>&1 || true
echo "[bench] $TARGET done: $RESULTS/k6-$TARGET-$RUN_ID.{log,json}"
