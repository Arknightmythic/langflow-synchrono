# Synchrono Matching Service — Langflow + DuckDB

Service matching yang **berdiri sendiri**, terpisah dari backend `data-matching` lama.
Logikanya dipecah menjadi 7 custom component Langflow. Backend Synchrono baru memanggilnya
lewat API.

```
Backend Synchrono  ──HTTP──►  Langflow (flow matching)
                                   │
                                   ▼
                              node 1 … 7
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        SeaweedFS (S3)      PostgreSQL            DuckDB
        parquet incoming    master + konfigurasi  mesin hitung
                            + hasil
```

**Satu-satunya dependency Python: `duckdb`.** Tidak ada Polars, rapidfuzz, pymysql,
maupun client S3 — DuckDB menangani parquet di S3, koneksi PostgreSQL, dan
`jaro_winkler_similarity` sekaligus.

---

## Status verifikasi

Diuji dengan **data produksi asli** (file `825fc484`, grade 4, 200.020 baris),
dibandingkan dengan hasil backend lama di StarRocks:

| | Backend StarRocks | Service ini | |
|---|---|---|---|
| AUTO_MATCH | 115.551 | 115.551 | ✅ |
| MANUAL_REVIEW | 24.022 | 24.022 | ✅ |
| AUTO_UNMATCH | 60.447 | 60.447 | ✅ |
| **Total** | **200.020** | **200.020** | ✅ |

Waktu: **15,9 detik** (join 2,8 + skor 1,3 + tulis 10,6), diukur dari laptop.
Backend lama butuh ~51 detik, itu pun diukur di server.

Yang **sudah** terbukti:
- hasil matching identik dengan produksi (grade 1 & 4)
- `jaro_winkler_similarity` DuckDB identik bit-per-bit dengan rapidfuzz, termasuk
  rumus grade 5 yang memakai rata-rata wilayah bersyarat (selisih `0.00e+00`)
- upsert mencegah duplikasi — dijalankan 2× tetap 1,00× (StarRocks langsung 2,00×)
- SeaweedFS baca-tulis parquet, PostgreSQL baca-tulis

- Langflow 1.12.1 di Docker berjalan dan **ketujuh node terdaftar** di kategori
  `matching` (diverifikasi lewat `GET /api/v1/all`)

Yang **belum** terbukti:
- perangkaian flow di kanvas dan pemanggilan `POST /api/v1/run/<flow_id>`
- grade 6 (custom mapping) — belum diimplementasikan

---

## 1. Prasyarat

| | |
|---|---|
| Docker | untuk SeaweedFS dan Langflow |
| PostgreSQL | sudah ada di `localhost:5432`, database `synchrono` (DBngin) |
| Python 3.12 + `duckdb` | hanya untuk menjalankan skrip di `infra/` dan `run_local.py` |

---

## 2. Siapkan infrastruktur

### SeaweedFS

```bash
cd infra
docker compose up -d seaweedfs
```

| | |
|---|---|
| S3 API | `localhost:8333` ← yang dipakai DuckDB |
| master UI | `localhost:9333` |
| filer | `localhost:8888` |
| kredensial | `synchrono` / `synchrono123` (lihat `s3-config.json`) |

### Skema PostgreSQL

```bash
python apply_schema.py            # DDL + seed ref_*, grade_rules, matching_queries
python apply_schema.py --verify   # cek kondisi saja
```

Membuat 10 tabel dan mengisi konfigurasinya. Hanya butuh `duckdb` — DDL dijalankan
lewat `postgres_execute()`, tanpa psycopg maupun sqlalchemy.

### Isi tabel master

```bash
python migrate_master.py tarik    # StarRocks -> parquet   (venv data-matching)
python migrate_master.py muat     # parquet -> PostgreSQL  (venv ber-duckdb)
```

Dua langkah karena extension `mysql` DuckDB membungkus query katalognya dalam
transaksi eksplisit yang ditolak StarRocks.

### Pindahkan parquet ke SeaweedFS

```bash
python salin_dari_minio.py curated/20260908_100247_825fc484_data_dukcapil_gradeD.parquet
```

