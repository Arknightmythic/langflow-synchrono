#!/bin/bash
# Nyalakan PostgreSQL, tunggu sampai siap, lalu serahkan container ke layanan
# konversi.
#
# Urutannya penting. Kalau layanannya naik lebih dulu, permintaan pertama akan
# gagal dengan "connection refused" — dan itu terlihat seperti kerusakan padahal
# cuma belum sempat.
set -e

# `docker-entrypoint.sh` milik image PostgreSQL yang mengurus initdb, izin
# folder, dan variabel POSTGRES_*. Dipanggil apa adanya supaya perilakunya sama
# dengan image resminya; hanya saja di latar belakang.
docker-entrypoint.sh postgres &
PG=$!

# Kalau PostgreSQL mati, container ikut berhenti. Tanpa ini, layanan konversi
# akan tetap menjawab HTTP dengan riang di atas basis data yang sudah tidak ada.
matikan() { kill -TERM "$PG" 2>/dev/null || true; }
trap matikan TERM INT

echo "[K] menunggu PostgreSQL siap..."
for i in $(seq 1 60); do
    if pg_isready -q -h 127.0.0.1 -p "${KONV_PG_PORT:-5432}" -U "${POSTGRES_USER:-postgres}"; then
        echo "[K] PostgreSQL siap setelah ${i} detik"
        break
    fi
    if ! kill -0 "$PG" 2>/dev/null; then
        echo "[K] PostgreSQL berhenti sebelum siap" >&2
        exit 1
    fi
    sleep 1
done

pg_isready -q -h 127.0.0.1 -p "${KONV_PG_PORT:-5432}" -U "${POSTGRES_USER:-postgres}" || {
    echo "[K] PostgreSQL tidak siap dalam 60 detik" >&2
    exit 1
}

exec python /konverter/layanan.py
