"""
Normalisasi & pembersihan berkas masuk.

DUA PEKERJAAN

  1. NAMA KOLOM  -> nama baku (nik, nama, tempat_lahir, tanggal_lahir,
     jenis_kelamin, nama_ibu, wilayah, provinsi, kabupaten, kecamatan,
     kelurahan, status_hidup)

  2. NILAI TANGGAL -> satu format, dd-mm-yyyy

Wilayah disimpan APA ADANYA: kalau berkas hanya punya satu kolom gabungan, ia
tetap satu kolom (bernama `wilayah`); kalau punya pecahan, tiap pecahan
dipertahankan. Yang diseragamkan namanya, bukan bentuknya.

LIMA LAPIS PENGENALAN, AI PALING AKHIR

    1  nama persis / alias          nol biaya, nol risiko
    2  nama mirip (rapidfuzz)       nol biaya
    3  tanda tangan NILAI           abaikan header, baca isinya
    4  kamus dari tabel master      99 tempat lahir, 38 provinsi, 512 kabupaten,
                                    6.887 kecamatan, 52.194 kelurahan
    5  AI                           satu panggilan per berkas

Diukur pada berkas uji yang headernya disamarkan: lapis 1 mengenali 1 dari 6
elemen, +lapis 2 jadi 3, +lapis 3 jadi 5 — semuanya benar, nol salah. Lapis 4
mengenali seluruh kolom teks bebas termasuk keempat tingkat wilayah. AI hanya
menyisakan pekerjaan yang benar-benar tidak bisa diputuskan mesin.

Tiap keputusan dicatat di `jejak`, lengkap dengan lapis mana yang memutuskan
dan atas dasar apa — supaya grade yang mengejutkan bisa ditelusuri.
"""

from __future__ import annotations

import re

from _shared import GENDER_L, GENDER_P

# ── Sasaran baku ───────────────────────────────────────────────────────────

ELEMEN_INTI = ["nik", "nama", "tempat_lahir", "tanggal_lahir",
               "jenis_kelamin", "nama_ibu"]
ELEMEN_WILAYAH = ["wilayah", "provinsi", "kabupaten", "kecamatan", "kelurahan"]
ELEMEN_LAIN = ["id", "status_hidup"]
SEMUA = ELEMEN_INTI + ELEMEN_WILAYAH + ELEMEN_LAIN

# Urutan dict ini juga urutan prioritas saat dua elemen berebut satu kolom.
ALIAS = {
    "nik": ["nik", "no_nik", "nomor_nik", "nik_ktp", "no_ktp", "nomor_ktp",
            "no_identitas", "nomor_identitas", "no_kependudukan"],
    "nama": ["nama_lengkap", "nama", "nama_penduduk", "nama_warga", "fullname",
             "full_name", "nama_wp", "nama_lengkap_wp"],
    "tempat_lahir": ["tempat_lahir", "tmp_lahir", "tempatlahir", "birth_place",
                     "tempat_kelahiran", "kota_kelahiran", "kota_lahir"],
    "tanggal_lahir": ["tanggal_lahir", "tgl_lahir", "tgl_lhr", "tanggallahir",
                      "dob", "date_of_birth", "birth_date", "tanggal_lhr"],
    "jenis_kelamin": ["jenis_kelamin", "jeniskelamin", "jk", "gender", "sex",
                      "l_p", "lp"],
    "nama_ibu": ["nama_ibu_kandung", "nama_ibu", "ibu_kandung", "nama_ibu_kdg",
                 "mother_name", "nama_ibunda"],
    # Wilayah gabungan. Disimpan apa adanya; TIDAK dipecah.
    "wilayah": ["wilayah", "daerah", "region", "alamat_wilayah"],
    "provinsi": ["provinsi", "prov", "prop", "province", "propinsi"],
    "kabupaten": ["kabupaten", "kab", "kab_kota", "kabkota", "kabupaten_kota",
                  "regency", "kota_kabupaten"],
    "kecamatan": ["kecamatan", "kec", "district"],
    "kelurahan": ["kelurahan", "kel", "desa", "desa_kelurahan",
                  "kelurahan_desa", "village", "kel_desa"],
    "status_hidup": ["status_hidup", "status_kematian", "hidup_mati",
                     "status_penduduk"],
    "id": ["id", "id_incoming", "no_urut", "nomor_urut", "row_id", "no"],
}

# Kolom master yang dipakai sebagai kamus pencocokan nilai (lapis 4).
KAMUS_MASTER = {
    "tempat_lahir": "tempat_lahir",
    "nama": "nama_lengkap",
    "nama_ibu": "nama_ibu",
    "provinsi": "provinsi",
    "kabupaten": "kabupaten",
    "kecamatan": "kecamatan",
    "kelurahan": "kelurahan",
}

