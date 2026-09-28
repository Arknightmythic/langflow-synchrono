#!/bin/bash
# Nyalakan MariaDB, tunggu sampai menerima koneksi TCP, lalu serahkan container
# ke layanan konversi.
#
# Sama polanya dengan konverter PostgreSQL. Yang ditunggu koneksi TCP ke
# 127.0.0.1, bukan socket: saat inisialisasi pertama, entrypoint MariaDB
# menyalakan server SEMENTARA tanpa jaringan, lalu mematikannya. Menunggu socket
# bisa keliru mengira server sementara itu sudah yang sesungguhnya.
set -e

docker-entrypoint.sh mariadbd &
DB=$!

matikan() { kill -TERM "$DB" 2>/dev/null || true; }
trap matikan TERM INT

echo "[K] menunggu MariaDB siap..."
for i in $(seq 1 90); do
    if MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -h 127.0.0.1 -u root \
           -e "SELECT 1" >/dev/null 2>&1; then
        echo "[K] MariaDB siap setelah ${i} detik"
        break
    fi
    if ! kill -0 "$DB" 2>/dev/null; then
        echo "[K] MariaDB berhenti sebelum siap" >&2
        exit 1
    fi
    sleep 1
done

MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -h 127.0.0.1 -u root \
    -e "SELECT 1" >/dev/null 2>&1 || {
    echo "[K] MariaDB tidak siap dalam 90 detik" >&2
    exit 1
}

exec python /konverter/layanan.py
