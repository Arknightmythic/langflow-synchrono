#!/usr/bin/env bash
# Isolated benchmark stack (works on Windows Git Bash and on the Linux server):
#   srb-s3   SeaweedFS for test files        srb-pg   PostgreSQL (DuckDB service + simulated portal)
#   srb-old  synchrono-service (DuckDB)       srb-cb   callback receiver
#   srb-new-* synchrono-service-starrocks (docker-compose.yml + docker-compose.bench.yml)
# Usage: bash bench/stack.sh [storage|harness|old|new|data|master|stop-old|stop-new|all|clean]
set -euo pipefail
export MSYS_NO_PATHCONV=1

abspath() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
HERE=$(abspath "$(dirname "${BASH_SOURCE[0]}")")
NEW=${NEW:-$(abspath "$HERE/..")}
ROOT=${ROOT:-$(abspath "$NEW/..")}
OLD=${OLD:-$ROOT/synchrono-service}
DATA=${DATA:-$NEW/bench/.data}
NET=${SHARED_NETWORK:-synchrono-shared}
OLD_IMAGE=${OLD_IMAGE:-synchrono-service:2.0.0}
TEST_DATA=${TEST_DATA:-$ROOT/test-data-csv/uji-master-ae}
MASTER_PARQUET=${MASTER_PARQUET:-$ROOT/1790325476460_23223dc0_master.parquet}
MASTER_ID=${MASTER_ID:-um-master}
OLD_PORT=${OLD_PORT:-58101}
NEW_PORT=${NEW_PORT:-58102}

mkdir -p "$DATA/seaweed" "$DATA/spill-old" "$DATA/work-new"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

start_storage() {
  docker rm -f srb-s3 srb-pg >/dev/null 2>&1 || true
  docker run -d --name srb-s3 --network "$NET" \
    -v "$OLD/infra/s3-config.json:/etc/s3.json:ro" -v "$DATA/seaweed:/data" \
    chrislusf/seaweedfs:latest server -dir=/data -s3 -s3.config=/etc/s3.json \
    -master.volumeSizeLimitMB=1024 >/dev/null
  docker run -d --name srb-pg --network "$NET" -e POSTGRES_PASSWORD=bench postgres:16-alpine >/dev/null
  for _ in $(seq 1 60); do
    docker exec srb-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1
  done
  sleep 2
  for db in synchrono_engine portal_sim; do
    docker exec srb-pg psql -U postgres -qc "CREATE DATABASE $db" >/dev/null
  done
  docker exec -i srb-pg psql -U postgres -q -d portal_sim < "$HERE/portal_schema.sql"
  docker run --rm --network "$NET" -v "$OLD/infra/skema:/skema:ro" -w /skema \
    -e PG_DSN="host=srb-pg port=5432 dbname=synchrono_engine user=postgres password=bench" \
    -e PYTHONDONTWRITEBYTECODE=1 "$OLD_IMAGE" sh -c "python migrate.py && python seed.py" | tail -3
  echo "[stack] storage ready"
}

start_old() {
  docker rm -f srb-old >/dev/null 2>&1 || true
  docker run -d --name srb-old --network "$NET" -p "$OLD_PORT:8000" \
    -v "$OLD/lib:/synchrono/lib:ro" -v "$OLD/components:/components:ro" \
    -v "$OLD/app:/synchrono/app:ro" -v "$OLD/infra:/synchrono/infra:ro" \
    -v "$DATA/spill-old:/tmp/spill" \
    -e PG_DSN="host=srb-pg port=5432 dbname=synchrono_engine user=postgres password=bench" \
    -e PORTAL_PG_DSN="host=srb-pg port=5432 dbname=portal_sim user=postgres password=bench" \
    -e S3_ENDPOINT=srb-s3:8333 -e S3_ACCESS_KEY=synchrono -e S3_SECRET_KEY=synchrono123 \
    -e S3_USE_SSL=false -e SERVICE_API_KEY=bench-key -e SERVICE_SUPERUSER=admin \
    -e SERVICE_SUPERUSER_PASSWORD=bench -e DUCKDB_MODE=kolam -e DUCKDB_POOL_SIZE=4 \
    -e DUCKDB_MEMORY_LIMIT="${OLD_MEMORY:-3GB}" -e DUCKDB_TEMP_DIR=/tmp/spill \
    -e GRADING_MAX_CONCURRENT=2 -e MATCHING_MAX_CONCURRENT=1 \
    -e WILAYAH_PARQUET="" -e REASONING_AI_BASE_URL="" -e NORMALISASI_AI_BASE_URL="" \
    "$OLD_IMAGE" >/dev/null
  echo "[stack] srb-old started on port $OLD_PORT"
}

