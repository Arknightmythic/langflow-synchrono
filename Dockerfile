# Synchrono Service — image kecil, isinya hanya yang benar-benar dipakai.
#
# Bandingkan dengan Dockerfile.langflow yang berangkat dari
# `langflowai/langflow:latest`: image itu membawa seluruh kanvas, penyunting
# alur, basis data SQLite, dan puluhan integrasi model — sementara yang dipakai
# service ini hanya enam fungsi Python dan DuckDB. Selisih ukuran image dan
# memori idle antara kedua Dockerfile inilah bagian pertama dari jawaban
# "lebih enteng mana", dan bisa dibaca tanpa menjalankan k6 sama sekali:
#
#     docker images --format "{{.Repository}}:{{.Tag}}\t{{.Size}}"

FROM python:3.14-slim

# Versi Python DISAMAKAN DENGAN IMAGE LANGFLOW, bukan dipilih sendiri.
# `docker exec synchrono-langflow python -V` menjawab 3.14.7, jadi di sini
# 3.14 juga. Percobaan pertama memakai 3.12 — menebak dari berkas
# __pycache__/*.cpython-312.pyc yang ternyata sisa dari host, bukan dari
# container — dan itu menyisakan bantahan yang wajar: sebagian selisih yang
# terukur bisa saja selisih penafsir Python, bukan selisih platform.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /synchrono

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Extension DuckDB diunduh saat pertama dipakai. Diunduh di sini supaya
# container tidak butuh akses internet saat jalan, dan supaya permintaan
# pertama tidak menanggung biaya unduhan yang muncul di grafik sebagai lonjakan
# p99 palsu.
RUN python -c "import duckdb; c=duckdb.connect(); [c.execute(f'INSTALL {e}') for e in ('httpfs','postgres')]"

# `excel` WAJIB ikut — unggahan .xlsx dibaca lewat extension itu. Tanpanya ia
# diunduh saat .xlsx pertama datang: terukur 104,8 detik di jaringan lambat,
# dan di server tanpa akses internet unggahan .xlsx GAGAL sama sekali. Image
# Langflow memasang ketiganya; daftar gabungan di sini harus sama dengan
# Dockerfile.langflow. Langkah terpisah hanya supaya lapisan di atas tetap
# terpakai ulang dari cache build.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL excel')"

COPY app ./app
COPY infra ./infra

# lib/ dan components/ milik repo INI — service berdiri sendiri, tanpa checkout
# langflow-synchrono (keputusan 28 Sep 2026). Keduanya ikut di image, jadi image
# ini jalan tanpa mount apa pun; compose tetap me-mount folder yang sama supaya
# `git pull` + restart cukup, tanpa build ulang. Kelas komponen dimuat dari
# SYNCHRONO_COMPONENTS (lihat app/alur.py); folder matching ada di PYTHONPATH
# untuk endpoint REST matching lama (n1..n7).
COPY lib ./lib
COPY components /components
ENV PYTHONPATH=/synchrono/lib:/components/matching \
    SYNCHRONO_COMPONENTS=/components

EXPOSE 8000

# Satu worker adalah BAWAAN YANG DISENGAJA: Langflow juga satu proses, jadi
# inilah perbandingan yang setara. Naikkan WEB_CONCURRENCY untuk melihat
# berapa jauh service ini bisa didorong — tapi baca dulu catatannya di
# PERBANDINGAN.md, karena batas konkurensi grading berlaku PER PROSES.
#
# Log akses MATI secara bawaan. Ini perlu disebut terus terang: Langflow
# menuliskan satu baris log per permintaan, dan pada beban ribuan permintaan
# per menit biaya itu tidak nol. Setel ACCESS_LOG=1 untuk menyalakannya dan
# menutup perbedaan itu kalau memang ingin diukur seketat mungkin.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1} $([ \"${ACCESS_LOG:-0}\" = 1 ] || echo --no-access-log)"]
