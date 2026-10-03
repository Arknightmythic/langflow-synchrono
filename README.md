# synchrono-config-ui

UI untuk melihat dan mengubah aturan **grading A–F** dan **matching**
synchrono-service lewat API konfigurasinya (`/api/v1/config/...`). Dipakai
untuk dua hal:

- **Uji & operasi**: melihat seluruh aturan yang berlaku, mencoba perubahan
  dengan *dry run*, menyimpannya, dan menelusuri riwayat/versi.
- **Acuan tim portal**: halaman *Panduan API* menjelaskan endpoint, kode
  status, aturan PATCH, dan arti setiap field; tombol *Lihat muatan* di setiap
  editor menampilkan muatan PATCH yang persis dikirim.

Berdiri sendiri: folder, image, dan kontainer terpisah dari service. Bekerja
dengan synchrono-service (DuckDB) maupun synchrono-service-starrocks — API
konfigurasinya sama.

## Isi

| Halaman | Isi |
|---|---|
| Ringkasan | alur penilaian (grading → Pass 1/2/3 → klasifikasi), tabel kriteria & pita skor A–F, ambang AUTO/REVIEW, matriks bobot & elemen kosong, nilai global, semua peringatan |
| Grade A–F | editor per grade: kriteria (A–D), pita skor, ambang, bobot (urutan bisa digeser), elemen kosong, pembersihan nama, cara menilai tanggal lahir, kueri blocking (baca saja), analisis skor tertinggi per jumlah elemen kosong, kalkulator satu baris |
| Global | bobot skor mutu, kombinasi grade E, selisih seri (CONFLICT), ambang nama ibu bertentangan |
| Riwayat & versi | siapa mengubah apa dan kapan; isi setiap `configVersion` dan bedanya dengan versi yang berlaku |
| Simulasi | grade & skor mutu sebuah berkas; skor & status satu baris matching; kemiripan Jaro-Winkler |
| Panduan API | rujukan untuk tim portal |

Setiap perubahan: **Periksa (dry run)** → baca masalah/peringatan → **Simpan**
(wajib mengisi nama penyunting, tercatat di riwayat). Muatan hanya berisi field
yang berubah. Sebelum menyimpan, UI memeriksa apakah konfigurasi sudah diubah
orang lain sejak halaman dimuat.

Analisis di editor dihitung di browser dengan rumus yang sama dengan service
(`web/js/analysis.js`, salinan `analisis_matching` dan validasi
`lib/_config.py`), jadi akibat sebuah angka terlihat sebelum dikirim. Hasil
*dry run* dari service tetap yang menentukan.

## Cara kerja

```
browser ──▶ nginx (kontainer ini) ──/t/<n>/api/v1/config/*──▶ service ke-n
             └─ file statis web/
```

- Hanya `/api/v1/config/` yang diteruskan; endpoint service lain tidak bisa
  dijangkau lewat UI.
- Service tidak mengirim header CORS, jadi browser selalu lewat proxy ini.
- API key bisa **diisi server** (`SERVICE_API_KEYS`) atau **diisi pengguna di
  browser** (tombol *API key*; disimpan di sessionStorage, atau localStorage
  bila dicentang).
- Bila kunci diisi server, `UI_PASSWORD` wajib (basic auth); tanpa itu
  kontainer menolak start, kecuali `UI_ALLOW_NO_LOGIN=true` untuk uji lokal.
- Alamat target: IP atau nama di `/etc/hosts` dipakai langsung; nama lain
  (mis. nama kontainer) dicari per permintaan, sehingga service yang sedang
  dimatikan tidak membuat UI ikut gagal start.

## Konfigurasi (`.env`)

| Variabel | Arti |
|---|---|
| `UI_PORT` | port di host, bawaan `7880` |
| `SERVICE_TARGETS` | satu atau lebih service, dipisah koma: `Nama=http://host:port` (nama boleh dihilangkan) |
| `SERVICE_API_KEYS` | API key per target, urutan sama; satu nilai = untuk semua; kosong = diisi di browser |
| `UI_USER`, `UI_PASSWORD` | login basic auth UI |
| `UI_ALLOW_NO_LOGIN` | `true` = izinkan kunci diisi server tanpa login (uji lokal saja) |

## Deploy di server

Di folder terpisah dari service, misalnya di 192.168.2.107:

```bash
git clone -b config-ui https://github.com/Arknightmythic/langflow-synchrono.git synchrono-config-ui
cd synchrono-config-ui
cp .env.example .env
nano .env        # SERVICE_TARGETS, SERVICE_API_KEYS, UI_PASSWORD
docker compose up -d --build
docker compose logs --tail 20 config-ui
```

Contoh `.env` untuk service lama (port 7860) dan versi StarRocks (port 7870)
yang dinyalakan bergantian:

```env
UI_PORT=7880
SERVICE_TARGETS=DuckDB=http://192.168.2.107:7860,StarRocks=http://192.168.2.107:7870
SERVICE_API_KEYS=<nilai SERVICE_API_KEY di .env service>
UI_USER=admin
UI_PASSWORD=<kata sandi>
```

Buka `http://192.168.2.107:7880`. Target yang sedang mati tampil sebagai
"Tidak terhubung" tanpa mengganggu target lain; pilih target di kanan atas.

Memperbarui: `git pull && docker compose up -d --build`. Menghentikan:
`docker compose down`.

UI ini memakai HTTP biasa. Untuk dibuka di luar jaringan internal, pasang di
belakang reverse proxy ber-TLS.

## Pengembangan

Tanpa langkah build: HTML, CSS, dan modul JavaScript biasa di `web/`.

```bash
docker compose up -d --build     # setelah mengubah web/ atau docker/
```

Bila rumus analisis atau validasi di service berubah, cek ulang salinannya di
UI (hanya GET dan PATCH `dryRun: true`, tidak ada yang ditulis):

```bash
SERVICE_URL=http://192.168.2.107:7860 API_KEY=... node tools/cek-analisis.mjs --acak 200
```

| Berkas | Isi |
|---|---|
| `docker/40-synchrono-ui.sh` | membangkitkan konfigurasi nginx, login, dan `ui-config.json` dari env |
| `web/js/api.js` | klien REST, penanganan status |
| `web/js/analysis.js` | salinan rumus analisis, validasi, simulasi grading, Jaro-Winkler |
| `web/js/fields.js` | label dan penjelasan field |
| `web/js/views/*.js` | halaman; `grade.js` membangun muatan PATCH (`buildPatch`) |
