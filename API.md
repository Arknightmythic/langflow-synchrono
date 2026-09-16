# API Synchrono di Langflow

Enam endpoint aktif. Semuanya `POST` — Langflow tidak menyediakan `GET` untuk
menjalankan flow, termasuk untuk yang sifatnya membaca.

| # | Endpoint | Untuk | Sifat |
|---|---|---|---|
| 1 | `/api/v1/run/grading-dispatch` | kirim job grading | **asinkron**, balas <5 detik |
| 2 | `/api/v1/run/grading-status` | polling status | cepat |
| 3 | `/api/v1/run/config-rules` | baca aturan grade | cepat |
| 4 | `/api/v1/run/config-rules-update` | ubah aturan grade | cepat |
| 5 | `/api/v1/run/grading` | pipeline grading telanjang | **sinkron**, penelusuran saja |
| 6 | `/api/v1/run/<UUID>` | matching | **sinkron**, belum punya nama endpoint |

Koleksi Postman siap impor: **`infra/postman_synchrono.json`**

```bash
cd infra && python buat_postman.py     # bangkitkan ulang dari Langflow yang jalan
```

Bangkitkan ulang setiap kali flow dibangun ulang. Node id dibaca langsung dari
flow, bukan ditulis tangan — dan itu penting, alasannya di bagian 3.

---

## 1. Mendapatkan API key

Endpoint `/api/v1/run` memakai header **`x-api-key`**, bukan
`Authorization: Bearer`. Bearer hanya berlaku untuk endpoint pengelolaan
(`/api/v1/flows`, `/api/v1/all`, `/api/v1/api_key`). Salah pakai membalas:

```json
{"detail": "Invalid or missing API key"}
```

### Cara A — lewat UI

1. Buka `http://localhost:7860`, masuk sebagai `admin`
2. Menu **Settings → Langflow API Keys → Add New**
3. Salin nilainya **sekarang** — tidak bisa dilihat lagi

### Cara B — lewat API (ada di koleksi Postman, folder `0. Auth`)

**Langkah 1: login, dapat bearer token.** Perhatikan `Content-Type`-nya
form-urlencoded, bukan JSON:

```http
POST {{base_url}}/api/v1/login
Content-Type: application/x-www-form-urlencoded

username=admin&password=synchrono123
```

```json
{ "access_token": "eyJhbGciOi...", "token_type": "bearer" }
```

**Langkah 2: buat API key dengan token itu.**

```http
POST {{base_url}}/api/v1/api_key/
Authorization: Bearer {{bearer}}
Content-Type: application/json

{ "name": "synchrono-backend" }
```

```json
{
  "name": "synchrono-backend",
  "api_key": "sk-zZJt9eFxUY6S7ZEG0PuMwbYoiJN-ot1QE-_yctaJvn0",
  "id": "4197b884-93ad-4228-9ac0-33e53a9150c3",
  "is_active": true,
  "total_uses": 0
}
```

Field `api_key` **hanya muncul di balasan ini**. `GET /api/v1/api_key/` bisa
mendaftar key yang ada, tapi nilainya tidak ikut ditampilkan — hanya nama,
jumlah pemakaian, dan kapan terakhir dipakai.

Di koleksi Postman, kedua langkah itu sudah punya script yang menyimpan hasilnya
ke variabel koleksi (`bearer` dan `api_key`), jadi tinggal jalankan berurutan.

> **Key ikut hilang kalau Langflow di-recreate tanpa volume.** Basis data
> SQLite Langflow aslinya tersimpan di dalam folder paketnya sendiri, bukan di
> `LANGFLOW_CONFIG_DIR`. `docker-compose.yml` sudah menyetel
> `LANGFLOW_DATABASE_URL` ke volume untuk menutup lubang itu; kalau suatu saat
> key dan flow hilang sendiri, periksa env itu dulu.

---

## 2. Bentuk permintaan dan balasan

Semua endpoint memakai bentuk yang sama:

```http
POST {{base_url}}/api/v1/run/<endpoint>?stream=false
x-api-key: {{api_key}}
Content-Type: application/json

{
  "output_type": "chat",
  "input_type": "text",
  "input_value": "",
  "tweaks": { "<NODE_ID>": { ...parameter... } }
}
```

Balasannya berlapis, dan isi yang berguna ada di dalam **string** yang masih
perlu di-`JSON.parse`:

```json
{
  "outputs": [{ "outputs": [{ "results": { "message": {
    "text": "{\"status\": \"QUEUED\", \"jobId\": \"ds-grade-...\"}"
  }}}]}]
}
```

```js
const r = await fetch(url, { method: 'POST', headers, body });
const hasil = JSON.parse((await r.json()).outputs[0].outputs[0].results.message.text);
```

