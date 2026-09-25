# Service Konfigurasi Aturan (Langflow)

Dua API untuk menu **Rule** di UI Synchrono: lihat aturan tiap grade, dan ubah
ambangnya. Berdiri sendiri dari service grading, tapi berbagi container
Langflow, PostgreSQL, dan SeaweedFS yang sama.

Perubahan **berlaku seketika** — tanpa restart, tanpa deploy. Sudah diuji:
mengubah ambang lewat API langsung mengubah hasil grading berikutnya, baik pada
proses baru maupun pada proses Langflow yang sudah lama berjalan.

---

## 1. Apa yang bisa disetel

Tiga tabel, ditampilkan sebagai satu kesatuan karena itulah yang dilihat
pengguna sebagai "aturan grade":

| Bagian | Tabel | Berlaku untuk | Bisa diedit |
|---|---|---|---|
| `criteria` | `grade_criteria` | grade A–D | **ya** |
| `score` | `grade_bands` | grade A–F | **ya** |
| `matching` | `grade_rules` | grade 1–6 | **ya** |
| bobot skor | *(di kode)* | semua | belum |
| kombinasi grade E | *(di kode)* | grade E | belum |

### Kenapa hanya A–D yang punya `criteria`

Keempatnya berbagi bentuk aturan yang **identik** — syarat kolom NIK, ambang
kelengkapan per elemen, ambang mutu NIK — sehingga muat dalam satu baris tabel.
Yang membedakannya hanya angka:

| | kolom NIK | nik | nama | tempat | tgl | JK | ibu | NIK tepercaya |
|---|:---:|---|---|---|---|---|---|---|
| **A** | wajib | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| **B** | wajib | — | 1.00 | 0.70 | 0.70 | 0.70 | 0.60 | 0.70 |
| **C** | terlarang | — | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | — |
| **D** | terlarang | — | 1.00 | 0.70 | 0.70 | 0.70 | 0.60 | — |

Grade **E** berbentuk lain: bukan ambang persentase melainkan **kombinasi kolom
yang harus ada** (nama+tgl+JK, nama+tempat+tgl, …). Grade **F** bukan aturan
sama sekali, melainkan hasil "tidak satu pun di atas terpenuhi". Keduanya tetap
di kode, dan API tetap mengembalikannya dengan `criteria: null` plus `note` yang
menjelaskan alasannya — supaya UI bisa menampilkan keenam grade dan menerangkan
kenapa dua di antaranya tidak punya tombol edit.

### Tiga nilai `nikColumn`

| Nilai | Arti |
|---|---|
| `wajib` | kolom NIK harus ada (A, B) |
| `terlarang` | kolom NIK harus **tidak** ada (C, D) |
| `abaikan` | tidak diperiksa |

`terlarang` tidak bisa diwakili angka, dan justru itulah yang memisahkan C/D
dari A/B: berkas dengan kolom NIK terisi 50% tidak boleh jatuh ke C.

Ambang kelengkapan `null` berarti **tidak diperiksa**. Nilai di atas 0 sekaligus
mensyaratkan kolomnya ada, karena kolom yang tidak ada selalu berkelengkapan 0.

---

## 2. API 3 — Baca aturan

```http
POST http://localhost:7860/api/v1/run/config-rules?stream=false
x-api-key: <API KEY>
Content-Type: application/json
```

```json
{
  "output_type": "chat", "input_type": "text", "input_value": "",
  "tweaks": { "GradingRuleGet-9c9c5": { "grade_id": "" } }
}
```

`grade_id` kosong = semua grade. Isi `"2"` untuk satu grade saja.

### Balasan

```json
{
  "grades": [{
    "gradeId": 2,
    "gradeLetter": "B",
    "criteriaEditable": true,
    "note": null,
    "criteria": {
      "order": 2,
      "active": true,
      "nikColumn": "wajib",
      "minCompleteness": {
        "nik": null, "nama": 1.0, "tempat_lahir": 0.7,
        "tanggal_lahir": 0.7, "jenis_kelamin": 0.7, "nama_ibu": 0.6
      },
      "minNikTrusted": 0.7,
      "updatedAt": "2026-09-15 17:32:59.808276+00",
      "updatedBy": "reno"
    },
    "score": {
      "min": 70, "max": 89, "severityLabel": "Baik",
      "canProceed": true, "criteriaDescription": "…"
    },
    "matching": {
      "autoMissingMax": 1, "autoScoreMin": 85.0,
      "reviewMissingCount": 2, "reviewScoreMin": 80.0, "reviewScoreMax": 85.0
    }
  }],
  "elements": ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"],
  "scoreWeights": { "kelengkapan": 0.6, "nik_tepercaya": 0.4, "editable": false, "note": "…" },
  "gradeECombinations": [["nama","tanggal_lahir","jenis_kelamin"], …]
}
```

Seperti API grading, isinya ada di
`outputs[0].outputs[0].results.message.text` dan **masih berupa string** yang
perlu di-`JSON.parse`.

---

## 3. API 4 — Ubah aturan