DuckDB menyambung ke MinIO dan SeaweedFS sekaligus lewat secret ber-`SCOPE`, jadi
penyalinannya satu statement tanpa file perantara.

Untuk data uji buatan:

```bash
python buat_data_uji.py 1000
```

---

## 3. Uji tanpa Langflow (lakukan ini dulu)

Buktikan logikanya lebih dulu di sini. Kalau gagal di tahap ini, mencari
penyebabnya di kanvas visual jauh lebih sulit.

```bash
python run_local.py \
    --file-id 825fc484 \
    --parquet s3://synchrono/curated/20260908_100247_825fc484_data_dukcapil_gradeD.parquet \
    --grade 4
```

Default **dry run** — tidak menulis ke PostgreSQL. Tambahkan `--write` untuk menulis.

> Berbeda dari sistem lama, menjalankan `--write` berkali-kali **aman**:
> penulisannya upsert, bukan append.

---

## 4. Pasang di Langflow

### ⚠ Di mesin Windows ini, Langflow harus lewat Docker

Venv Langflow di `D:\ISGS\PROJECT\subsynchrono\langflow` **tidak bisa dijalankan**:

```
import xxhash   -> DLL load failed: An Application Control policy has blocked this file
import langflow -> GAGAL (xxhash dependency langsungnya)
langflow.exe    -> GAGAL
```

Ini kebijakan keamanan Windows, bukan masalah kode. Container tidak tersentuh
kebijakan itu, jadi jalur Docker sekaligus menyelesaikannya.

### Jalankan

```bash
cd infra
docker compose up -d --build langflow
```

Build pertama ~6 menit. Setelah itu buka `http://localhost:7860`, login
`admin` / `synchrono123`. Ketujuh node ada di sidebar, grup **matching**.

`Dockerfile.langflow` hanya menambahkan `duckdb` ke image resmi, plus mengunduh
extension `httpfs` dan `postgres` di build time supaya eksekusi flow pertama tidak
tertunda.

### Empat hal yang bikin gagal, dan sudah diberesi di compose

Keempatnya ditemukan saat benar-benar menjalankannya. Tidak satu pun terdokumentasi
dengan jelas di dokumentasi Langflow, jadi dicatat di sini.

**1. Kredensial superuser wajib.** Tanpa ini container gagal start:

```
ValueError: Username and password must be set
```

Langflow 1.12.x menuntut `LANGFLOW_SUPERUSER` + `LANGFLOW_SUPERUSER_PASSWORD`, dan
password default lama ditolak. Yang dipakai sekarang kredensial **development** —
ganti sebelum keluar dari mesin lokal.

**2. `LANGFLOW_HOST` tidak dihormati.** Gejalanya `ERR_EMPTY_RESPONSE` di browser,
padahal dari dalam container `/health_check` balas 200. Langflow mengikat ke
`127.0.0.1`, sehingga port mapping Docker tidak bisa menjangkaunya. Harus lewat
argumen CLI, bukan env:

```yaml
command: ["langflow", "run", "--host", "0.0.0.0", "--port", "7860"]
```

**3. File helper tidak boleh ada di folder komponen.** Langflow memindai **setiap**
`.py` di `LANGFLOW_COMPONENTS_PATH` dan menuntut tiap file berisi subclass
`Component`:

```
TypeError: No Component subclass found in the code string.
```

Karena itu strukturnya dipisah — dan subfolder `matching/` sekaligus menjadi nama
grup di sidebar:

```
components/matching/   → node saja   → /components   (LANGFLOW_COMPONENTS_PATH)
lib/_shared.py         → helper      → /lib          (PYTHONPATH)
```

**4. Nama input dan output tidak boleh sama.** Ini yang paling halus: node tetap
termuat tanpa error mencolok, hanya **hilang diam-diam** dari sidebar. Semula hanya
2 dari 7 node yang muncul, dan penyebabnya cuma terlihat sebagai `warning` di log:

```
Could not build template for PrepareIncoming in bundle 'matching' (skipped):
Inputs and outputs have overlapping names: {'session'}
```

