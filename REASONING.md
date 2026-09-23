# Membangun Reasoning di langflow-synchrono

Panduan untuk menambahkan **AI reasoning** ke sistem matching Langflow —
padanan dari `data-matching/reasoning/` pada arsitektur lama.

Dokumen ini bukan spesifikasi yang harus diikuti huruf per huruf. Ia
menjelaskan apa yang sudah ada, apa yang belum, jebakan yang sudah diketahui,
dan satu keputusan arsitektur yang menurut kami paling menentukan. Kalau ada
rancangan yang lebih baik, silakan — asal alasannya ditulis.

---

## 1. Apa yang harus dihasilkan

Matching memberi vonis `MANUAL_REVIEW` pada baris yang skornya di ambang:
bukan cocok, bukan juga jelas beda. Petugas harus memutuskan sendiri.

Tanpa reasoning, yang dilihat petugas hanya dua kolom data berdampingan dan
sebuah angka skor. Ia harus membandingkan sendiri field demi field untuk tahu
apa yang sebenarnya berbeda.

Reasoning menuliskannya jadi satu kalimat, tersimpan di basis data:

> "Full name is different (Institution: Budianto Sudarsono vs Master: Budi
> Sudarsono), date of birth is empty in institution, and mother's name is
> different (Institution: Siti Aminah vs Master: Suti Aminah)."

Satu kalimat per baris manual review. Bukan per baris berkas — baris yang
auto-match atau auto-unmatch tidak perlu dijelaskan.

**Reasoning tidak mengubah keputusan matching.** Ia hanya menjelaskan keputusan
yang sudah diambil. Kalau suatu saat rancangannya membuat reasoning bisa
menggeser `match_result`, berhenti dan bicarakan dulu — itu perubahan sifat
sistem, bukan penambahan fitur.

---

## 2. Yang SUDAH ada — jangan dibangun ulang

Ini bagian terpenting dokumen ini. Lahannya sudah disiapkan sejak awal.

### Tabel `manual_matches` sudah punya seluruh kolomnya

`infra/db/migrasi/001_dasar.sql:138`

```sql
CREATE TABLE IF NOT EXISTS manual_matches (
    file_id                varchar(64) NOT NULL,
    id_incoming            text        NOT NULL,
    nama_incoming          text,
    tempat_lahir_incoming  text,
    tanggal_lahir_incoming text,
    jenis_kelamin_incoming text,
    nama_ibu_incoming      text,
    reason                 text,          -- <- kalimat penjelasannya
    pattern_name           varchar(255),  -- <- pola yang dipakai
    reasoning_source       varchar(20),   -- <- 'CACHE' atau 'LLM'
    reasoning_status       varchar(30) NOT NULL,
    PRIMARY KEY (file_id, id_incoming)
);
```

### Node 7 sudah mengisinya, berstatus PENDING

`components/matching/n7_persist.py:117`

Setiap kali matching jalan, baris ber-`match_result = 2` masuk ke
`manual_matches` dengan `reasoning_status = 'PENDING'`, **lengkap dengan
snapshot nilai incoming-nya**. Itu disengaja, dan komentarnya menyebutkan
alasannya: supaya reasoning tidak perlu membaca parquet ulang per baris.

Artinya antrean kerja reasoning sudah terbentuk sendiri. Kueri pertama yang
perlu ditulis kira-kira:

```sql
SELECT * FROM manual_matches
WHERE file_id = ? AND reasoning_status IN ('PENDING', 'FAILED')
```

N7 memakai UPSERT, jadi menjalankan matching dua kali tidak menggandakan
antrean — baris yang sama diperbarui, dan `reasoning_status` dikembalikan ke
`PENDING`.

### Sisi master diambil lewat `institution`

`manual_matches` **tidak menyimpan nilai master**, hanya nilai incoming. Nilai
master diambil dengan menyambung dua tabel:

```
manual_matches  --(file_id, id_incoming)-->  institution.nik_master  --(nik)-->  master
```

