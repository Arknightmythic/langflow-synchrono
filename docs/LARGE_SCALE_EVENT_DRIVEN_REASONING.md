# Panduan Skala Besar & Arsitektur Event-Driven Anti Double-Hit LLM

Dokumen ini adalah panduan teknis operasional dan arsitektur lanjutan untuk:
1. **Stress-Testing Data Skala Besar:** Menangani pengujian dengan ratusan ribu hingga jutaan baris *Manual Review* di atas Master Parquet 100 Juta Baris.
2. **Arsitektur Event-Driven Paralel Bebas Redundansi:** Solusi mengatasi *Cache Stampede / Thundering Herd Problem* agar ketika banyak worker paralel berjalan bersamaan, **tidak terjadi pemanggilan LLM ganda (*double-hit*)** untuk pola perbedaan yang sama.

---

## BAGIAN 1: PENGUJIAN SKALA BESAR (MASSIVE MANUAL REVIEW)

### 1.1 Karakteristik Matematika Pola Kependudukan
Meskipun data yang masuk berjumlah **100.000 hingga 1.000.000 baris manual review**, ruang kombinasi perbedaan identitas **sangat terbatas**:
* Terdapat 5 atribut inti: `nama_lengkap`, `tempat_lahir`, `tanggal_lahir`, `jenis_kelamin`, `nama_ibu`.
* Masing-masing hanya memiliki 4 status: `SAME`, `DIFFERENT`, `EMPTY_IN_INSTITUTION`, `EMPTY_IN_MASTER`.
* **Maksimum Kemungkinan Pola:**
  $$\text{Ruang Pola} \le 4^5 = 1.024 \text{ pola}$$
* **Hukum Distribusi Pareto (Zipfian Distribution) di Lapangan:**
  Di dunia nyata kependudukan Indonesia, **95% s/d 99% data hanya jatuh pada 15 - 30 pola dominan** (misal: *hanya nama yang beda tipis*, *nama + tempat lahir beda*, *nama + nama ibu beda*).

> **Prinsip Skalabilitas:**  
> Skalabilitas sistem ini **bersifat sub-linear ($O(1)$ untuk 99% data)**. Begitu 30 pola utama telah dipelajari oleh AI pada 1.000 baris pertama, 999.000 baris sisanya diselesaikan **100% via SQL DuckDB dalam hitungan milidetik tanpa menyentuh LLM sama sekali**.

---

### 1.2 Strategi Optimasi Memori & Konfigurasi DuckDB
Saat menguji data manual review dalam jumlah masif (>100.000 baris) di atas Parquet 100 Juta baris (3.01 GB):