Node 2–6 punya input `session` dan output `session` sekaligus. Node 1 dan 7 lolos
karena namanya kebetulan sudah berbeda. Penamaan sekarang ada di §5.

> Kalau suatu saat ada node yang tidak muncul di sidebar, **cek `docker compose logs
> langflow | grep -i "could not build template"`** lebih dulu — kegagalannya tidak
> memunculkan error, hanya warning.

### Env yang diatur di compose

| Variabel | Nilai | Kenapa |
|---|---|---|
| `LANGFLOW_COMPONENTS_PATH` | `/components` | tempat Langflow menemukan node |
| `PYTHONPATH` | `/lib` | agar `from _shared import …` bisa diselesaikan |
| `LANGFLOW_SUPERUSER(_PASSWORD)` | `admin` / `synchrono123` | wajib, lihat poin 1 |
| `LANGFLOW_CONFIG_DIR` | `/app/langflow-data` | flow tersimpan di volume, tahan rebuild |
| `PG_DSN` | `host=host.docker.internal …` | PostgreSQL ada di host, bukan Docker |
| `S3_ENDPOINT` | `seaweedfs:8333` | satu jaringan compose, cukup nama service |

### ⚠ Jangan uji dengan `curl` dari Git Bash

Di mesin ini `curl` Git Bash selalu mengembalikan `000` untuk `localhost:7860`,
padahal layanannya sehat — kemungkinan terkena Application Control yang sama seperti
`_xxhash`. Sempat membuat Langflow terlihat mati padahal tidak.

Pakai PowerShell atau browser:

```powershell
Invoke-RestMethod -Uri "http://localhost:7860/health_check"
# {"status":"ok","chat":"ok","db":"ok"}
```

### Kalau kebijakan Windows sudah dilonggarkan

Jalur venv host tetap tersedia:

```powershell
$akar = "D:\ISGS\PROJECT\synchrono\langflow-matching"
$env:LANGFLOW_COMPONENTS_PATH   = "$akar\components"
$env:PYTHONPATH                 = "$akar\lib"
$env:LANGFLOW_SUPERUSER         = "admin"
$env:LANGFLOW_SUPERUSER_PASSWORD= "synchrono123"
$env:PG_DSN      = "host=127.0.0.1 port=5432 dbname=synchrono user=postgres"
$env:S3_ENDPOINT = "localhost:8333"

cd D:\ISGS\PROJECT\subsynchrono\langflow
venv\Scripts\python.exe -m pip install duckdb==1.5.5   # belum ada di venv itu
venv\Scripts\langflow.exe run --host 0.0.0.0 --port 7860
```

Perhatikan `--host 0.0.0.0` tetap diperlukan, dan `PYTHONPATH` harus menunjuk
`lib/` — dua hal yang sama seperti di Docker.

Verifikasi API component sudah dilakukan terhadap **Langflow 1.12.1** — keenam
simbol yang dipakai (`Component`, `MessageTextInput`, `HandleInput`, `IntInput`,
`Output`, `Data`) tersedia, dan pola node hulu→hilir lewat `Data` sudah diuji jalan.

---

## 5. Rangkai flow

Alurnya **linear** — tiap node menerima sesi dan meneruskannya. Tidak ada
percabangan, jadi tidak ada yang perlu ditebak.

```
[1 Open Session] ──session──► [2 Prepare Incoming] ──incoming_ready──►
[3 Prepare Master] ──master_ready──► [4 Load Config] ──config_ready──►
[5 Run Join] ──joined──► [6 Score & Classify] ──scored──►
[7 Persist Results] ──summary──► ringkasan JSON
```

Nama port tiap node — output sengaja **tidak** boleh sama dengan input (lihat §4
poin 4):

| Node | Input | Output |
|---|---|---|
| 1 `OpenMatchingSession` | `file_id`, `parquet_path`, `grade` | `session` |
| 2 `PrepareIncoming` | `session` | `incoming_ready` |
| 3 `PrepareMaster` | `session` | `master_ready` |
| 4 `LoadMatchingConfig` | `session` | `config_ready` |
| 5 `RunMatchingJoin` | `session` | `joined` |
| 6 `ScoreAndClassify` | `session` | `scored` |
| 7 `PersistResults` | `session` | `summary` |

