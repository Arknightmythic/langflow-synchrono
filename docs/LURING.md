# Bekerja tanpa VPN (lembur di luar jam kantor)

Server `172.16.12.98` — StarRocks, MinIO, Redis, Qdrant — dimatikan di luar jam
kantor. Dokumen ini mencatat apa yang sudah disalin ke mesin lokal supaya kedua
service tetap bisa dikerjakan, dan apa yang tetap butuh server.

Semua yang di bawah sudah **diverifikasi jalan tanpa VPN**.

---

## Apa yang sudah aman

| Yang dibutuhkan | Ada di mana sekarang | Jumlah |
|---|---|---|
| Tabel `master` | PostgreSQL lokal `:5432/synchrono` | 299.088 baris, 11 kolom |
| Cadangan `master` | `infra/cadangan/master_terbaru.parquet` | 299.088 × 13 kolom, 8,9 MB |
| Parquet berkas unggahan | SeaweedFS lokal `s3://synchrono/curated/` | 14 berkas, 33 MB |
| Metadata `uploaded_files` | PostgreSQL lokal + cadangan parquet | 154 baris |
| `matching_queries`, `grade_rules`, `grade_bands`, tabel `ref_*` | PostgreSQL lokal | sudah ter-seed |
| Data uji grading | SeaweedFS lokal `s3://bucket-test/uploads/` | 8 berkas |

Cadangan `master` sudah dibandingkan **per nilai** (bukan cuma jumlah baris)
dengan isi PostgreSQL: identik, dua arah, nol selisih.

## Berkas yang benar-benar bisa dipakai

Metadata mencatat 154 unggahan, tapi parquet lama sudah dibersihkan dari
penyimpanan — hanya **14** yang berkasnya masih ada. Untuk melihat daftarnya
kapan saja:

```bash
docker exec synchrono-langflow python /synchrono/infra/cek_ketersediaan.py
```

Keluarannya berisi file_id, grade, jumlah baris, dan perintah siap tempel.
Grade 1 sampai 6 semuanya terwakili.

---

## Menjalankan tanpa VPN

**Matching** (200.000 baris, ±2,6 detik, `--write` untuk benar-benar menulis):

```bash
docker exec synchrono-langflow python /synchrono/run_local.py \
    --file-id d88150c5 \
    --parquet s3://synchrono/curated/20260908_100245_d88150c5_data_dukcapil_gradeA.parquet \
    --grade 1
```

**Grading** — lewat API, seperti backend:

```bash
docker exec synchrono-langflow python /synchrono/run_grading_local.py \
    --file-id uji-b --bucket bucket-test --key uploads/uji-b/data.parquet --job
```

Berkas produksi juga bisa digrading, dari bucket `synchrono`:

```bash
docker exec synchrono-langflow python /synchrono/run_grading_local.py \
    --file-id d88150c5 --bucket synchrono \
    --key curated/20260908_100245_d88150c5_data_dukcapil_gradeA.parquet --skip-write
```

> Semua perintah **harus lewat `docker exec`.** Di mesin Windows ini extension
> `httpfs` DuckDB diblokir Application Control policy, jadi apa pun yang
> menyentuh S3 gagal kalau dijalankan dari host.

> **Sesudah mengubah apa pun di `lib/`, restart Langflow:**
> `docker compose restart langflow`
>
> Skrip `run_*.py` memulai proses baru sehingga langsung memakai kode terbaru,
> tetapi Langflow berumur panjang dan menyimpan modul yang sudah diimpor di
> memori. Tanpa restart, API-nya masih menjalankan versi lama — dan itu tampak
> seperti perbaikan yang tidak berpengaruh, bukan seperti galat.

---

## Memulihkan `master` dari cadangan

Kalau PostgreSQL lokal perlu diisi ulang — misalnya database dibuat ulang —
**tidak perlu VPN**:

```bash
cd infra
python muat_cadangan.py          # bandingkan saja, tidak menulis
python muat_cadangan.py --muat   # isi ulang dari cadangan parquet
```

Mengisi ulang dari berkas lokal jauh lebih aman daripada langsung dari
StarRocks: kalau gagal di tengah jalan, berkasnya masih ada dan perintahnya
tinggal diulang. Kalau sumbernya StarRocks dan servernya mati di tengah proses,
tabelnya tinggal kosong.

---

## Yang tetap butuh VPN

| | Kenapa |
|---|---|
| Menyegarkan `master` | Sumbernya StarRocks |
| Mengambil berkas unggahan baru | Sumbernya MinIO produksi |
| Chatbot `data-matching` | Butuh StarRocks, Qdrant, Redis, Ollama — semuanya di server itu |
| Reasoning LLM | Butuh Ollama di server itu |

Matching dan grading **tidak** ada di daftar ini. Keduanya sudah sepenuhnya
mandiri.

---

## Menyegarkan cadangan (lakukan saat VPN masih hidup)

```bash
# 1. master -> parquet (±97 detik)
cd data-matching
./.venv/Scripts/python.exe ../langflow-synchrono/infra/cadangkan_master.py

# 2. metadata uploaded_files
./.venv/Scripts/python.exe ../langflow-synchrono/infra/cadangkan_uploaded_files.py
cd ../langflow-synchrono/infra && python cadangkan_uploaded_files.py --muat

# 3. parquet baru dari MinIO -> SeaweedFS (±97 detik; yang sudah ada dilewati)
docker cp ../.env synchrono-langflow:/tmp/.env
docker exec synchrono-langflow python /synchrono/infra/salin_semua_dari_minio.py
docker exec synchrono-langflow rm -f /tmp/.env

# 4. samakan PostgreSQL dengan cadangan master yang baru
python muat_cadangan.py          # periksa dulu
python muat_cadangan.py --muat   # kalau memang berbeda
```

Langkah 1 dan 2 memakai venv `data-matching` karena butuh `pymysql` — venv
Langflow tidak punya itu. Langkah 3 harus di container karena butuh `httpfs`.

Hapus `/tmp/.env` dari container setelah selesai; berkas itu memuat kredensial
StarRocks dan MinIO.

---

## Catatan

`infra/cadangan/` sudah masuk `.gitignore`. Isinya data kependudukan
sungguhan — jangan pernah masuk repositori.
