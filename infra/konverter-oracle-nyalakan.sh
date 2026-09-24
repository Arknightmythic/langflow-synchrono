#!/bin/bash
# Nyalakan Oracle, daftarkan DIRECTORY object-nya, lalu serahkan container ke
# layanan konversi.
#
# Menunggunya paling lama dari ketiga konverter — Oracle bisa butuh beberapa
# menit pada nyala pertama, karena database-nya dibuat dari nol. Itu justru
# alasan container ini berumur panjang: ongkos itu dibayar sekali.
set -e

/opt/oracle/container-entrypoint.sh &
ORA=$!

matikan() { kill -TERM "$ORA" 2>/dev/null || true; }
trap matikan TERM INT

echo "[K] menunggu Oracle siap (bisa beberapa menit pada nyala pertama)..."
SIAP=0
for i in $(seq 1 180); do
    if echo 'SELECT 1 FROM dual;' | sqlplus -s -L \
            "system/${ORACLE_PASSWORD}@localhost:1521/FREEPDB1" >/dev/null 2>&1; then
        echo "[K] Oracle siap setelah ${i} detik"
        SIAP=1
        break
    fi
    if ! kill -0 "$ORA" 2>/dev/null; then
        echo "[K] Oracle berhenti sebelum siap" >&2
        exit 1
    fi
    sleep 2
done

[ "$SIAP" = "1" ] || { echo "[K] Oracle tidak siap dalam batas waktu" >&2; exit 1; }

# DIRECTORY object dibuat SEKALI di sini, bukan tiap job. Ia menunjuk folder
# tempat berkas .dmp diunduh, dan Data Pump membacanya dari dalam server.
#
# Dibuat oleh `system` (punya CREATE ANY DIRECTORY); skema job hanya diberi
# READ/WRITE padanya, tidak pernah hak membuat directory baru — kalau punya, ia
# bisa mengarahkannya ke folder mana pun di server.
echo "[K] mendaftarkan DIRECTORY ${KONV_ORA_DIRNAME:-KONVERSI_DIR}"
sqlplus -s -L "system/${ORACLE_PASSWORD}@localhost:1521/FREEPDB1" <<SQL
SET FEEDBACK OFF
CREATE OR REPLACE DIRECTORY ${KONV_ORA_DIRNAME:-KONVERSI_DIR}
    AS '${KONV_ORA_KERJA:-/opt/oracle/dmp}';
EXIT
SQL

exec python /konverter/layanan.py