`institution` punya `nik_master`, `match_score`, dan `match_result`
(`001_dasar.sql:118`). Tabel `master` punya `nama_lengkap`, `tempat_lahir`,
`tanggal_lahir`, `jenis_kelamin`, `nama_ibu` — persis kelima field yang
dibandingkan (`001_dasar.sql:70`).

### Klien LLM sudah ada

`lib/_llm.py` — klien minimal OpenAI-compatible, sudah dipakai lapis kelima
pengenalan kolom. **Pakai ini, jangan menambah dependency baru.** Seluruh
sistem ini hanya bergantung pada `duckdb` dan `python-dotenv`; menambahkan
`langchain` demi reasoning akan membalik keputusan desain yang disengaja.

Baca bagian privasinya di §7 sebelum memakainya.

### Kolam koneksi sudah ada

`lib/_kolam.py` — `with pinjam() as con:` untuk pekerjaan pendek. Membuka
koneksi sendiri memakan ~328 ms per panggilan; kolam menghapusnya. Lihat §6
untuk kapan kolam TIDAK boleh dipakai.

### Yang belum ada

| Perlu dibuat | Catatan |
|---|---|
| Tabel `reasoning_patterns` | migrasi baru, lihat §5.1 |
| Deteksi pola per baris | §4 — jangan tiru cara lama |
| Mesin reasoning | `lib/_reasoning.py` |
| Node + flow | `components/reasoning/` |
| Pemicu | §5.5 |

---

## 3. Referensinya: `data-matching/reasoning/`

Implementasi lama ada di repo `data-matching`, dan layak dibaca lebih dulu —
terutama **prompt-nya**, yang sudah melewati banyak putaran perbaikan.

| Berkas | Isi | Layak disalin? |
|---|---|---|
| `reasoning/prompt.py` | system prompt | **Ya, hampir apa adanya** |
| `reasoning/pattern_detector.py` | vonis per field + hash pola | Idenya ya, kodenya tidak — lihat §4 |
| `reasoning/reasoning_service.py` | mesin + cache | Idenya ya, strukturnya tidak |
| `reasoning/tasks.py` | orkestrasi Celery | Tidak — tidak ada Celery di sini |
| `reasoning/schema.py` | skema output | Hati-hati, ada cacatnya (§7) |

### Yang paling berharga dari implementasi lama

Prompt-nya memberi LLM **vonis per field sebagai ground truth**, lalu melarang
keras LLM menilai sendiri apakah dua nilai berbeda:

> "NEVER claim a field is different when its verdict is `SAME`. This is the most
> important rule. Do not describe a field as differing just because the two
> spellings look unusual to you — trust the verdict, not your own comparison."

Perbandingannya dikerjakan kode; LLM hanya merangkai kalimat. Itu yang menutup
jalan bagi LLM mengarang perbedaan yang tidak ada. **Pertahankan pembagian
tugas ini.**

### Cache berbasis pola

Pola perbedaan berulang. "Nama beda, tanggal lahir kosong di institusi" akan
muncul ribuan kali dalam satu berkas. Implementasi lama memanfaatkannya:
jawaban LLM diubah jadi template berisi placeholder, disimpan dengan kunci hash
pola, dan baris berikutnya yang polanya sama dijawab dari template tanpa
memanggil LLM.

**Pertahankan ini juga.** Ia yang membuat ongkos LLM tidak tumbuh sebanding
jumlah baris.

---

## 4. Keputusan arsitektur yang paling menentukan

**Jangan tiru cara `data-matching` memproses baris satu per satu.**

Implementasi lama menyebar task Celery per baris, mengulang di Python, dan
memanggil LLM tiap kali cache meleset. Itu wajar untuk arsitektur yang memang
bekerja baris demi baris.

Di sini tidak. Seluruh sistem ini dibangun di atas satu prinsip: **data tidak
berpindah ke Python; ia diolah di dalam DuckDB lewat SQL.** Prinsip itu yang
membuat tahap join master turun dari 84 detik jadi hitungan detik.

Reasoning bisa mengikuti prinsip yang sama:

### Tiga langkah, bukan satu lingkaran

**Langkah 1 — vonis seluruh baris dalam SATU statement SQL.**