Node 1 punya tiga input yang menjadi payload API: `file_id`, `parquet_path`, `grade`.

Simpan, lalu catat `flow_id` dari URL.

### Kenapa node mengoper "Session", bukan data

`Session` membawa **objek koneksi DuckDB**, bukan isi tabel. Tiap node membuat
view/tabel di dalam DuckDB dan node berikutnya merujuknya lewat nama — jadi data
tidak pernah diserialisasi antar node. Inilah yang membuat pemecahan jadi node
tidak menambah biaya apa pun.

---

## 6. Panggil lewat API

API-nya butuh token — `LANGFLOW_AUTO_LOGIN` sengaja `false`. Tanpa login,
setiap endpoint membalas **403 Forbidden**.

```powershell
# 1. login
$login = Invoke-RestMethod -Uri "http://localhost:7860/api/v1/login" -Method Post `
    -Body @{ username = "admin"; password = "synchrono123" } `
    -ContentType "application/x-www-form-urlencoded"
$hdr = @{ Authorization = "Bearer $($login.access_token)" }

# 2. jalankan flow
$payload = @{
    tweaks = @{
        OpenMatchingSession = @{
            file_id      = "825fc484"
            parquet_path = "s3://synchrono/curated/xxx.parquet"
            grade        = 4
        }
    }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://localhost:7860/api/v1/run/<FLOW_ID>?stream=false" `
    -Method Post -Headers $hdr -Body $payload -ContentType "application/json"
```

Untuk memastikan ketujuh node terbaca Langflow:

```powershell
$all = Invoke-RestMethod -Uri "http://localhost:7860/api/v1/all" -Headers $hdr
$all.matching.PSObject.Properties.Name    # harus 7 entri
```

Balasannya kecil:

```json
{
  "message": "Grade 4 matching completed",
  "file_id": "825fc484",
  "grade": 4,
  "processed_rows": 200020,
  "matched_rows": 115551,
  "manual_review_rows": 24022,
  "unmatched_rows": 60447,
  "sync_status": 1
}
```

> Bentuk payload `tweaks` berbeda antar versi Langflow. Cek `http://localhost:7860/docs`
> di instance-mu untuk bentuk yang pasti.

### Kontrak

**Masuk** — backend mengirim semuanya, service tidak menuntut skema apa pun di sisi backend:

| Field | Contoh |
|---|---|
| `file_id` | `825fc484` |
| `parquet_path` | `s3://synchrono/curated/xxx.parquet` |
| `grade` | `1`–`5` |

**Keluar** — ringkasan di atas. Service **tidak** mengelola status alur
(`sync_status` di tabel, pemicu reasoning/export, audit). Itu urusan pemanggil.

---

## 7. Daftar node

| # | Node | Yang dikerjakan |
|---|---|---|
| 1 | `OpenMatchingSession` | buka koneksi DuckDB, pasang extension, sambungkan SeaweedFS + PostgreSQL |
| 2 | `PrepareIncoming` | `CREATE VIEW incoming_df` atas `read_parquet('s3://…')` + normalisasi |
| 3 | `PrepareMaster` | `CREATE VIEW master_df` atas tabel master PostgreSQL + normalisasi |
| 4 | `LoadMatchingConfig` | ambil `matching_query` + `grade_rules` dari PostgreSQL |
| 5 | `RunMatchingJoin` | eksekusi SQL blocking join per grade |
| 6 | `ScoreAndClassify` | Jaro-Winkler berbobot + ambang → `match_result`, satu statement SQL |
| 7 | `PersistResults` | upsert `institution` + `manual_matches` |

Nama view `incoming_df` dan `master_df` **tidak boleh diubah** — kelima SQL di tabel
`matching_queries` merujuk nama itu, dan disalin apa adanya dari sistem yang sudah berjalan.

### Normalisasi (node 2 & 3)

Menentukan hasil matching, jadi harus sama persis dengan sistem lama:

- semua kolom teks → `lower(trim(...))`
- `tanggal_lahir` → 6 format dicoba berurutan; tahun < 1900 dibuang jadi NULL
- `jenis_kelamin` → whitelist → `l` / `p`
- `status_kematian` → whitelist → `h` / `m`