# Ambang kecocokan kamus. Dinaikkan dari 0,5 karena `nama_ibu` dan `nama`
# saling tumpang tindih sampai 83% pada data uji — keduanya sama-sama nama
# orang dari sebaran yang sama, jadi selisihnyalah yang menentukan, bukan
# nilai mutlaknya.
AMBANG_KAMUS = 0.60
SELISIH_KAMUS = 0.15

# rapidfuzz: `ratio`, BUKAN `WRatio`. WRatio memberi nilai tinggi untuk
# kecocokan sebagian, sehingga "kota kelahiran" akan dikira "kota" lalu
# dipetakan ke kabupaten. `ratio` menuntut kedua string benar-benar mirip.
AMBANG_MIRIP = 85

N_SAMPEL = 300

# Kode provinsi TIDAK lagi ditulis di sini. Diambil dari tabel rujukan yang
# dimuat `_wilayah.muat()` dari S3 — satu sumber untuk seluruh sistem, dan bisa
# disunting aplikasi Synchrono tanpa menyentuh kode.

STATUS_HIDUP = {"hidup", "mati", "meninggal", "h", "m", "wafat", "almarhum"}


def _kunci(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _kutip(nama: str) -> str:
    return '"' + str(nama).replace('"', '""') + '"'


# ── Lapis 1: alias persis ──────────────────────────────────────────────────

def _lapis_alias(kolom: list[str]) -> tuple[dict, list]:
    tersedia = {_kunci(k): k for k in kolom}
    peta, jejak = {}, []
    for elemen, kandidat in ALIAS.items():
        for alias in kandidat:
            k = _kunci(alias)
            if k in tersedia and tersedia[k] not in peta.values():
                peta[elemen] = tersedia[k]
                jejak.append({"elemen": elemen, "kolom": tersedia[k],
                              "lapis": 1, "dasar": f"alias '{alias}'"})
                break
    return peta, jejak


# ── Lapis 2: nama mirip ────────────────────────────────────────────────────

def _lapis_mirip(kolom: list[str], peta: dict) -> tuple[dict, list]:
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        return {}, [{"lapis": 2, "dasar": "rapidfuzz tidak terpasang, dilewati"}]

    kosakata = [(a, e) for e, d in ALIAS.items() for a in d]
    pilihan = [_kunci(a) for a, _ in kosakata]

    baru, jejak = {}, []
    for k in kolom:
        if k in peta.values() or k in baru.values():
            continue
        cocok = process.extractOne(_kunci(k), pilihan, scorer=fuzz.ratio)
        if not cocok or cocok[1] < AMBANG_MIRIP:
            continue
        alias, elemen = kosakata[cocok[2]]
        if elemen in peta or elemen in baru:
            continue
        baru[elemen] = k
        jejak.append({"elemen": elemen, "kolom": k, "lapis": 2,
                      "dasar": f"mirip '{alias}' (skor {cocok[1]:.0f})"})
    return baru, jejak


# ── Lapis 3: tanda tangan nilai ────────────────────────────────────────────

def _tanda_tangan(nilai: list, prov_sah: set[str] | None = None) -> str | None:
    """
    Tebak elemen dari ISI kolom. Header diabaikan sepenuhnya.

    `prov_sah` kosong berarti rujukan wilayah tidak terbaca; syarat kode
    provinsi lalu DILEWATI, dan pengenalan NIK jatuh ke panjang 16 digit saja.
    Lebih longgar, tapi jauh lebih baik daripada berhenti mengenali NIK sama
    sekali hanya karena satu berkas rujukan tidak terjangkau.
    """
    isi = [str(v).strip() for v in nilai if v is not None and str(v).strip()]
    if len(isi) < 5:
        return None
    n = len(isi)

    def rasio(pola: str) -> float:
        return sum(1 for v in isi if re.fullmatch(pola, v)) / n

    # NIK: 16 digit DAN dua digit pertamanya kode provinsi yang sah. Syarat
    # kedua penting — tanpa itu, nomor rekening 16 digit ikut tertangkap.
    if rasio(r"\d{16}") > 0.8:
        if not prov_sah:
            return "nik"
        prov = {v[:2] for v in isi if re.fullmatch(r"\d{16}", v)}
        if prov and len(prov & prov_sah) / len(prov) > 0.8:
            return "nik"

    if rasio(r"\d{1,4}[-/. ]\w{1,9}[-/. ]\d{2,4}") > 0.7:
        return "tanggal_lahir"

    unik = {v.lower() for v in isi}
    if len(unik) <= 8:
        if unik <= set(GENDER_L) | set(GENDER_P):
            return "jenis_kelamin"
        if unik <= STATUS_HIDUP:
            return "status_hidup"

    return None


def _lapis_nilai(sampel: dict[str, list], peta: dict,
                 prov_sah: set[str] | None = None) -> tuple[dict, list]:
    baru, jejak = {}, []
    for kolom, nilai in sampel.items():
        if kolom in peta.values() or kolom in baru.values():
            continue
        tebak = _tanda_tangan(nilai, prov_sah)
        if tebak and tebak not in peta and tebak not in baru:
            baru[tebak] = kolom
            contoh = next((str(v) for v in nilai if v), "")
            jejak.append({"elemen": tebak, "kolom": kolom, "lapis": 3,
                          "dasar": f"bentuk nilai (contoh: {contoh[:24]})"})
    return baru, jejak


# ── Lapis 4: kamus dari tabel master ───────────────────────────────────────

def _siapkan_kamus(con) -> bool:
    """Tabel sementara berisi nilai unik tiap kolom rujukan master."""
    try:
        bagian = " UNION ALL ".join(
            f"SELECT '{elemen}' AS elemen, lower(trim({kol})) AS nilai "
            f"FROM pg.public.master WHERE {kol} IS NOT NULL"
            for elemen, kol in KAMUS_MASTER.items()
        )
        con.execute(f"CREATE OR REPLACE TEMP TABLE kamus_master AS "
                    f"SELECT DISTINCT elemen, nilai FROM ({bagian})")
        return True
    except Exception as e:  # noqa: BLE001 — master boleh saja belum terisi
        print(f"[NORM] kamus master tidak tersedia: {str(e).splitlines()[0][:90]}")
        return False


def _skor_kamus(con, sampel: dict[str, list],
                sisa: list[str]) -> dict[str, list[tuple[str, float]]]:
    """
    Skor kecocokan kamus untuk SEMUA kolom sisa, dalam satu pernyataan.

    KENAPA LEWAT ARROW, BUKAN PARAMETER

    Versi sebelumnya memasukkan 300 nilai contoh per kolom lewat `executemany`,
    lalu men-join-nya. Diukur pada berkas Dukcapil 35 kolom: 18-20 detik, dan
    join-nya sendiri hanya 3 milidetik. Sisanya ongkos menyeberangkan nilai dari
    Python ke DuckDB — sekitar 2-3 ms PER NILAI, lurus terhadap jumlahnya, dan
    tidak berubah oleh bentuk pernyataan apa pun yang dicoba (`executemany`,
    satu INSERT ber-300 parameter, satu parameter berisi list, tabel dipakai
    ulang, tanpa tabel, maupun satu pernyataan untuk seluruh kolom).

    Arrow menghapus ongkos itu: `con.register()` memperlihatkan buffer-nya apa
    adanya, tanpa menyalin nilai satu per satu. Terukur 17,9 detik -> 0,26
    detik, dengan keputusan pemetaan yang sama persis di seluruh kolom.

    Kalau pyarrow tidak ada, jalur lama dipakai — lambat, tapi tetap benar.
    """
    isi = {}
    for kolom in sisa:
        nilai = [str(v).strip().lower() for v in sampel[kolom]
                 if v is not None and str(v).strip()]
        if len(nilai) >= 5:
            isi[kolom] = nilai
    if not isi:
        return {}

    hasil: dict[str, list[tuple[str, float]]] = {}
    try:
        import pyarrow as pa
    except ImportError:
        # Jalur lama, per kolom. Dipertahankan apa adanya supaya berkas yang
        # digrading di lingkungan tanpa pyarrow tetap mendapat pemetaan yang
        # sama, hanya lebih lambat.
        print("[NORM] pyarrow tidak ada — lapis kamus memakai jalur lambat")
        for kolom, nilai in isi.items():
            con.execute("CREATE OR REPLACE TEMP TABLE _uji_nilai (v VARCHAR)")
            con.executemany("INSERT INTO _uji_nilai VALUES (?)", [(v,) for v in nilai])
            hasil[kolom] = con.execute("""
                SELECT k.elemen, count(DISTINCT u.rowid) * 1.0
                       / (SELECT count(*) FROM _uji_nilai)
                  FROM _uji_nilai u JOIN kamus_master k ON k.nilai = u.v
                 GROUP BY k.elemen ORDER BY 2 DESC
            """).fetchall()
        return hasil

    # `idx` menggantikan rowid: yang dihitung baris yang cocok, bukan nilai
    # unik. Tanpa itu kolom dengan banyak nilai berulang akan menghasilkan
    # skor yang berbeda dari jalur lama.
    kol_kolom, kol_nilai = [], []
    for kolom, nilai in isi.items():
        kol_kolom.extend([kolom] * len(nilai))
        kol_nilai.extend(nilai)

    con.register("_uji_kamus", pa.table({
        "idx": list(range(len(kol_nilai))),
        "kolom": kol_kolom,
        "nilai": kol_nilai,
    }))
    try:
        rows = con.execute("""
            WITH n AS (SELECT kolom, count(*) AS total FROM _uji_kamus GROUP BY kolom)
            SELECT u.kolom, k.elemen,
                   count(DISTINCT u.idx) * 1.0 / any_value(n.total)
              FROM _uji_kamus u
              JOIN n ON n.kolom = u.kolom
              JOIN kamus_master k ON k.nilai = u.nilai
             GROUP BY u.kolom, k.elemen
             ORDER BY 1, 3 DESC
        """).fetchall()
    finally:
        con.unregister("_uji_kamus")

    for kolom in isi:
        hasil[kolom] = []
    for kolom, elemen, skor in rows:
        hasil[kolom].append((elemen, skor))
    return hasil


def _lapis_kamus(con, sampel: dict[str, list], peta: dict) -> tuple[dict, list]:
    sisa = [k for k in sampel if k not in peta.values()]
    if not sisa or not _siapkan_kamus(con):
        return {}, []

    skor_semua = _skor_kamus(con, sampel, sisa)

    baru, jejak = {}, []
    for kolom in sisa:
        hasil = skor_semua.get(kolom)
        if not hasil:
            continue

        # Elemen yang SUDAH diambil kolom lain dikeluarkan dari perbandingan.
        # Margin harus diukur terhadap pilihan yang masih tersedia — kalau
        # tidak, kolom nama ibu tertolak hanya karena mirip kamus `nama`, yang
        # justru sudah dipegang kolom lain dan tidak mungkin jadi jawabannya.
        tersisa = [(e, s) for e, s in hasil if e not in peta and e not in baru]
        if not tersisa:
            continue

        elemen, skor = tersisa[0]
        kedua = tersisa[1][1] if len(tersisa) > 1 else 0.0

        # Selisih terhadap pesaing yang menentukan, bukan skor mutlak: nama ibu
        # cocok 100% ke kamus nama_ibu, tapi juga 88% ke nama_lengkap.
        if skor < AMBANG_KAMUS or (skor - kedua) < SELISIH_KAMUS:
            continue

        baru[elemen] = kolom
        jejak.append({"elemen": elemen, "kolom": kolom, "lapis": 4,
                      "dasar": f"cocok kamus master {skor:.0%} "
                               f"(pesaing terdekat {kedua:.0%})"})
    return baru, jejak


# ── Lapis 5: AI ────────────────────────────────────────────────────────────

def _lapis_ai(sampel: dict[str, list], peta: dict) -> tuple[dict, list]:
    import _llm

    sisa = [k for k in sampel if k not in peta.values()]
    if not sisa:
        return {}, []
    if not _llm.aktif():
        return {}, [{"lapis": 5, "dasar": f"AI tidak dipanggil: {_llm.alasan_mati()}",
                     "kolom_tersisa": sisa}]

    terpakai = sorted(peta)
    tersedia = [e for e in SEMUA if e not in peta]

    boleh_sampel, alasan_sampel = _llm.boleh_kirim_sampel()
    bagian_contoh = ""
    if boleh_sampel:
        baris = []
        for k in sisa:
            contoh = [str(v) for v in sampel[k] if v][:_llm.JUMLAH_SAMPEL]
            baris.append(f'  "{k}": {contoh}')
        bagian_contoh = "\n\nContoh nilai tiap kolom:\n" + "\n".join(baris)

    prompt = f"""Anda memetakan nama kolom berkas data kependudukan Indonesia ke
elemen baku. Jawab HANYA JSON, tanpa penjelasan apa pun.

Elemen yang MASIH tersedia: {tersedia}
Elemen yang SUDAH terpakai (jangan dipakai lagi): {terpakai}

Kolom yang belum dikenali: {sisa}{bagian_contoh}

Pakai null untuk kolom yang tidak cocok dengan elemen mana pun.
Format: {{"nama_kolom": "elemen_atau_null"}}"""

    try:
        balas = _llm.tanya(prompt)
        usul = _llm.urai_json(balas["teks"])
    except Exception as e:  # noqa: BLE001 — AI gagal tidak boleh menggagalkan grading
        return {}, [{"lapis": 5, "dasar": f"AI gagal: {type(e).__name__}: {e}",
                     "kolom_tersisa": sisa}]

    baru, jejak = {}, []
    jejak.append({
        "lapis": 5, "dasar": "AI dipanggil",
        "model": balas["model"], "endpoint": balas["endpoint"],
        "detik": balas["detik"], "token": balas["token"],
        "contoh_nilai_dikirim": boleh_sampel, "catatan_sampel": alasan_sampel,
    })

    for kolom, elemen in (usul or {}).items():
        if kolom not in sisa:
            continue  # model mengarang nama kolom
        if elemen in (None, "null", ""):
            continue
        if elemen not in SEMUA:
            jejak.append({"lapis": 5, "kolom": kolom,
                          "dasar": f"usul '{elemen}' ditolak: bukan elemen baku"})
            continue
        if elemen in peta or elemen in baru:
            jejak.append({"lapis": 5, "kolom": kolom,
                          "dasar": f"usul '{elemen}' ditolak: sudah terpakai"})
            continue
        baru[elemen] = kolom
        jejak.append({"elemen": elemen, "kolom": kolom, "lapis": 5,
                      "dasar": "usulan AI"})
    return baru, jejak


# ── Kaskade ────────────────────────────────────────────────────────────────

def ambil_sampel(con, view: str, kolom: list[str]) -> dict[str, list]:
    """Contoh nilai per kolom. Reservoir supaya tidak bias ke baris awal."""
    baris = con.execute(
        f"SELECT * FROM {view} USING SAMPLE reservoir({N_SAMPEL} ROWS) REPEATABLE (42)"
    ).fetchall()
    return {k: [b[i] for b in baris] for i, k in enumerate(kolom)}


def _prov_sah(con) -> set[str]:
    """Himpunan kode provinsi dari tabel rujukan; kosong kalau belum dimuat."""
    try:
        import _wilayah
        return _wilayah.kode_provinsi(con)
    except Exception:  # noqa: BLE001
        return set()


def petakan_kolom(con, view: str, kolom: list[str],
                  izin_ai: bool = True) -> dict:
    """Jalankan kelima lapis berurutan. Berhenti begitu semua kolom terpetakan."""
    peta, jejak = _lapis_alias(kolom)

    # SAMPELNYA DIAMBIL SEKALI, bukan sekali per lapis.
    #
    # Lapis 3, 4, dan 5 dulu memanggil `ambil_sampel` masing-masing. Karena
    # sampelnya memakai `REPEATABLE (42)` atas view yang sama, ketiganya
    # menghasilkan 300 baris yang SAMA PERSIS — yang berbeda hanya cara
    # memakainya. Jadi dua dari tiga pemanggilan itu murni terbuang.
    #
    # Terukur pada berkas 1 juta baris: 0,57 + 0,37 + 0,40 detik pada sumber
    # parquet. Pada sumber CSV jauh lebih mahal, karena tiap pemanggilan
    # berarti satu kali mengurai seluruh berkas.
    #
    # Tetap malas: berkas yang seluruh kolomnya sudah dikenali lapis 1 atau 2
    # tidak pernah mengambil sampel sama sekali.
    sampel = None

    for lapis in (2, 3, 4, 5):
        if len([k for k in kolom if k not in peta.values()]) == 0:
            break

        if lapis == 2:
            baru, j = _lapis_mirip(kolom, peta)
        else:
            if sampel is None:
                sampel = ambil_sampel(con, view, kolom)
            if lapis == 3:
                baru, j = _lapis_nilai(sampel, peta, _prov_sah(con))
            elif lapis == 4:
                baru, j = _lapis_kamus(con, sampel, peta)
            elif not izin_ai:
                baru, j = {}, [{"lapis": 5, "dasar": "AI dilarang untuk berkas ini"}]
            else:
                baru, j = _lapis_ai(sampel, peta)

        peta.update(baru)
        jejak += j

    sisa = [k for k in kolom if k not in peta.values()]
    return {"peta": peta, "jejak": jejak, "kolom_sisa": sisa}


# ── Normalisasi tanggal ────────────────────────────────────────────────────

# Nama bulan Indonesia -> Inggris, karena strptime DuckDB hanya kenal Inggris.
# Yang panjang harus lebih dulu: mengganti "jan" duluan akan merusak "januari".
BULAN_ID = [
    ("januari", "january"), ("februari", "february"), ("pebruari", "february"),
    ("maret", "march"), ("april", "april"), ("agustus", "august"),
    ("oktober", "october"), ("nopember", "november"), ("november", "november"),
    ("desember", "december"), ("juni", "june"), ("juli", "july"), ("mei", "may"),
    ("jan", "jan"), ("feb", "feb"), ("mar", "mar"), ("apr", "apr"),
    ("agt", "aug"), ("ags", "aug"), ("agu", "aug"), ("jun", "jun"),
    ("jul", "jul"), ("sep", "sep"), ("okt", "oct"), ("nov", "nov"),
    ("des", "dec"),
]

# Format dipisah menurut PANJANG TAHUN, dan pemisahan ini wajib.
#
# `%Y` di DuckDB longgar: ia menerima tahun dua digit dan membacanya harfiah,
# sehingga '13/07/58' terbaca sebagai tahun 58 Masehi. Kalau semua format
# dicoba dalam satu COALESCE, hasil keliru itu muncul lebih dulu dan format
# `%y` yang benar tidak pernah sempat dicoba — 22 ribu baris pada berkas uji
# hilang persis karena ini. Karena itu bentuk stringnya diperiksa dulu, baru
# daftar format yang sesuai yang dipakai.

# Tidak bergantung urutan hari/bulan (tahun 4 digit).
FORMAT_NETRAL = ["%Y-%m-%d", "%Y/%m/%d",
                 "%d %B %Y", "%d %b %Y", "%d-%B-%Y", "%d-%b-%Y",
                 "%d/%B/%Y", "%d/%b/%Y",
                 # Gaya Amerika bernama bulan: "Oktober 2, 1990". Tidak ambigu
                 # karena bulannya tertulis huruf, jadi aman ditaruh di sini.
                 "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"]
FORMAT_DMY = ["%d-%m-%Y", "%d/%m/%Y", "%d %m %Y", "%d.%m.%Y"]
FORMAT_MDY = ["%m-%d-%Y", "%m/%d/%Y", "%m %d %Y", "%m.%d.%Y"]

# Tahun 2 digit.
FORMAT_NETRAL_YY = ["%d %B %y", "%d %b %y", "%d-%B-%y", "%d-%b-%y",
                    "%d/%B/%y", "%d/%b/%y"]
FORMAT_DMY_YY = ["%d-%m-%y", "%d/%m/%y", "%d %m %y", "%d.%m.%y"]
FORMAT_MDY_YY = ["%m-%d-%y", "%m/%d/%y", "%m %d %y", "%m.%d.%y"]

# "Tahun ditulis dua digit" = berakhir dengan tepat dua digit yang didahului
# bukan-digit, DAN tidak diawali empat digit. Syarat kedua menjaga bentuk ISO:
# '1988-10-21' juga berakhir '21', tapi tahunnya ada di depan.
#
# Ditulis sebagai DUA pola, bukan satu dengan negative lookahead: DuckDB
# memakai RE2, dan RE2 menolak '(?!' dengan "invalid perl operator".
POLA_AKHIR_2 = r"(^|[^0-9])[0-9]{2}$"
POLA_AWAL_4 = r"^[0-9]{4}"

# Komponen jam yang menempel di belakang tanggal. Muncul tiap kali kolom
# tanggal diekspor dari basis data ('1990-10-02 00:00:00') atau dari Excel
# ('02/10/1990 00:00'). Dibuang dulu; jamnya tidak pernah dipakai.
POLA_JAM = r"[ t]+[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?([.,][0-9]+)?\s*([ap]m)?$"

# Serial Excel: jumlah hari sejak 1899-12-30. Excel keliru menganggap 1900
# tahun kabisat, dan titik awal itulah yang membetulkan kekeliruannya.
EPOCH_EXCEL = "DATE '1899-12-30'"

# Batas atas kira-kira tahun 2064 — cukup longgar untuk tanggal lahir, cukup
# ketat untuk menolak angka yang jelas bukan tanggal.
SERIAL_MAKS = 60000

POLA_NUMERIK = r"^\s*(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2,4})\s*$"


def deteksi_konvensi(con, view: str, kolom: str) -> dict:
    """
    DD-MM atau MM-DD? Diputuskan per BERKAS, dari baris yang tak terbantahkan.

    Satu nilai seperti 03-05-1991 tidak bisa dipastikan. Tapi 25-07-1985 hanya
    masuk akal sebagai DD-MM (tidak ada bulan ke-25), dan 03-18-1991 hanya
    masuk akal sebagai MM-DD. Baris-baris itulah buktinya; mayoritasnya
    menentukan bagaimana baris yang ambigu dibaca.
    """
    r = con.execute(f"""
        SELECT
            count(*) FILTER (WHERE a > 12 AND b <= 12) AS bukti_dmy,
            count(*) FILTER (WHERE b > 12 AND a <= 12) AS bukti_mdy,
            count(*) FILTER (WHERE a <= 12 AND b <= 12) AS ambigu,
            count(*)                                    AS numerik
        FROM (
            SELECT TRY_CAST(regexp_extract(v, '{POLA_NUMERIK}', 1) AS INTEGER) AS a,
                   TRY_CAST(regexp_extract(v, '{POLA_NUMERIK}', 2) AS INTEGER) AS b
              FROM (SELECT trim(CAST({_kutip(kolom)} AS VARCHAR)) AS v FROM {view})
             WHERE regexp_matches(v, '{POLA_NUMERIK}')
        )
    """).fetchone()

    dmy, mdy, ambigu, numerik = (r or (0, 0, 0, 0))
    konvensi = "MDY" if mdy > dmy else "DMY"
    return {
        "konvensi": konvensi,
        "bukti_dmy": dmy, "bukti_mdy": mdy,
        "ambigu": ambigu, "numerik": numerik,
        "dasar": (f"{dmy:,} baris hanya masuk akal sebagai DD-MM, "
                  f"{mdy:,} hanya sebagai MM-DD; {ambigu:,} baris ambigu "
                  f"dibaca sebagai {konvensi}"),
    }


def _sql_bulan_id(ekspresi: str) -> str:
    """Terjemahkan nama bulan Indonesia agar strptime bisa memakainya."""
    for idn, eng in BULAN_ID:
        ekspresi = f"regexp_replace({ekspresi}, '\\b{idn}\\b', '{eng}', 'g')"
    return ekspresi


def deteksi_serial_excel(con, view: str, kolom: str) -> bool:
    """
    Apakah kolom ini didominasi serial Excel (angka bulat, tanpa pemisah)?

    Kalau ya, serial 4 digit ikut diterima. Kalau tidak, hanya 5 digit —
    sebab di kolom tanggal biasa, `1990` hampir pasti tahun, bukan serial hari
    ke-1990 (yang berarti 13 Juni 1905).
    """
    r = con.execute(f"""
        SELECT count(*) FILTER (
                   WHERE regexp_matches(v, '^[0-9]{{4,5}}$')
                     AND TRY_CAST(v AS INTEGER) BETWEEN 1 AND {SERIAL_MAKS}),
               count(*)
          FROM (SELECT trim(CAST({_kutip(kolom)} AS VARCHAR)) AS v FROM {view})
         WHERE nullif(v, '') IS NOT NULL
    """).fetchone()
    serial, total = (r or (0, 0))
    return bool(total) and serial / total > 0.5


def sql_tanggal_normal(kolom: str, konvensi: str = "DMY",
                       ada_huruf: bool = True,
                       serial_excel: bool = False) -> str:
    """
    Ekspresi DuckDB: teks tanggal bentuk apa pun -> VARCHAR 'dd-mm-yyyy'.

    Satu ekspresi untuk seluruh kolom sekaligus, bukan per baris — 200 ribu
    baris selesai dalam satu pemindaian.
    """
    mentah = f"lower(trim(CAST({_kutip(kolom)} AS VARCHAR)))"

    # Komponen jam dibuang lebih dulu. '1990-10-02 00:00:00' tidak cocok format
    # mana pun selama jamnya masih menempel, padahal tanggalnya sempurna.
    tanpa_jam = f"trim(regexp_replace({mentah}, '{POLA_JAM}', ''))"
    bersih = _sql_bulan_id(tanpa_jam) if ada_huruf else tanpa_jam

    def rangkai(netral, utama, cadangan):
        """
        Coba format netral, lalu konvensi berkas, lalu konvensi sebaliknya.

        Urutannya yang menentukan segalanya. '03-05-1991' cocok dengan
        %d-%m-%Y maupun %m-%d-%Y, jadi yang dicoba lebih dulu itulah jawabannya
        — dan yang lebih dulu adalah konvensi mayoritas berkas. Sementara
        '03-18-1991' tidak mungkin DD-MM (tidak ada bulan ke-18), gagal di
        format utama, lalu tertangkap format cadangan sebagai 18 Maret.

        Stringnya TIDAK ditukar. Sempat ditukar lalu diparsing dengan format
        kebalikannya — dua operasi yang persis saling membatalkan, sehingga
        6.658 baris MM-DD pada berkas uji tetap tidak terbaca.
        """
        bagian = [f"try_strptime({bersih}, '{f}')"
                  for f in netral + utama + cadangan]
        return "COALESCE(\n            " + ",\n            ".join(bagian) + "\n        )"

    if konvensi == "MDY":
        empat = rangkai(FORMAT_NETRAL, FORMAT_MDY, FORMAT_DMY)
        dua = rangkai(FORMAT_NETRAL_YY, FORMAT_MDY_YY, FORMAT_DMY_YY)
    else:
        empat = rangkai(FORMAT_NETRAL, FORMAT_DMY, FORMAT_MDY)
        dua = rangkai(FORMAT_NETRAL_YY, FORMAT_DMY_YY, FORMAT_MDY_YY)

    # Serial Excel diperiksa PALING DULU: bentuknya angka bulat tanpa pemisah,
    # jadi tidak mungkin tertangkap format strptime mana pun.
    lebar = "{4,5}" if serial_excel else "{5}"
    serial = (
        f"CASE WHEN regexp_matches({bersih}, '^[0-9]{lebar}$') "
        f"      AND TRY_CAST({bersih} AS INTEGER) BETWEEN 1 AND {SERIAL_MAKS} "
        f"THEN CAST({EPOCH_EXCEL} + to_days(TRY_CAST({bersih} AS INTEGER)) "
        f"     AS TIMESTAMP) END"
    )

    ts = (f"COALESCE({serial}, "
          f"CASE WHEN regexp_matches({bersih}, '{POLA_AKHIR_2}') "
          f"      AND NOT regexp_matches({bersih}, '{POLA_AWAL_4}') "
          f"THEN {dua} ELSE {empat} END)")

    # Pivot tahun 2 digit. Bawaan DuckDB memetakan 58 -> 2058 dan 30 -> 2030;
    # untuk tanggal LAHIR keduanya mustahil. Apa pun yang jatuh di masa depan
    # dimundurkan satu abad — sekaligus menjaga 05 -> 2005 tetap apa adanya,
    # karena orang yang lahir 2005 memang ada.
    masuk_akal = (f"CASE WHEN ({ts}) > current_date "
                  f"THEN ({ts}) - INTERVAL 100 YEAR ELSE ({ts}) END")

    return (f"CASE WHEN {masuk_akal} IS NULL "
            f"       OR year({masuk_akal}) < 1900 THEN NULL "
            f"ELSE strftime({masuk_akal}, '%d-%m-%Y') END")


def ada_huruf(con, view: str, kolom: str) -> bool:
    """Apakah kolom memuat nama bulan? Kalau tidak, 25 regexp_replace dilewati."""
    return bool(con.execute(
        f"SELECT count(*) > 0 FROM {view} "
        f"WHERE regexp_matches(CAST({_kutip(kolom)} AS VARCHAR), '[A-Za-z]')"
    ).fetchone()[0])


# ── Membangun view ternormalisasi ──────────────────────────────────────────

def bangun_view(con, sumber: str, tujuan: str, peta: dict,
                kolom_asli: list[str]) -> dict:
    """
    View berisi kolom baku (nama sudah diseragamkan, tanggal sudah satu format)
    diikuti kolom lain yang tidak dikenali, apa adanya.

    Kolom yang tidak dikenali TETAP DIBAWA. Berkas instansi sering memuat
    keterangan yang tidak dipakai matching tapi berarti bagi pemiliknya.
    """
    pilih, info_tanggal = [], None

    for elemen in SEMUA:
        kol = peta.get(elemen)
        if not kol:
            continue
        if elemen == "tanggal_lahir":
            info_tanggal = deteksi_konvensi(con, sumber, kol)
            huruf = ada_huruf(con, sumber, kol)
            serial = deteksi_serial_excel(con, sumber, kol)
            info_tanggal["ada_nama_bulan"] = huruf
            info_tanggal["serial_excel"] = serial
            pilih.append(
                sql_tanggal_normal(kol, info_tanggal["konvensi"], huruf, serial)
                + f" AS {elemen}")
        else:
            pilih.append(f"{_kutip(kol)} AS {elemen}")

    terpakai = set(peta.values())
    sisa = [k for k in kolom_asli if k not in terpakai]
    pilih += [_kutip(k) for k in sisa]

    con.execute(f"CREATE OR REPLACE VIEW {tujuan} AS "
                f"SELECT {', '.join(pilih)} FROM {sumber}")

    return {
        "kolom_baku": [e for e in SEMUA if e in peta],
        "kolom_dibawa_apa_adanya": sisa,
        "tanggal": info_tanggal,
    }