Kelima field dibandingkan dengan `CASE`, menghasilkan vonis per field dan
sebuah tanda tangan pola. Rancangan kasarnya:

```sql
CREATE OR REPLACE TEMP TABLE vonis AS
SELECT
    mm.file_id,
    mm.id_incoming,
    CASE
        WHEN <incoming kosong> THEN 'EMPTY_IN_INSTITUTION'
        WHEN <master kosong>   THEN 'EMPTY_IN_MASTER'
        WHEN <sama>            THEN 'SAME'
        ELSE 'DIFFERENT'
    END AS v_nama,
    -- ... empat field lainnya
FROM pg.public.manual_matches mm
JOIN pg.public.institution i USING (file_id, id_incoming)
LEFT JOIN pg.public.master m ON m.nik = i.nik_master
WHERE mm.file_id = ? AND mm.reasoning_status IN ('PENDING','FAILED');
```

Lalu tanda tangannya cukup penggabungan kelima vonis, mis.
`DIFFERENT|SAME|EMPTY_IN_INSTITUTION|SAME|DIFFERENT`.

(SQL di atas **sketsa, belum diuji** — definisi "kosong" dan "sama" harus
disamakan dengan `pattern_detector.py` lama, termasuk normalisasi gender
`l/laki-laki/pria` dan penanganan string `"null"`/`"none"`.)

**Langkah 2 — panggil LLM sekali per pola BARU, bukan per baris.**

```sql
SELECT tanda_tangan, MIN(id_incoming) AS contoh, COUNT(*) AS jumlah
FROM vonis GROUP BY tanda_tangan
```

Lima field dengan empat kemungkinan vonis memberi batas teoretis 4⁵ = 1.024
pola, dan dalam praktik hanya beberapa puluh yang benar-benar muncul. Dari
jumlah itu, hanya yang belum ada di `reasoning_patterns` yang perlu LLM.

Jadi berkas dengan 50.000 baris manual review mungkin hanya memanggil LLM
belasan kali — bukan 50.000, bukan juga "sebanyak cache meleset".

**Langkah 3 — terapkan template ke semua baris dengan SATU UPDATE.**

Template diisi nilai tiap baris lewat SQL (`replace`/`format`), lalu ditulis
balik ke `manual_matches` sekaligus. Tidak ada lingkaran Python di sini.

### Kenapa ini layak diperjuangkan