Macro `j(a, b)` menirukan `safe_jaro()` Python: mengembalikan 0 kalau salah satu
sisi NULL atau string kosong.

---

## 8. Perbaikan dari sistem lama

### Duplikasi hilang di level skema

```sql
PRIMARY KEY (file_id, id_incoming)
```

StarRocks tidak bisa menegakkan ini, sehingga matching ulang selalu **menambah**
baris: tabel `institution` di sana berisi 22,9 juta baris untuk 14,2 juta record
unik — 34% duplikat, satu file mencapai rasio 15×, dan semua `COUNT` menggelembung.

Di sini penulisannya `ON CONFLICT DO UPDATE`. Dijalankan dua kali, hasilnya sama persis.

### Kolom jebakan tidak dibawa

`nik_incoming` (90% NULL) dan `area_incoming` (100% NULL) sengaja tidak ada di skema
baru. Keduanya selalu diisi NULL oleh kode matching, dan keberadaannya menggoda orang
menulis `JOIN master ON manual_matches.nik_incoming = master.nik` yang hampir selalu
mengembalikan kosong.

### Master tidak lagi ditarik ke memori

Dulu 299.088 baris di-load penuh tiap job (84 detik dari laptop, 6 detik di server).
Sekarang `master_df` hanyalah view ke PostgreSQL; DuckDB mengambil seperlunya saat join.

---

## 9. Yang perlu diwaspadai

| Hal | Keterangan |
|---|---|
| **DuckDB menulis ke PostgreSQL lewat COPY** | Daftar kolom di `INSERT` **diabaikan** — kolom yang dilewati terkirim sebagai NULL. Karena itu skema sengaja tanpa `bigserial` dan tanpa `DEFAULT`, dan node 7 mengisi semua kolom secara eksplisit dengan urutan sama persis seperti definisi tabel. |
| **`review_missing_count` adalah kecocokan PERSIS** | Grade 4 hanya masuk manual review bila `missing_count` **tepat 2**, bukan "minimal 2". Ubah sedikit kondisi join dan seluruh jalur manual review bisa mati tanpa error apa pun. |
| **Tie-break skor** | Versi Python mempertahankan kandidat yang ditemui lebih dulu (urutan iterasi, praktis acak). Di sini `ORDER BY skor DESC, nik_master` — deterministik dan bisa direproduksi. |
| **Grade 6** | Belum didukung. Butuh pairing kolom dinamis dari `custom_field_mapping`. Node 1 menolaknya secara eksplisit. |
| **Memori node 5** | Hasil join bisa jauh lebih besar dari jumlah baris incoming — file 200 ribu baris menghasilkan 2,99 juta pasangan kandidat. |

---

## 10. Struktur folder

```
langflow-matching/
├── README.md
├── run_local.py                  uji 7 node tanpa Langflow (default dry run)
├── components/                   → di-mount ke /components
│   └── matching/                 nama folder = nama grup di sidebar Langflow
│       ├── n1_open_session.py    HANYA file node boleh ada di sini —
│       ├── n2_prepare_incoming.py  Langflow menuntut tiap .py berisi
│       ├── n3_prepare_master.py    subclass Component (§4 poin 3)
│       ├── n4_load_config.py
│       ├── n5_run_join.py
│       ├── n6_score_classify.py
│       └── n7_persist.py
├── lib/                          → di-mount ke /lib, masuk PYTHONPATH
│   └── _shared.py                koneksi, SQL normalisasi, rumus skor
└── infra/
    ├── docker-compose.yml        SeaweedFS + Langflow
    ├── Dockerfile.langflow       image Langflow + duckdb
    ├── s3-config.json            kredensial S3
    ├── schema.sql                DDL PostgreSQL
    ├── apply_schema.py           terapkan DDL + seed
    ├── matching_queries.json     ekspor SQL join dari StarRocks
    ├── migrate_master.py         isi tabel master
    ├── salin_dari_minio.py       MinIO → SeaweedFS
    └── buat_data_uji.py          bangkitkan parquet uji
```