start_harness() {
  docker rm -f srb-cb >/dev/null 2>&1 || true
  docker run -d --name srb-cb --network "$NET" -v "$HERE:/bench:ro" \
    -e PORTAL_PG_DSN="host=srb-pg port=5432 dbname=portal_sim user=postgres password=bench" \
    "$OLD_IMAGE" python /bench/harness.py >/dev/null
  echo "[stack] srb-cb started"
}

starrocks_password() {
  if [ -n "${STARROCKS_PASSWORD:-}" ]; then
    echo "$STARROCKS_PASSWORD"
  elif [ -f "$ROOT/starrtock-key.txt" ]; then
    sed -n 's/.*Root password: \([^ .]*\).*/\1/p' "$ROOT/starrtock-key.txt"
  else
    echo "STARROCKS_PASSWORD is not set" >&2; exit 2
  fi
}

new_env() {
  local pw
  pw=$(starrocks_password)
  sed -e "s|^STARROCKS_PASSWORD=.*|STARROCKS_PASSWORD=$pw|" \
      -e "s|^S3_ENDPOINT=.*|S3_ENDPOINT=srb-s3:8333|" \
      -e "s|^SERVICE_API_KEY=.*|SERVICE_API_KEY=bench-key|" \
      -e "s|^SERVICE_SUPERUSER_PASSWORD=.*|SERVICE_SUPERUSER_PASSWORD=bench|" \
      -e "s|^WILAYAH_PARQUET=.*|WILAYAH_PARQUET=|" \
      -e "s|^NORMALISASI_AI_BASE_URL=.*|NORMALISASI_AI_BASE_URL=|" \
      -e "s|^REASONING_AI_BASE_URL=.*|REASONING_AI_BASE_URL=|" \
      "$NEW/.env.example" > "$NEW/.env.bench"
  # Another cluster than the .env.example default (e.g. the office server):
  # export these before running stack.sh / run_bench.sh.
  local key
  for key in STARROCKS_HOST STARROCKS_PORT STARROCKS_USER STARROCKS_STREAM_LOAD_URL; do
    if [ -n "${!key:-}" ]; then
      sed -i "s|^$key=.*|$key=${!key}|" "$NEW/.env.bench"
    fi
  done
  local host=${UDF_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}
  if [ -n "$host" ]; then
    sed -i "s|^UDF_JAR_URL=.*|UDF_JAR_URL=http://$host:$NEW_PORT/udf/synchrono-udf.jar|" "$NEW/.env.bench"
  fi
}

compose_new() {
  (cd "$NEW" && ENV_FILE=.env.bench PREFIX=srb-new API_PORT="$NEW_PORT" WORK_VOLUME="$DATA/work-new" \
     docker compose -p srb-new -f docker-compose.yml -f docker-compose.bench.yml "$@")
}

start_new() {
  new_env
  compose_new up -d --build
  echo "[stack] srb-new started on port $NEW_PORT"
}

upload_data() {
  docker run --rm --network "$NET" -v "$TEST_DATA:/t:ro" -v "$(dirname "$MASTER_PARQUET"):/m:ro" \
    -v "$HERE:/bench:ro" -e FILER_URL=http://srb-s3:8888 "$OLD_IMAGE" sh -c "
      for g in A B C D E; do python /bench/upload.py /t/uji_master_\$g.csv bench um-\$g/raw/data.csv; done
      python /bench/upload.py /m/$(basename "$MASTER_PARQUET") bench master/$MASTER_ID.parquet"
}

load_master() {
  docker run --rm --network "$NET" --env-file "$NEW/.env.bench" -e WORK_DIR=/work -e DUCKDB_TEMP_DIR=/work/spill \
    -v "$DATA/work-new:/work" -v "$(dirname "$MASTER_PARQUET"):/m:ro" \
    synchrono-service-starrocks:dev python tools/load_master.py "$MASTER_ID" "/m/$(basename "$MASTER_PARQUET")"
}

case "${1:-all}" in
  storage) start_storage ;;
  harness) start_harness ;;
  old) start_old ;;
  new) start_new ;;
  data) upload_data ;;
  master) load_master ;;
  stop-old) docker stop srb-old >/dev/null 2>&1 || true ;;
  stop-new) compose_new stop || true ;;
  all) start_storage; start_harness; start_old; start_new; upload_data ;;
  clean) compose_new down || true; docker rm -f srb-s3 srb-pg srb-old srb-cb srb-k6 srb-sampler >/dev/null 2>&1 || true ;;
  *) echo "usage: stack.sh [storage|harness|old|new|data|master|stop-old|stop-new|all|clean]"; exit 2 ;;
esac