Selain jauh lebih cepat, ia menghapus seluruh kelas masalah yang ada di versi
lama: tidak ada fan-out task, tidak ada race antar worker yang memperebutkan
`COUNT(*)` saat menamai pola (bug #8 di `BUG_FIXING_GUIDE.md` lama), tidak ada
Celery yang harus dipasang dan diawasi.

Kalau ternyata ada alasan kuat untuk tetap baris demi baris, tulis alasannya —
tapi coba jalur SQL dulu.

---

## 5. Rancangan yang disarankan

### 5.1 Migrasi: `infra/db/migrasi/005_reasoning_patterns.sql`

Nomornya lanjut dari yang terakhir di `infra/db/migrasi/` (saat ini `004`).
Perhatikan: `infra/db/seeder/` punya penomoran **sendiri** dan sudah sampai
`005` — keduanya tidak berhubungan, jangan tertukar.

Jangan menyunting migrasi lama. Tiap berkas dicatat di `schema_migrations`
beserta checksum-nya, dan mengubah yang sudah diterapkan akan membuat
`migrate.py` menolak jalan.

Kolom minimal, mengikuti versi lama: `pattern_hash` (primary key),
`pattern_name`, `pattern_signature`, `reason_template`, `sample_id`,
`hit_count`, `created_at`, `updated_at`.

Satu perbaikan yang layak dipertimbangkan: di versi lama `pattern_name`
dirangkai dari `COUNT(*) + 1`, yang menimbulkan race antar worker paralel.
Karena rancangan di §4 hanya punya satu penulis per berkas, masalahnya hilang
sendiri — tapi jangan bangkitkan lagi dengan menyalin polanya.

### 5.2 Mesin: `lib/_reasoning.py`

**Harus di `lib/`, bukan di `components/`.** Ini bukan pilihan gaya — Langflow
memuat tiap berkas komponen sebagai bundle module terisolasi, dan foldernya
tidak pernah masuk `sys.path`. Dua berkas di folder komponen yang sama pun
tidak bisa saling impor. Lihat `README.md` §4.

Fungsi yang masuk akal:

```python
def vonis(con, file_id) -> None        # langkah 1, bikin TEMP TABLE
def pola_baru(con) -> list[dict]       # pola yang belum ada di cache
def mintakan_kalimat(pola) -> str      # panggil LLM, satu pola
def simpan_pola(con, ...) -> str       # tulis ke reasoning_patterns
def terapkan(con, file_id) -> int      # langkah 3, UPDATE massal
def jalankan_penuh(job) -> dict        # rangkaiannya, untuk pekerja latar
```

Pola `jalankan_penuh` sengaja meniru `lib/_grading.py:1179` supaya bisa
dipanggil dua tempat: node di kanvas, dan thread latar.

### 5.3 Node: `components/reasoning/`

Nama subfolder jadi nama grup di sidebar Langflow, jadi `reasoning/` akan
muncul sebagai grup tersendiri.

Minimal dua node, mengikuti pola grading:

* `R1 Reasoning Dispatch` — catat antrean, lepas thread latar, balas seketika
* `R2 Reasoning Status` — tanya sudah selesai belum

Plus node per tahap kalau ingin tahapannya terlihat di kanvas (`R3` vonis,
`R4` pola, `R5` terapkan). Itu berguna saat menelusuri, tapi ingat: menyiapkan
tiap node memakan ~55 ms, dibayar sebelum satu baris pun dibaca. Jangan
memecah lebih halus daripada yang benar-benar berguna untuk penelusuran.

### 5.4 Flow dan builder

Salin pola `infra/buat_flow_grading.py`. Yang penting: **node id-nya
diturunkan dari (nama endpoint, nama komponen)**, sehingga membangun ulang flow
tidak mengubahnya dan portal boleh menanamkannya. Lihat `infra/flow_util.py`.

Jangan tiru `infra/buat_flow.py` (matching) — yang itu masih memakai node id
acak, dan itu utang teknis yang sedang ingin dihilangkan, bukan ditiru.

### 5.5 Pemicu

N7 sengaja tidak memicu reasoning. Docstring-nya menyatakannya:

> "YANG SENGAJA TIDAK DIKERJAKAN DI SINI: Status alur, pemicu reasoning/export,
> dan audit. Itu urusan pemanggil."

Tiga pilihan, dengan rekomendasi:

| Pilihan | Untung | Rugi |
|---|---|---|
| **Flow `reasoning-dispatch` terpisah, dipanggil portal sesudah matching** | matching tidak melambat; reasoning bisa diulang tanpa mengulang matching | portal perlu satu panggilan tambahan |
| Node N8 di ujung flow matching | tidak ada panggilan tambahan | matching ikut menunggu LLM — latensinya tidak bisa diramalkan |
| Thread latar dilepas N7 | otomatis | melanggar pemisahan yang sudah dinyatakan N7 |

**Rekomendasi: yang pertama.** Ia mengikuti pola `grading-dispatch` yang sudah
terbukti, dan yang paling penting — reasoning bisa dijalankan ulang untuk baris
`FAILED` tanpa harus mengulang seluruh matching.

Kalau dipilih yang asinkron, `lib/_worker.py` adalah contoh lengkapnya:
semaphore pembatas paralel, detak supaya job tidak dikira mangkrak, status
`QUEUED → RUNNING → COMPLETED/FAILED`, dan pemanenan job mangkrak.

---

## 6. Jebakan khas repo ini

Semuanya sudah pernah memakan waktu orang. Empat yang pertama **tidak
memunculkan error sama sekali**.

**Nama input dan output tidak boleh sama.** Node yang punya input `session` dan
output `session` akan **hilang dari sidebar** — tanpa error, hanya satu baris
warning di log. Beri nama output sendiri (`vonis_ready`, `pola_ready`).
Perintah pertama saat node tidak muncul:

```bash
docker compose logs langflow | grep -i "could not build template"
```

**Berkas di folder komponen tidak bisa saling impor.** Taruh logika di `lib/`,
yang masuk lewat `PYTHONPATH`. Lihat §5.2.

**Flow menyimpan SALINAN kode komponen.** Mengubah berkas di `components/` lalu
merestart container **tidak berefek apa pun** — Langflow menjalankan kode yang
tersimpan di dalam flow saat flow itu dibangun. Setelah mengubah komponen,
flow harus dibangun ulang:

```bash
docker compose -f docker-compose.server.yml --env-file .env \
  exec langflow sh -c "cd /synchrono/infra && python buat_flow_reasoning.py"
```

Ini jebakan paling mahal di repo ini. Lihat `DEPLOY.md` langkah 3.

**`tweaks` dikunci node id, bukan nama komponen.** Salah kunci = parameter
diam-diam tidak sampai, dan node pertama gagal dengan pesan yang menyesatkan.

**Semua balasan Langflow 200,** termasuk eksekusi yang gagal. Jangan menilai
keberhasilan dari kode status; urai badannya.

**Kolam koneksi: pakai untuk pekerjaan pendek, JANGAN untuk yang memegang
view.** `with pinjam() as con:` cocok untuk node status dan pembacaan singkat.
Tapi node yang membuat TEMP TABLE (seperti langkah 1 di §4) memegang keadaan di
koneksinya — mengembalikannya ke kolam berarti peminjam berikutnya mewarisi
tabel milik pekerjaan orang lain, dan karena namanya sama, ia tidak dapat galat
melainkan **jawaban yang salah**. Untuk itu pakai `buka_koneksi()` sendiri dan
tutup setelah selesai, persis seperti `buka()` di `lib/_grading.py`.

---

## 7. Soal LLM: privasi dan satu cacat yang diwarisi

### Reasoning WAJIB memakai LLM on-prem

Tidak seperti pengenalan kolom yang bisa dijalankan hanya dengan nama kolom,
**reasoning harus mengirim nilai aslinya** — kalimat keluarannya mengutip nama
lengkap, tempat dan tanggal lahir, serta nama ibu kandung. Itu data
kependudukan.

`lib/_llm.py` sudah menjaganya: `kirim_sampel` ditolak pada endpoint non-lokal
kecuali ada persetujuan eksplisit lewat `NORMALISASI_AI_IZIN_SAMPEL_LUAR=1`.
**Jangan menyalakan flag itu untuk reasoning.** Pakai Ollama on-prem
(`OLLAMA_LOCAL_BASE_URL`), dan kalau on-prem belum tersedia, reasoning ditunda
— bukan dijalankan lewat layanan publik.

Kalau perlu variabel environment sendiri supaya reasoning tidak menumpang
setelan normalisasi, buat `REASONING_AI_*` terpisah. Itu lebih jelas daripada
dua fitur berbagi satu sakelar.

### Cacat yang jangan ikut disalin

`data-matching/reasoning/schema.py` mendeskripsikan field `reason` sebagai
*"Penjelasan singkat dalam Bahasa Indonesia"*, sedangkan `prompt.py`
memerintahkan `MUST be in English`. Pada mode structured output keduanya
sama-sama sampai ke LLM, jadi ia menerima dua instruksi yang berlawanan.

Tentukan satu bahasa lalu konsisten. Kalau keluarannya untuk petugas Indonesia,
kemungkinan besar seluruh prompt-nya yang harus diterjemahkan, bukan sekadar
deskripsi field-nya.

---

## 8. Selesai kalau...

1. `python migrate.py --status` menunjukkan migrasi reasoning sudah diterapkan
2. Node reasoning muncul di sidebar Langflow (kalau tidak, lihat §6)
3. Matching satu berkas uji → `manual_matches` terisi `reasoning_status='PENDING'`
4. Memicu reasoning → seluruh baris jadi `COMPLETED`, `reason` terisi
5. Kalimatnya **hanya menyebut field yang benar-benar berbeda** — ini yang
   paling perlu diperiksa manual pada beberapa baris contoh
6. Menjalankannya dua kali tidak menggandakan apa pun (UPSERT)
7. `SELECT reasoning_source, COUNT(*) FROM manual_matches WHERE file_id=? GROUP BY 1`
   menunjukkan `CACHE` jauh lebih banyak daripada `LLM`
8. Jumlah panggilan LLM sebanding jumlah **pola**, bukan jumlah **baris**

Nomor 5 dan 8 yang paling menentukan. Nomor 5 karena kalimat yang mengarang
perbedaan lebih buruk daripada tidak ada kalimat sama sekali — petugas akan
mempercayainya. Nomor 8 karena di situlah seluruh keuntungan rancangan §4.

---

## 9. Menjalankannya di laptop sendiri

Bagian ini untuk yang baru pertama kali menyalakan repo ini. `README.md` §1–5
memuat panduan lengkapnya — yang di bawah ini ringkasan jalur tercepatnya,
ditambah hal-hal yang khusus berguna saat mengerjakan reasoning.

### 9.1 Prasyarat

| | |
|---|---|
| Docker Desktop | untuk SeaweedFS dan Langflow |
| Python 3.12 + `duckdb` | untuk skrip di `infra/` dan pengujian tanpa Langflow |
| PostgreSQL di `localhost:5432` | database `synchrono`, user `postgres`, tanpa kata sandi |
| Ollama (opsional, tapi perlu untuk reasoning) | di `localhost:11434` |

PostgreSQL-nya **tidak** ikut di compose lokal. Kalau belum punya, satu baris
ini cukup dan cocok persis dengan DSN yang sudah tertulis di compose:

```bash
docker run -d --name pg-synchrono -p 5432:5432 \
  -e POSTGRES_DB=synchrono -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres:16
```

`POSTGRES_HOST_AUTH_METHOD=trust` dipakai karena DSN di compose memang tanpa
kata sandi. Itu hanya pantas untuk laptop — jangan pernah dipakai di server.

> Jangan tertukar dengan `infra/docker-compose.infra.yml`. Berkas itu untuk
> **server**, dan memakai port 5430 dengan user `root`. Compose lokal
> menuliskan DSN-nya secara harfiah (`port=5432 user=postgres`), jadi memakai
> yang server di laptop berarti harus menyunting compose lokal.

### 9.2 Menyalakan

```bash
cd infra

# 1. SeaweedFS lebih dulu — Langflow tidak membutuhkannya untuk menyala,
#    tapi tiap flow yang membaca parquet akan gagal tanpanya.
docker compose up -d seaweedfs

# 2. Skema + data awal. Aman diulang: migrate mencatat di schema_migrations,
#    seed memakai ON CONFLICT DO NOTHING.
python migrate.py
python seed.py

# 3. Langflow
docker compose up -d langflow
docker compose logs -f langflow        # tunggu sampai siap (~30-90 detik)
```

Alamat yang berguna:

| | |
|---|---|
| Langflow UI | `http://localhost:7860` (admin / synchrono123) |
| S3 API | `localhost:8333` ← yang dibaca DuckDB |
| SeaweedFS filer | `http://localhost:8888` — penjelajah berkas |

### 9.3 Membangun flow

```bash
cd infra
python buat_flow_grading.py
python buat_flow_config.py
python buat_flow.py            # matching
```

Node id grading dan config bersifat tetap; matching masih acak (lihat §5.4).

### 9.4 Lingkar kerja sehari-hari — BACA INI

Ini yang paling menghemat waktu, dan yang paling sering bikin orang bingung di
hari pertama. **Aturannya berbeda tergantung apa yang diubah:**

| Yang diubah | Yang harus dilakukan |
|---|---|
| `lib/*.py` | restart container: `docker compose restart langflow` |
| `components/**/*.py` | restart container **DAN bangun ulang flow** |
| `infra/db/migrasi/*.sql` | `python migrate.py` |
| compose / `.env` | `docker compose up -d` |

Baris kedua itu jebakannya. Langflow menjalankan **salinan kode komponen yang
tersimpan di dalam flow**, bukan berkas di disk. Mengubah berkas komponen lalu
merestart container **tidak berefek apa pun** — tanpa error, tanpa peringatan,
endpointnya tetap menjawab 200 dengan perilaku lama. Satu-satunya tanda adalah
perubahanmu seperti tidak terjadi.

Cara memastikan flow benar-benar memakai kode baru: cari sesuatu yang khas dari
perubahanmu di kode yang tersimpan, atau cukup tambahkan `print()` sementara
dan lihat lognya.

### 9.5 Cara tercepat: jangan lewat Langflow sama sekali

Siklus "ubah kode → restart container → bangun ulang flow → buka kanvas →
klik jalankan" memakan menit. Untuk mengembangkan logika, jangan pakai itu.

Repo ini punya dua skrip yang menjalankan seluruh rantai node dari baris
perintah: `run_local.py` (matching) dan `run_grading_local.py` (grading).
Keduanya mengimpor fungsi yang sama dengan yang dipakai node.

**Buat `run_reasoning_local.py` yang setara sebagai langkah pertama.** Ia akan
jadi alat kerja utamamu: hitungan detik per putaran, tanpa container, dan
kegagalannya muncul sebagai traceback Python biasa — bukan sebagai flow yang
diam-diam membalas 200.

Pasang `--dry-run` sebagai bawaannya, seperti `run_local.py`, supaya mencoba
tidak pernah menulis ke basis data tanpa disengaja.

### 9.6 LLM di laptop

`lib/_llm.py` membaca `OLLAMA_LOCAL_BASE_URL`. Dari dalam container Langflow,
Ollama di laptop dijangkau lewat `host.docker.internal`, bukan `localhost`:

```yaml
# infra/docker-compose.yml, bagian environment langflow
OLLAMA_LOCAL_BASE_URL: "http://host.docker.internal:11434"
```

Compose lokal sudah memasang `extra_hosts: host.docker.internal:host-gateway`,
jadi tidak ada setelan tambahan yang perlu.

Model yang dipakai sistem ini `gemma4:31b` — berat untuk laptop. Untuk
mengembangkan reasoning, model kecil apa pun cukup: yang diuji adalah
alurnya, bukan mutu kalimatnya. Baru pakai model sebenarnya saat menilai
kualitas keluaran.

```bash
ollama pull llama3.1:8b
```

Lalu setel `NORMALISASI_AI_MODEL` (atau variabel `REASONING_AI_MODEL` sendiri,
lihat §7) ke model itu.

---

## 10. Berkas yang harus dibaca lebih dulu

Urut kepentingan:

| Berkas | Kenapa |
|---|---|
| `components/matching/n7_persist.py` | tempat antrean reasoning terbentuk |
| `infra/db/migrasi/001_dasar.sql` baris 70–152 | skema `master`, `institution`, `manual_matches` |
| `data-matching/reasoning/prompt.py` | prompt yang sudah matang, layak disalin |
| `lib/_grading.py` | pola sesi + `jalankan_penuh` yang layak ditiru |
| `lib/_worker.py` | pola pekerja latar, kalau memilih jalur asinkron |
| `lib/_llm.py` | klien LLM + penjagaan privasinya |
| `lib/_kolam.py` | kapan memakai kolam, kapan tidak |
| `README.md` §4 | empat jebakan pemasangan |
| `DEPLOY.md` §3 | kenapa flow harus dibangun ulang |

---

## 11. Kalau ada yang tidak jelas

Dua hal yang sengaja **tidak** diputuskan di dokumen ini, dan sebaiknya
dibicarakan sebelum ditulis:

* **Bahasa keluaran** — Inggris seperti sekarang, atau Indonesia? Ini
  menentukan seluruh prompt, bukan cuma satu baris.
* **Siapa yang memicu** — portal, atau otomatis dari matching? §5.5 memberi
  rekomendasi, tapi itu menyentuh kontrak dengan portal, jadi bukan keputusan
  sepihak.