Selubung berlapis ini bawaan Langflow, bukan pilihan desain kami.

---

## 3. Empat jebakan yang sudah terbukti

**1. Kunci `tweaks` HARUS node id, bukan nama komponen.** Memakai nama komponen
tidak menimbulkan galat apa pun — flow tetap jalan, parameternya diam-diam tidak
tersampaikan, lalu node pertama gagal dengan "fileId kosong".

**2. Node id flow grading dan config bersifat TETAP.** Diturunkan dari
(nama endpoint, nama komponen), jadi membangun ulang flow tidak mengubahnya.
Aman di-hardcode di backend:

| Endpoint | Node id |
|---|---|
| `grading-dispatch` | `GradingDispatch-a3967` |
| `grading-status` | `GradingStatus-3cc03` |
| `config-rules` | `GradingRuleGet-9c9c5` |
| `config-rules-update` | `GradingRuleUpdate-ea0f7` |
| `grading` | `OpenGradingSession-ab8d5` |

**Matching adalah pengecualian** — node id-nya masih acak dan berubah tiap kali
`buat_flow.py` dijalankan. Bangkitkan ulang koleksi Postman sesudahnya.

**3. Parameter bertipe objek dikirim sebagai STRING berisi JSON**, bukan objek
JSON bersarang. Berlaku untuk `payload` di endpoint 1 dan 4.

**4. `tweaks` dikunci PER NODE — parameter tidak bisa dititipkan.** Satu
permintaan boleh memuat beberapa node sekaligus, dan memang harus, kalau
parameternya milik node yang berbeda:

```json
"tweaks": {
  "OpenMatchingSession-5951c": { "file_id": "d88150c5", "grade": 1 },
  "PersistResults-9be52":      { "dry_run": true }
}
```

`dry_run` milik node ke-7. Mengirimkannya lewat node pertama **tidak
menimbulkan galat apa pun** — nilainya diabaikan diam-diam, dan 200 ribu baris
tetap tertulis. Persis seperti jebakan nomor 1, kesalahannya hanya terlihat
dari akibatnya.

---

## 4. Endpoint 1 — Grading Dispatch

```http
POST {{base_url}}/api/v1/run/grading-dispatch?stream=false
```

```json
{
  "output_type": "chat", "input_type": "text", "input_value": "",
  "tweaks": { "GradingDispatch-a3967": {
    "payload": "{\"fileId\":\"csv_178...\",\"s3Bucket\":\"bucket-test\",\"parquetKey\":\"uploads/csv_178.../data.parquet\",\"callback\":{\"url\":\"http://portal:3000/api/internal/grading/callback\",\"secretToken\":\"rahasia\"}}"
  }}
}
```

Isi `payload` persis `OutboundGradingJobPayload` pada spesifikasi integrasi
bagian 2.1. `callback` boleh dikosongkan kalau backend memilih polling.

**Balasan** (diukur 0,6–1,2 detik):

```json
{"status": "QUEUED", "jobId": "ds-grade-20260915-b6cb12dc",
 "fileId": "uji-b", "message": "Grading job successfully queued for processing."}
```

Untuk pengujian manual, kolom satuan lebih enak daripada `payload`:

```json
"tweaks": { "GradingDispatch-a3967": {
  "file_id": "uji-b", "s3_bucket": "bucket-test",
  "parquet_key": "uploads/uji-b/data.parquet",
  "callback_url": "", "callback_token": ""
}}
```

---

## 5. Endpoint 2 — Grading Status

```json
"tweaks": { "GradingStatus-3cc03": { "file_id": "uji-b" } }
```

`file_id` mengembalikan job **terbaru** untuk berkas itu. Pakai `job_id` untuk
menanyakan percobaan tertentu.

**Balasan:**

```json
{
  "found": true, "status": "COMPLETED", "done": true,
  "jobId": "ds-grade-20260915-b6cb12dc", "fileId": "uji-b",
  "stage": "selesai",
  "queuedAt": "...", "startedAt": "...", "finishedAt": "...",
  "gradingDurationMs": 575,
  "enrichedParquetKey": "uploads/uji-b/enriched.parquet",
  "parquetSizeBytes": 101250, "recordCount": 2000,
  "callbackStatus": "SENT", "callbackError": null, "error": null,
  "result": { "...GradingCallbackPayload utuh..." }
}
```

**Pakai `done` untuk berhenti polling**, bukan `status` — `done` bernilai true
saat COMPLETED maupun FAILED, jadi backend tidak perlu menghafal daftar status.
Selang 2–3 detik lebih dari cukup: 299.088 baris selesai dalam 1,6 detik.

Berkas yang belum pernah digrading membalas
`{"found": false, "status": "NOT_FOUND"}`.

