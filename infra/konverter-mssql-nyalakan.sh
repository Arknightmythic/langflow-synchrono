#!/bin/bash
# Nyalakan SQL Server, tunggu sampai menerima koneksi, lalu serahkan container
# ke layanan konversi.
#
# Sama polanya dengan konverter PostgreSQL, hanya menunggunya lebih lama:
# SQL Server butuh puluhan detik untuk siap, bukan detik. Itu justru alasan
# container ini berumur panjang — waktu nyala itu dibayar sekali, bukan tiap job.
set -e

/opt/mssql/bin/sqlservr &
SQL=$!

matikan() { kill -TERM "$SQL" 2>/dev/null || true; }
trap matikan TERM INT

echo "[K] menunggu SQL Server siap..."
for i in $(seq 1 90); do
    if /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" \
           -C -l 2 -Q "SELECT 1" >/dev/null 2>&1; then
        echo "[K] SQL Server siap setelah ${i} detik"
        break
    fi
    if ! kill -0 "$SQL" 2>/dev/null; then
        echo "[K] SQL Server berhenti sebelum siap" >&2
        exit 1
    fi
    sleep 1
done

/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -l 2 \
    -Q "SELECT 1" >/dev/null 2>&1 || {
    echo "[K] SQL Server tidak siap dalam 90 detik" >&2
    exit 1
}

exec python /konverter/layanan.py