```http
POST http://localhost:7860/api/v1/run/config-rules-update?stream=false
```

```json
{
  "output_type": "chat", "input_type": "text", "input_value": "",
  "tweaks": { "GradingRuleUpdate-ea0f7": {
    "payload": "{\"gradeId\":2,\"updatedBy\":\"reno\",\"criteria\":{\"minCompleteness\":{\"tempat_lahir\":0.75}},\"score\":{\"min\":72}}",
    "dry_run": false
  }}
}
```

### Perubahan sebagian

Hanya field yang disebut yang berubah; sisanya dibiarkan. UI tidak perlu
mengirim ulang seluruh konfigurasi hanya untuk menggeser satu ambang, dan dua
orang yang menyunting bagian berbeda tidak saling menimpa.

### `dryRun` untuk tombol "periksa"

`"dryRun": true` menjalankan seluruh validasi dan **menunjukkan apa yang akan
berubah**, tanpa menulis apa pun:

```json
{
  "applied": false,
  "dryRun": true,
  "problems": [],
  "before": { … },
  "after":  { … },
  "changed": [
    {"section": "criteria", "field": "minCompleteness.tempat_lahir", "from": 0.7, "to": 0.75},
    {"section": "score",    "field": "min",                          "from": 70,  "to": 72}
  ]
}
```

`changed` menelusuri sampai ke elemen (`minCompleteness.tempat_lahir`), bukan
melaporkan objek `minCompleteness` utuh — jejak audit jadi menunjukkan yang
benar-benar berubah saja.

---

## 4. Validasi

Divalidasi **sebelum ditulis**: perubahan digabungkan dulu ke salinan
konfigurasi, seluruhnya diperiksa, baru disimpan. Konfigurasi yang merusak tidak
pernah sempat masuk ke basis data. Kalau ada masalah, balasannya
`"applied": false` dengan daftar `problems`, dan tidak ada yang berubah.

### Yang dijaga

**Grade yang tidak akan pernah tercapai.** Ini bahaya terbesar dan paling sulit
disadari. Kalau ambang B dibuat sama ketat dengan A, setiap berkas yang lolos B
pasti sudah lolos A lebih dulu — B mati diam-diam tanpa satu pun pesan galat.

```
Grade B tidak akan pernah tercapai: seluruh ambangnya sama ketat atau lebih
ketat dari grade A yang dievaluasi lebih dulu, sehingga berkas selalu
tertangkap di sana.
```

**Kontradiksi kolom NIK.**

```
Grade C: kolom NIK dinyatakan terlarang, tapi min_nik_trusted diisi 0.5.
Tidak ada NIK untuk dipercaya, jadi grade ini tak akan pernah cocok.
```

**Juga diperiksa:** nilai di luar 0–1, `urutan` berulang, `score_min` melebihi
`score_max`, pita skor di luar 0–100, dan pita yang tumpang tindih antar grade.

### Memeriksa konfigurasi yang sedang berlaku

```bash
cd infra && python validasi_config.py
```

Menjalankan validasi yang sama terhadap keadaan sekarang. Berguna sesudah
menyunting tabel langsung lewat SQL — jalur itu melewati API dan melewati
validasinya. Keluar dengan status 1 kalau ada masalah, jadi bisa dipakai di
skrip.

---

## 5. Pemasangan

```bash
cd infra
python apply_schema.py            # membuat & mengisi grade_criteria
python buat_flow_config.py        # membangun kedua flow
```

Keduanya idempoten. Id node **tetap sama** antar pembangunan ulang karena
diturunkan dari (endpoint, komponen), jadi aman di-hardcode di UI:

| | Endpoint | Node id |
|---|---|---|
| API 3 | `/api/v1/run/config-rules` | `GradingRuleGet-9c9c5` |
| API 4 | `/api/v1/run/config-rules-update` | `GradingRuleUpdate-ea0f7` |

---

## 6. Catatan teknis

### `double precision`, bukan `real`

Ambang disimpan 8 byte, bukan 4. Dengan `real`, angka `0.6` tersimpan sebagai
`0.6000000238` — sedikit lebih besar dari `0.6` yang dihitung Python. Berkas
dengan nama ibu terisi **tepat 60%** (mis. 120.000 dari 200.000 baris) akan
gagal ambang yang tertulis 0.6. Perubahan perilaku yang nyata, dan nyaris
mustahil dilacak.

### Menyunting lewat SQL melewati validasi

`UPDATE grade_criteria …` langsung tetap bisa. Itu disengaja — kadang perlu.
Tapi jalankan `validasi_config.py` sesudahnya.

### Nonaktifkan satu grade

`aktif = false` membuat satu baris kriteria dilewati tanpa menghapus
konfigurasinya. Berkas yang tadinya masuk grade itu akan jatuh ke grade
berikutnya yang cocok.

### Perilaku terverifikasi tidak bergeser

Tabel `grade_criteria` di-seed dengan nilai yang **persis sama** dengan yang
dulu tertanam di kode. Setelah pemindahan, kedelapan berkas uji dan keempat
belas berkas produksi menghasilkan grade dan skor yang identik.