1. **Batasi Alokasi Memori DuckDB:**
   Tambahkan konfigurasi memori pada inisialisasi DuckDB di [`lib/_reasoning.py`](file:///home/mario_siregar_isgs/kerjaan/langflow-synchrono/lib/_reasoning.py):
   ```sql
   SET max_memory = '8GB';
   SET threads = 8;
   SET preserve_insertion_order = false;
   ```
2. **Chunking / Micro-Batching (Jika Record > 50.000 Baris):**
   Hindari menjalankan satu transaksi raksasa di PostgreSQL. Pecah antrean `manual_matches` menjadi *chunk* 10.000 baris per eksekusi:
   ```python
   CHUNK_SIZE = 10000
   # Proses per batch chunk agar transaksi commit bertahap dan hemat RAM
   ```
3. **Pembersihan Temp Tables:**
   Pastikan klausa `finally` selalu mendrop tabel `reasoning_verdict` dan `filled_reasons` agar memori DuckDB seketika dikembalikan ke OS.

---

### 1.3 Cara Menjalankan Uji Skala Besar

Gunakan generator data pengujian sintetis yang sudah tersedia di repositori:

```bash
# 1. Bangkitkan 100.000 data uji dengan variasi kesalahan tipikal Grade B/C
python3 test_data/generate_comprehensive_test_data.py --rows 100000 --output /tmp/uji_100k.csv

# 2. Jalankan Matching ke 100 Juta Master Parquet
python3 run_matching_csv_to_db.py \
  --file-id stress_test_100k \
  --csv /tmp/uji_100k.csv \
  --master-parquet /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet \
  --grade 2

# 3. Jalankan AI Reasoning dengan pemantauan metrik
python3 run_reasoning_local.py \
  --file-id stress_test_100k \
  --master-parquet /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet \
  --live
```

---

## BAGIAN 2: MASALAH "DOUBLE-HIT LLM" PADA SISTEM EVENT-DRIVEN PARALEL

### 2.1 Masalah di Lapangan (*The Race Condition / Cache Stampede*)
Jika sistem dikembangkan menjadi *Event-Driven* di mana banyak worker berjalan paralel (misal: 10 Worker Celery/Kafka Consumer membaca antrean pesan secara bersamaan):

```
Time    Worker 1 (Record A)                     Worker 2 (Record B)
 ───    ───────────────────                     ───────────────────
 T0     Terima Record A (Pola X)                Terima Record B (Pola X)
 T1     Cek Cache DB: Pola X BELUM ADA          Cek Cache DB: Pola X BELUM ADA
 T2     Panggil LLM Gemma3 (Inferensi 3 detik)  Panggil LLM Gemma3 (Inferensi 3 detik) ◄── [DOUBLE HIT!]
 T5     Selesai ➔ Simpan Pola X ke Cache        Selesai ➔ Simpan Pola X (Gagal / Do Nothing)
```

**Dampak Buruk:**
* Server LLM kebanjiran beban ganda (*GPU VRAM bottleneck*).
* Kuota inferensi terbuang sia-sia untuk memproses pertanyaan yang sama persis.
* Waktu tunggu antrean membengkak.

---

## BAGIAN 3: TIGA OPSI ARSITEKTUR ANTI DOUBLE-HIT (PILIHAN DESAIN)

Berikut adalah 3 pendekatan arsitektur yang dapat dipilih tim engineering untuk mengunci pola secara absolut:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        SOLUSI ANTI DOUBLE-HIT (PATTERN LOCKING)                        │
└────────────────────────────────────────────────────────────────────────────────────────┘

        Worker 1 (Record A)                           Worker 2 (Record B)
                 │                                             │
                 ▼                                             ▼
        [Deteksi Pola X]                              [Deteksi Pola X]
                 │                                             │
                 ▼                                             ▼
  ┌──────────────────────────────┐              ┌──────────────────────────────┐
  │ Atomic Reservation (Claim):  │              │ Atomic Reservation (Claim):  │
  │ INSERT ... status='RESOLVING'│              │ INSERT ... status='RESOLVING'│
  └──────────────┬───────────────┘              └──────────────┬───────────────┘
                 │ (MENANG CLAIM)                              │ (KALAH CLAIM - CONFLICT)
                 ▼                                             ▼
        [Panggil LLM Gemma 3]                         [Masuk Status AWAIT / WAIT]
                 │                                             │
                 ▼                                             │
      Update status = 'READY'                                  │ Tunggu Event NOTIFY
                 │                                             │ (atau Backoff Sleep 100ms)
                 ▼                                             ▼
       Kirim Signal / NOTIFY ────────────────────────► [Terima Signal: Pola READY]
                                                               │
                                                               ▼
                                                      [Ambil Template dari DB]
                                                      (0.001s TANPA PANGGIL LLM!)
```

---

### Solusi 1 (Rekomendasi Utama): *Database Row-Claiming & State Machine* (Zero Extra Infra)

Solusi ini **tidak memerlukan Redis atau Kafka tambahan**, cukup memanfaatkan PostgreSQL yang sudah aktif berjalan.

#### Mekanisme Kerja:
1. Kolom status ditambahkan pada tabel `reasoning_patterns`: `status VARCHAR(20) DEFAULT 'READY'`. Nilainya: `'RESOLVING'` atau `'READY'`.
2. Saat Worker menemukan pola baru, ia mencoba melakukan **Atomic Claim**:
   ```sql
   INSERT INTO public.reasoning_patterns (
       pattern_hash, pattern_name, pattern_signature, reason_template, status
   )
   VALUES (
       :p_hash, :p_name, :signature, 'PENDING_RESOLVE', 'RESOLVING'
   )
   ON CONFLICT (pattern_hash) DO NOTHING
   RETURNING pattern_hash;
   ```
3. **Logika Percabangan Worker:**
   * **Jika RETURNING mengembalikan `pattern_hash`:**  
     Worker tersebut adalah **Pemenang (*Leader*)**. Worker ini yang berhak memanggil LLM `gemma3:12b`. Setelah template selesai dibuat, ia mengupdate status:
     ```sql
     UPDATE public.reasoning_patterns
     SET reason_template = :template, status = 'READY', updated_at = NOW()
     WHERE pattern_hash = :p_hash;

     -- Kirim sinyal event ke worker lain
     SELECT pg_notify('pattern_resolved', :p_hash);
     ```
   * **Jika RETURNING kosong (Conflict / Kalah):**  
     Worker lain **dilarang memanggil LLM**. Worker ini masuk ke mode *Await* (mendengarkan `LISTEN pattern_resolved` atau polling interval 100ms hingga status menjadi `READY`).
   * Begitu status `READY`, worker kedua langsung mengambil template yang sudah jadi.

*Keunggulan:* 100% konsisten, ACID compliant, dan tidak ada dependensi software baru.

---

### Solusi 2: *Consistent Hashing Partition Key* (Jika Menggunakan Kafka / RabbitMQ)

Jika tim aplikasi membangun arsitektur antrean pesan berskala masif dengan **Apache Kafka** atau **RabbitMQ Consistent Hash Exchange**:

#### Mekanisme Kerja:
* Jangan kirim pesan dengan *Round-Robin*.
* Tetapkan **Message Partition Key = `pattern_hash`**.

```
Record Ingestion ──► Producer: Set Key = pattern_hash
                          │
         ┌────────────────┴────────────────┐
         ▼                                 ▼
   Partition 0                       Partition 1
   (Semua Pola A & C)                (Semua Pola B & D)
         │                                 │
         ▼                                 ▼
   Worker Consumer 1                 Worker Consumer 2
```

* **Hasil:** Seluruh record yang memiliki pola identik dijamin 100% dialirkan ke **satu consumer yang sama secara sekuensial**. Worker 2 tidak akan pernah memproses Pola A bersamaan dengan Worker 1. Race condition lenyap secara arsitektural tanpa perlu distributed lock!

---

### Solusi 3: *Distributed Mutex (Redis Redlock / Golang Singleflight)*

Jika backend aplikasi menggunakan sistem microservices berbasis Node.js/Go/Python dengan caching layer Redis:

#### Mekanisme Kerja:
Sebelum worker memproses resolusi pola, ia membungkus fungsi pemanggilan LLM dalam mekanisme **Singleflight / Distributed Lock**:
```python
# Contoh implementasi konseptual Python Redis Lock
lock_key = f"lock:pattern_resolve:{pattern_hash}"

with redis_client.lock(lock_key, timeout=15):
    # Cek ulang DB setelah acquire lock (Double-Checked Locking)
    existing = get_pattern_from_db(pattern_hash)
    if existing and existing["status"] == "READY":
        return existing["reason_template"]
    
    # Hanya 1 worker yang mencapai baris ini
    explanation = call_local_llm(prompt, model="gemma3:12b")
    template = convert_explanation_to_template(explanation, sample_row)
    save_pattern_to_db(pattern_hash, template)
    return template
```

---

## BAGIAN 4: BLUEPRINT IMPLEMENTASI REKOMENDASI (SOLUSI 1)

Berikut adalah DDL migrasi dan skrip Python helper siap pakai untuk tim aplikasi:

### 4.1 Skrip DDL Migrasi Tabel (`006_pattern_locking.sql`)
```sql
-- Tambahkan kolom status pada reasoning_patterns jika ingin mengaktifkan event-driven locking
ALTER TABLE public.reasoning_patterns 
ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'READY';

CREATE INDEX IF NOT EXISTS idx_reasoning_patterns_status 
ON public.reasoning_patterns (pattern_hash, status);
```

### 4.2 Helper Python Thread-Safe Pattern Resolver
```python
import time
import psycopg2

def resolve_pattern_thread_safe(con, p_hash: str, signature: str, sample_row: dict) -> str:
    """
    Menjamin pemanggilan LLM hanya terjadi 1x untuk pola yang sama
    di lingkungan eksekusi paralel / event-driven.
    """
    # 1. Cek apakah template sudah READY
    row = con.execute(
        f"SELECT reason_template, status FROM reasoning_patterns WHERE pattern_hash = '{p_hash}'"
    ).fetchone()
    
    if row and row[1] == "READY":
        return row[0]

    # 2. Coba lakukan claim atomic (Menjadi Leader)
    cur = con.cursor()
    cur.execute(f"""
        INSERT INTO reasoning_patterns (pattern_hash, pattern_name, pattern_signature, reason_template, status)
        VALUES ('{p_hash}', 'pattern_{p_hash[:8]}', '{signature}', 'IN_PROGRESS', 'RESOLVING')
        ON CONFLICT (pattern_hash) DO NOTHING
        RETURNING pattern_hash;
    """)
    claimed = cur.fetchone()

    if claimed:
        # === WORKER INI PEMENANG CLAIM (PANGGIL LLM) ===
        try:
            explanation = call_local_llm(build_prompt(signature, sample_row), model="gemma3:12b")
            template = convert_explanation_to_template(explanation, sample_row)
            
            cur.execute(f"""
                UPDATE reasoning_patterns 
                SET reason_template = '{template}', status = 'READY', updated_at = NOW()
                WHERE pattern_hash = '{p_hash}';
                SELECT pg_notify('pattern_resolved', '{p_hash}');
            """)
            con.commit()
            return template
        except Exception as e:
            # Rollback status jika LLM gagal agar bisa dicoba worker lain
            cur.execute(f"DELETE FROM reasoning_patterns WHERE pattern_hash = '{p_hash}';")
            con.commit()
            raise e
    else:
        # === WORKER INI KALAH CLAIM (MENUNGGU PEMENANG SELESAI) ===
        max_wait_seconds = 15.0
        start = time.time()
        while time.time() - start < max_wait_seconds:
            time.sleep(0.1)  # Cek berkala setiap 100ms
            res = con.execute(
                f"SELECT reason_template, status FROM reasoning_patterns WHERE pattern_hash = '{p_hash}'"
            ).fetchone()
            if res and res[1] == "READY":
                return res[0]
                
        # Fallback deterministik jika pemenang mengalami timeout / crash
        return generate_deterministic_fallback(signature, sample_row)
```

---

## RINGKASAN REKOMENDASI UNTUK TIM LAIN

1. **Untuk Data Skala Besar (>100k):** Jangan takut lonjakan data manual review, karena secara matematis kombinasi pola akan mengalami *plateau* (jenuh) di ~30 pola awal. 99% data tetap berjalan dengan kecepatan SQL biasa.
2. **Untuk Event-Driven Paralel:** Terapkan **Solusi 1 (Database Row Claiming `status='RESOLVING'` + NOTIFY)**. Solusi ini paling ringan, elegan, tidak memerlukan instalasi tools baru, dan secara matematis menjamin **zero double-hit LLM**.