---

## 6. Endpoint 3 — Baca aturan grade

```json
"tweaks": { "GradingRuleGet-9c9c5": { "grade_id": "" } }
```

Kosongkan `grade_id` untuk semua grade; isi `"2"` untuk satu grade.

Balasannya menggabungkan tiga tabel jadi satu gambaran — `criteria` (ambang
kelengkapan, grade A–D), `score` (pita skor, A–F), dan `matching` (ambang
similarity). Lengkapnya di [`KONFIGURASI.md`](KONFIGURASI.md).

---

## 7. Endpoint 4 — Ubah aturan grade

```json
"tweaks": { "GradingRuleUpdate-ea0f7": {
  "payload": "{\"gradeId\":2,\"updatedBy\":\"reno\",\"dryRun\":true,\"criteria\":{\"minCompleteness\":{\"tempat_lahir\":0.75}},\"score\":{\"min\":72}}",
  "dry_run": false
}}
```

Perubahan **sebagian** — hanya field yang disebut yang berubah.
`"dryRun": true` memvalidasi dan menampilkan diff tanpa menulis apa pun.

Divalidasi sebelum ditulis; konfigurasi yang membuat sebuah grade tidak pernah
tercapai ditolak dengan `"applied": false` dan daftar `problems`.

**Perubahan berlaku seketika, tanpa restart.**

---

## 8. Endpoint 5 — Pipeline grading telanjang

```json
"tweaks": { "OpenGradingSession-ab8d5": {
  "file_id": "uji-b", "s3_bucket": "bucket-test",
  "parquet_key": "uploads/uji-b/data.parquet",
  "s3_endpoint": "", "enriched_key": ""
}}
```

**SINKRON — memblokir sampai selesai.** Ada supaya tiap tahap G1–G6 terlihat di
kanvas dan bisa ditelusuri satu per satu. Backend memakai endpoint 1, bukan ini.

---

## 9. Endpoint 6 — Matching

```http
POST {{base_url}}/api/v1/run/bacf3051-6540-45e6-8915-b8912db35d6a?stream=false
```

```json
"tweaks": {
  "OpenMatchingSession-<acak>": {
    "file_id": "d88150c5",
    "parquet_path": "s3://synchrono/curated/20260908_100245_d88150c5_data_dukcapil_gradeA.parquet",
    "grade": 1,
    "pakai_enriched": true
  },
  "PersistResults-<acak>": { "dry_run": true }
}
```

**`dry_run` WAJIB dikirim ke node `PersistResults`, bukan node pertama.** Tanpa
itu, sekali panggil berarti 200 ribu baris tertulis ke `institution`. Contoh di
koleksi Postman memakai `true`; setel `false` untuk benar-benar menyimpan —
penulisannya upsert, jadi aman diulang.

Dua hal yang membedakannya dari yang lain:

- **Belum punya `endpoint_name`**, jadi dipanggil dengan UUID flow
- **Node id-nya acak**, berubah tiap `buat_flow.py` dijalankan

`pakai_enriched: true` (bawaan) membuat matching memakai hasil grading yang
kolomnya sudah ternormalisasi, dicari dari `grading_jobs` berdasarkan `file_id`.
Lihat [`NORMALISASI.md`](NORMALISASI.md).

**SINKRON** — 200.000 baris memakan ±2,6 detik pada mesin pengembangan.

---

## 10. Kalau semuanya balas 500

Endpoint layanan menyentuh PostgreSQL; kalau DBngin mati, semuanya gagal
sekaligus dengan **500 berbadan kosong** — Langflow tidak meneruskan pesan
aslinya ke pemanggil.

Penyebabnya hanya terlihat di log container:

```bash
docker compose logs langflow --since 3m | grep -i error
```

```
Unable to connect to Postgres at "host=host.docker.internal port=5432 ...":
connection to server ... Connection refused
```

Folder `0. Auth` di koleksi tetap berhasil dalam keadaan itu, karena login dan
pembuatan key hanya menyentuh SQLite milik Langflow sendiri. **Kalau Auth
berhasil tapi keenam endpoint lain balas 500, curigai PostgreSQL lebih dulu.**

Penyebab lain yang pernah terjadi: SeaweedFS mati (`Could not resolve hostname
seaweedfs`) — periksa `docker compose ps`.

---

## 11. Kredensial pengembangan

| | |
|---|---|
| Langflow | `admin` / `synchrono123` |
| SeaweedFS | `synchrono` / `synchrono123` |
| PostgreSQL | `postgres`, tanpa kata sandi |

Semuanya untuk mesin lokal. **Ganti sebelum dipakai di luar itu**, termasuk
kredensial StarRocks dan MinIO di `.env`.
