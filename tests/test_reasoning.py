"""
Uji reasoning matching — tanpa S3, tanpa PostgreSQL, tanpa LLM sungguhan.

  1. Pipeline ASLI (muat -> Pass 1/2/3 -> gabung -> pasangan -> reasoning ->
     hasil) pada parquet lokal buatan: setiap baris sengaja mengenai satu jalur.
  2. Pola yang tidak pernah muncul di data uji 200 ribu baris (SPELLING_NAME,
     TITLE_DEGREE, SWAPPED_DOB, NIK_CONFLICT, CONFLICT beda tanggal lahir),
     plus nilai kotor yang memuat teks placeholder.
  3. `periksa` — penjaga jawaban LLM.
  4. Jalur LLM dengan server tiruan: terima/tolak, cache, anggaran, endpoint
     mati, endpoint publik — dan bukti tidak satu pun nilai baris terkirim.
  5. Galat reasoning tidak menggagalkan matching.

Menjalankan (tests/ tidak di-mount ke container, jadi lewat stdin):
    docker exec -i synchrono-langflow python - < tests/test_reasoning.py
"""
import json
import os
import sys
import tempfile
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/synchrono/lib")
sys.path.insert(0, "lib")

import duckdb  # noqa: E402

import _matching as M  # noqa: E402
import _reasoning as R  # noqa: E402
from _shared import SQL_MACRO  # noqa: E402

GAGAL = []


def cek(benar: bool, pesan: str) -> None:
    print(f"  {'ok   ' if benar else 'GAGAL'} {pesan}")
    if not benar:
        GAGAL.append(pesan)


def ada(teks: str | None, *potongan: str) -> bool:
    return teks is not None and all(p in teks for p in potongan)


# ── 1. Pipeline asli pada parquet lokal ─────────────────────────────────────

MASTER = [
    # nik, nama, tempat, tgl, jk, ibu
    ("3171010101900001", "BUDI SANTOSO", "JAKARTA", "1990-01-01", "L", "SITI AMINAH"),
    ("3171010202920002", "AHMAD SYAHRUL", "BOGOR", "1992-02-02", "L", "NURHAYATI"),
    ("3171010303930003", "DEWI LESTARI", "BANDUNG", "1993-03-03", "P", "RATNA SARI"),
    ("3171010404940004", "RINA WIJAYANTI", "SURABAYA", "1994-04-04", "P", "SRI WAHYUNI"),
    ("3171010505950005", "JOKO WIDODO", "SOLO", "1995-05-05", "L", "SUDJIATMI"),
    ("3171010606960006", "MUHAMMAD RIZKI", "MEDAN", "1996-06-06", "L", "FATIMAH"),
    ("3171010707970007", "LUWES JANUAR", "DEPOK", "1997-07-07", "L", "SUMIATI"),
    ("3171010707970008", "LUWES JANUAR", "DEPOK", "1997-07-07", "L", "SUMIATI"),
]

# id, nik, nik_trusted, nama, tgl (dd-mm-yyyy, SENGAJA beda format dari
# master), jk (bentuk panjang, SENGAJA beda dari master), ibu, tempat
INCOMING = [
    ("a", "3171010101900001", True, "BUDI SANTOSO", "01-01-1990", "LAKI-LAKI",
     "SITI AMINAH", "JAKARTA"),
    ("b", "9999999999999999", True, "AHMAD SYAHRUL", "02-02-1992", "LAKI-LAKI",
     "NURHAYATI", ""),
    ("c", "3171010303930003", True, "RINA WIJAYANTI", "04-04-1994", "PEREMPUAN",
     "SRI WAHYUNI", "SURABAYA"),
    ("d", "3171010505950005", True, "SITI RAHAYU", "05-05-1995", "PEREMPUAN",
     "SUDJIATMI", "SOLO"),
    ("e", "3171010606960006", True, "MUHAMAD RIZKI", "06-06-1996", "LAKI-LAKI",
     "FATIMAH", "MEDAN"),
    ("f", "", False, "LUWES JANUAR", "07-07-1997", "LAKI-LAKI", "SUMIATI", "DEPOK"),
    ("g", "1234", False, "TANPA PASANGAN", "08-08-1998", "LAKI-LAKI", "ENTAH", "MANA"),
]

# Query grade 1 dari matching_queries.json (versi berpenjaga nik_trusted).
_KUERI = next(p for p in ("/synchrono/infra/matching_queries.json",
                          "infra/matching_queries.json") if os.path.exists(p))
KUERI_G1 = next(r["matching_query"] for r in json.load(open(
    _KUERI, encoding="utf-8"))["matching_queries"] if r["grade_code"] == 1)
ATURAN_G1 = {"auto_missing_max": 99, "auto_score_min": 80.001,
             "review_missing_count": 99, "review_score_min": 0.0,
             "review_score_max": 80.001}
JOB = {"job_id": "uji-reasoning", "file_id": "berkas-uji", "master_file_id": "master-uji",
       "actor": "uji@lokal"}


def tulis_parquet(con, folder: str) -> tuple[str, str]:
    master = os.path.join(folder, "master.parquet")
    incoming = os.path.join(folder, "incoming.parquet")
    con.execute("""CREATE TABLE m_src (nik VARCHAR, nama_lengkap VARCHAR, tempat_lahir VARCHAR,
                   tanggal_lahir DATE, jenis_kelamin VARCHAR, nama_ibu VARCHAR)""")
    con.executemany("INSERT INTO m_src VALUES (?, ?, ?, ?, ?, ?)", MASTER)
    con.execute(f"""COPY (SELECT *, 'HIDUP' AS status_kematian, 'DKI' AS provinsi,
                           NULL AS kabupaten, NULL AS kecamatan, NULL AS kelurahan
                          FROM m_src) TO '{master}' (FORMAT parquet)""")
    con.execute("""CREATE TABLE i_src (id VARCHAR, nik VARCHAR, nik_trusted BOOLEAN,
                   nama_lengkap VARCHAR, tanggal_lahir VARCHAR, jenis_kelamin VARCHAR,
                   nama_ibu_kandung VARCHAR, tempat_lahir VARCHAR)""")
    con.executemany("INSERT INTO i_src VALUES (?, ?, ?, ?, ?, ?, ?, ?)", INCOMING)
    con.execute(f"COPY i_src TO '{incoming}' (FORMAT parquet)")
    return incoming, master


def uji_pipeline(con, folder: str) -> None:
    print("\n1. Pipeline asli pada parquet lokal")
    incoming, master = tulis_parquet(con, folder)
    M.muat_masukan(con, incoming, master)
    M.pass1(con)
    M.pass2(con)
    M.pass3(con, 1, ATURAN_G1, KUERI_G1)
    M.gabung(con)
    M.susun_pasangan(con, master)
    M._isi_reasoning(con, JOB)
    M.susun_hasil(con, JOB)

    h = {r[0]: r[1:] for r in con.execute("""
        SELECT id_incoming, status, method, pattern_group, master_nik, rank_conflict,
               score, reasoning, master_snapshot FROM hasil""").fetchall()}
    cek(len(h) == len(INCOMING), f"satu baris hasil per baris incoming ({len(h)})")

    st, mt, pola, nik, rk, skor, alasan, snap = h["a"]
    cek((st, mt) == ("AUTO", "PASS1_NIK_NAMA"), "a: AUTO Pass 1")
    cek(ada(alasan, "Pass 1", "NIK (3171010101900001)",
            "Tanggal lahir, jenis kelamin, nama ibu kandung, dan tempat lahir juga identik"),
        "a: '01-01-1990' = 1990-01-01 dan 'LAKI-LAKI' = 'L' divonis SAMA")

    st, mt, pola, nik, rk, skor, alasan, snap = h["b"]
    cek((st, mt) == ("AUTO", "PASS2_NAMA_TGL_IBU"), "b: AUTO Pass 2")
    cek(ada(alasan, "NIK berkas (9999999999999999) tidak terdaftar di master",
            "Tempat lahir kosong pada data incoming"), "b: NIK tak terdaftar + tempat kosong")

    st, mt, pola, nik, rk, skor, alasan, snap = h["c"]
    cek((st, mt, pola) == ("REVIEW", "PASS2_NAMA_TGL_IBU", "NIK_CONFLICT"),
        "c: REVIEW NIK_CONFLICT (aturan pengaman 1)")
    cek(ada(alasan, "Peringatan:", "NIK berkas (3171010303930003) terdaftar di master "
                                   "atas nama orang lain", "verifikasi fisik"),
        "c: kalimat peringatan NIK milik orang lain")

    st, mt, pola, nik, rk, skor, alasan, snap = h["d"]
    cek(st == "UNMATCH" and nik is None and snap is None and skor > 0,
        f"d: UNMATCH tanpa master_nik/snapshot, skor tetap disimpan ({skor})")
    cek(ada(alasan, "kandidat terdekat memperoleh skor", "NIK berkas (3171010505950005) "
                                                         "terdaftar di master, tetapi"),
        "d: skor kandidat terdekat + NIK terdaftar tapi identitas lain tak cocok")

    st, mt, pola, nik, rk, skor, alasan, snap = h["e"]
    cek((st, mt) == ("AUTO", "SCORING"), f"e: AUTO Pass 3 ({skor})")
    cek(ada(alasan, "Pass 3", "Nama lengkap berbeda ('MUHAMAD RIZKI' vs 'MUHAMMAD RIZKI', "
                              "Jaro-Winkler"), "e: beda ejaan nama beserta Jaro-Winkler")

    st, mt, pola, nik, rk, skor, alasan, snap = h["f"]
    cek((st, mt, rk) == ("CONFLICT", "PASS2_NAMA_TGL_IBU", True), "f: CONFLICT Pass 2")
    cek(ada(alasan, "Ditemukan 2 kandidat master", "Kandidat 1 NIK 3171010707970007 "
            "(LUWES JANUAR) dan Kandidat 2 NIK 3171010707970008 (LUWES JANUAR)"),
        "f: kedua kandidat disebut dengan NIK dan nama")

    st, mt, pola, nik, rk, skor, alasan, snap = h["g"]
    cek(st == "UNMATCH" and not rk, "g: UNMATCH, rank_conflict FALSE")
    cek(ada(alasan, "tidak ada kandidat yang lolos penyaringan awal",
            "NIK berkas (1234) ditandai tidak tepercaya saat grading"),
        "g: tanpa kandidat + NIK tak tepercaya")


# ── 2. Pola yang tidak muncul di data uji ───────────────────────────────────

def isi_pasangan(con, **kolom) -> None:
    b = dict(
        master_nik="3171010101900001", skor=90.0, status="REVIEW", method="SCORING",
        rank_conflict=False, n_kandidat=1, nik_2=None, skor_2=None,
        pattern_group="GENERAL_REVIEW", nik_di_master=True,
        i_nik="3171010101900001", i_nik_trusted=True, i_nama="BUDI SANTOSO",
        i_tgl_mentah="01-01-1990", i_tgl=date(1990, 1, 1), i_jk="LAKI-LAKI",
        i_ibu="SITI AMINAH", i_tmp="JAKARTA", i_provinsi=None,
        m_nik="3171010101900001", m_nama="BUDI SANTOSO", m_tgl_mentah=date(1990, 1, 1),
        m_tgl=date(1990, 1, 1), m_jk="L", m_ibu="SITI AMINAH", m_tmp="JAKARTA",
        m_provinsi=None, k2_nama=None, k2_tgl=None)
    b.update(kolom)
    for sisi in ("i", "m"):
        for f in ("nama", "ibu", "tmp"):
            mentah = b[f"{sisi}_{f}"]
            b[f"{sisi}_{f}_clean"] = mentah.strip().lower() if mentah else None
    kol = list(b)
    con.execute(f"INSERT INTO pasangan ({', '.join(kol)}) VALUES "
                f"({', '.join(['?'] * len(kol))})", [b[k] for k in kol])


def uji_pola(con) -> None:
    print("\n2. Pola yang tidak muncul di data uji")
    # Struktur diambil dari tabel `pasangan` yang dibangun pipeline asli di
    # atas, jadi uji ini ikut gagal kalau kolomnya berubah.
    con.execute("ALTER TABLE pasangan RENAME TO pasangan_pipeline")
    con.execute("CREATE TABLE pasangan AS SELECT * FROM pasangan_pipeline WHERE FALSE")
    isi_pasangan(con, id="r1", pattern_group="SPELLING_NAME", skor=88.5,
                 i_nama="ACHMAD SYAHRUL", m_nama="AHMAD SYAHRUL")
    isi_pasangan(con, id="r2", pattern_group="TITLE_DEGREE", skor=81.2,
                 i_nama="DRS. H. BAMBANG UTOMO, M.SI", m_nama="BAMBANG UTOMO")
    isi_pasangan(con, id="r3", pattern_group="SWAPPED_DOB", skor=83.0,
                 i_tgl_mentah="05-02-1990", i_tgl=date(1990, 5, 2),
                 m_tgl_mentah=date(1990, 2, 5), m_tgl=date(1990, 2, 5))
    isi_pasangan(con, id="r4", pattern_group="NIK_CONFLICT", skor=40.0,
                 i_nama="SITI AISYAH", m_nama="BUDI SANTOSO")
    isi_pasangan(con, id="r5", status="CONFLICT", pattern_group="GENERAL_REVIEW",
                 rank_conflict=True, n_kandidat=5, nik_2="3171020202900002", skor_2=89.2,
                 skor=89.2, k2_nama="BUDI SUSANTO", k2_tgl=date(1991, 1, 1))
    # Nilai kotor yang memuat teks placeholder: harus tercetak apa adanya,
    # tidak ikut diganti.
    isi_pasangan(con, id="r6", status="AUTO", pattern_group=None, skor=95.0,
                 i_nama="BUDI {skor} SANTOSO")
    isi_pasangan(con, id="r7", pattern_group="GENERAL_REVIEW", skor=85.0,
                 i_tgl_mentah="31-02-1990", i_tgl=None)
    isi_pasangan(con, id="r8", status="AUTO", pattern_group=None, skor=97.7,
                 i_nama="SARI HARDIANSYAH, M.FARM", m_nama="SARI HARDIANSYAH")
    R.isi(con, JOB)
    a = dict(con.execute("SELECT id, reasoning FROM alasan").fetchall())

    cek(ada(a["r1"], "Skor kemiripan 88.5%", "perbedaan ejaan nama ('ACHMAD SYAHRUL' vs "
            "'AHMAD SYAHRUL', Jaro-Winkler"), "SPELLING_NAME")
    cek(ada(a["r2"], "hanya berbeda pada gelar", "('DRS. H. BAMBANG UTOMO, M.SI' vs "
            "'BAMBANG UTOMO')"), "TITLE_DEGREE")
    cek(ada(a["r3"], "tertukar (1990-05-02 pada data incoming vs 1990-02-05 pada master)"),
        "SWAPPED_DOB")
    cek(ada(a["r4"], "Peringatan: NIK cocok dengan master (3171010101900001)",
            "('SITI AISYAH') berbeda total", "('BUDI SANTOSO', Jaro-Winkler"),
        "NIK_CONFLICT (NIK sama, nama berbeda total)")
    cek(ada(a["r5"], "Dua kandidat teratas memiliki skor seimbang", "(BUDI SANTOSO, 89.2%)",
            "(BUDI SUSANTO, 89.2%)", "tanggal lahir berbeda (1990-01-01 vs 1991-01-01)"),
        "CONFLICT skor: kedua kandidat + tanggal lahir berbeda")
    cek("5 kandidat" not in a["r5"],
        "CONFLICT skor tidak menyebut n_kandidat (itu jumlah hasil blocking, bukan yang seri)")
    cek(ada(a["r6"], "'BUDI {skor} SANTOSO'", "skor kemiripan 95.0%"),
        "nilai berisi '{skor}' tercetak apa adanya, placeholder asli tetap terisi")
    cek(ada(a["r7"], "Tanggal lahir pada data incoming ('31-02-1990') tidak dapat dibaca"),
        "tanggal tak terbaca: disebut beserta nilai mentahnya, bukan 'kosong'")
    cek(ada(a["r8"], "hanya berbeda pada gelar akademis/keagamaan atau bin/binti",
            "('SARI HARDIANSYAH, M.FARM' vs 'SARI HARDIANSYAH')"),
        "AUTO dengan nama beda gelar saja")

    # Setiap placeholder yang mungkin dipakai templat punya nilai.
    tak_dikenal = {p for t in [R.templat(t) for (t,) in
                               con.execute("SELECT DISTINCT tanda FROM vonis").fetchall()]
                   for p in R.PLACEHOLDER.findall(t)} - set(R.NILAI)
    cek(not tak_dikenal, f"semua placeholder templat punya nilai {tak_dikenal or ''}")


# ── 3. Penjaga jawaban LLM ──────────────────────────────────────────────────

def uji_periksa() -> None:
    print("\n3. Penjaga jawaban LLM")
    dasar = ("Skor kemiripan {skor}%. Terdapat perbedaan ejaan nama ('{incoming.nama}' vs "
             "'{master.nama}', Jaro-Winkler {jw_nama}%). Tanggal lahir identik.")
    kasus = [
        ("parafrase sah", dasar.replace("Terdapat perbedaan ejaan nama",
                                        "Ada perbedaan ejaan pada nama"), True),
        ("dibungkus kutip dan pagar kode", "```text\n\"" + dasar + "\"\n```", True),
        ("placeholder hilang", dasar.replace("'{master.nama}'", "master"), False),
        ("placeholder berlipat", dasar + " {skor}", False),
        ("placeholder diterjemahkan", dasar.replace("{incoming.nama}", "{incoming.name}"), False),
        ("angka baru", dasar.replace("identik.", "identik (selisih 0 hari)."), False),
        ("'identik' jadi 'berbeda'", dasar.replace("identik", "berbeda"), False),
        ("'tidak' disisipkan", dasar.replace("identik", "tidak identik"), False),
        ("markdown", "**" + dasar + "**", False),
        ("terlalu panjang", dasar + " " + "Mohon diperiksa dengan saksama. " * 6, False),
        ("kosong", "   ", False),
    ]
    for nama, jawab, harap in kasus:
        hasil, alasan = R.periksa(dasar, jawab)
        cek((hasil is not None) == harap, f"{nama}: {'diterima' if hasil else alasan}")

    # Akar kata "beda": parafrase gemma4:31b sungguhan yang dulu ditolak keliru.
    dasar2 = ("Tempat lahir berbeda ('{incoming.tempat_lahir}' vs "
              "'{master.tempat_lahir}'). Nama lengkap identik.")
    kasus2 = [
        ("'berbeda' ditulis 'perbedaan'", "Terdapat perbedaan tempat lahir "
         "('{incoming.tempat_lahir}' vs '{master.tempat_lahir}'). Nama lengkap identik.", True),
        ("'berbeda' dihapus", "Tempat lahir ('{incoming.tempat_lahir}' vs "
         "'{master.tempat_lahir}') tercatat. Nama lengkap identik.", False),
    ]
    for nama, jawab, harap in kasus2:
        hasil, alasan = R.periksa(dasar2, jawab)
        cek((hasil is not None) == harap, f"{nama}: {'diterima' if hasil else alasan}")


# ── 4. Jalur LLM dengan server tiruan ───────────────────────────────────────

DITERIMA_SERVER: list[str] = []


def jawaban_tiruan(teks: str) -> str:
    if "Pass 1" in teks:
        return '"' + teks.replace("Cocok otomatis melalui", "Dicocokkan otomatis lewat") + '"'
    if "ejaan" in teks:
        return teks.replace("Terdapat perbedaan ejaan nama", "Ada perbedaan ejaan pada nama")
    if "gelar" in teks:
        return teks.replace("'{incoming.nama}'", "nama incoming")      # placeholder hilang
    if "tertukar" in teks:
        return teks.replace("tertukar", "tertukar (selisih 3 bulan)")   # angka baru
    if "Peringatan" in teks:
        return teks.replace("berbeda total", "identik")                 # makna terbalik
    if "kandidat teratas" in teks:
        return "**" + teks + "**"                                        # markdown
    return teks


class ServerTiruan(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        badan = self.rfile.read(int(self.headers["Content-Length"])).decode()
        DITERIMA_SERVER.append(badan)
        teks = json.loads(badan)["messages"][1]["content"].split("Teks:\n", 1)[1]
        keluar = json.dumps({"choices": [{"message": {"content": jawaban_tiruan(teks)}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(keluar.encode())))
        self.end_headers()
        self.wfile.write(keluar.encode())

    def log_message(self, *a):
        pass


def uji_llm(con) -> None:
    print("\n4. Jalur LLM (server tiruan)")
    con.execute("ATTACH ':memory:' AS pg")
    con.execute("""CREATE TABLE pg.reasoning_patterns (
        pattern_hash VARCHAR PRIMARY KEY, pattern_name VARCHAR NOT NULL,
        pattern_signature VARCHAR NOT NULL, reason_template VARCHAR NOT NULL,
        sample_id VARCHAR, hit_count INTEGER DEFAULT 1,
        created_at TIMESTAMPTZ DEFAULT current_timestamp,
        updated_at TIMESTAMPTZ DEFAULT current_timestamp)""")
    server = HTTPServer(("127.0.0.1", 0), ServerTiruan)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    asal = (R.BASE_URL, R.RETRIES, R.TIMEOUT, R.MAKS_POLA)
    R.BASE_URL, R.RETRIES, R.TIMEOUT = f"http://127.0.0.1:{server.server_port}", 1, 5
    try:
        # Pipeline + pola buatan digabung: semua jenis kalimat sekaligus.
        con.execute("INSERT INTO pasangan SELECT * FROM pasangan_pipeline")
        info = R.isi(con, JOB)
        n_pola = info["pola"]
        cek(info.get("llm_dipanggil") == n_pola, f"pola baru ke LLM: {info}")
        # Ditolak tepat 5: dua pola bergelar (placeholder hilang), tertukar
        # (angka baru), peringatan NIK (makna terbalik), konflik (markdown).
        cek(info.get("llm_diterima", 0) >= 3 and info.get("llm_ditolak", 0) == 5,
            "diterima >= 3, ditolak tepat 5 (placeholder x2, angka, makna, markdown)")

        a = dict(con.execute("SELECT id, reasoning FROM alasan").fetchall())
        cek(ada(a["a"], "Dicocokkan otomatis lewat pencocokan deterministik Pass 1")
            and not a["a"].startswith('"'), "jawaban diterima dipakai, kutip pembungkus dilepas")
        cek(ada(a["r1"], "Ada perbedaan ejaan pada nama ('ACHMAD SYAHRUL'"),
            "parafrase sah terhidrasi dengan nilai baris")
        cek(ada(a["r2"], "('DRS. H. BAMBANG UTOMO, M.SI' vs 'BAMBANG UTOMO')"),
            "jawaban ditolak -> kalimat deterministik")

        # Privasi: tidak satu pun nilai baris sampai ke LLM.
        nilai = set()
        for kol in ("i_nik", "m_nik", "i_nama", "m_nama", "i_ibu", "m_ibu", "i_tmp", "m_tmp",
                    "nik_2", "k2_nama"):
            nilai |= {v for (v,) in con.execute(
                f"SELECT DISTINCT CAST({kol} AS VARCHAR) FROM pasangan").fetchall()
                if v and len(v) >= 4}
        nilai |= {v for (v,) in con.execute(
            "SELECT DISTINCT strftime(i_tgl, '%Y-%m-%d') FROM pasangan").fetchall() if v}
        bocor = sorted(v for v in nilai if any(v in b for b in DITERIMA_SERVER))
        cek(not bocor and len(nilai) > 20,
            f"{len(nilai)} nilai baris diperiksa, yang terkirim ke LLM: {bocor or 'tidak ada'}")

        simpan = con.execute("""SELECT count(*), count(*) FILTER (WHERE pattern_name LIKE 'llm:%'),
                                count(*) FILTER (WHERE pattern_name LIKE 'llm-ditolak:%')
                                FROM pg.reasoning_patterns""").fetchone()
        cek(simpan == (n_pola, n_pola - 5, 5), f"cache: {simpan} (total, llm, llm-ditolak)")

        sebelum = len(DITERIMA_SERVER)
        info2 = R.isi(con, JOB)
        cek(len(DITERIMA_SERVER) == sebelum and info2.get("cache") == n_pola,
            f"job kedua: semua dari cache, LLM tidak dipanggil ({info2})")
        hit = con.execute("SELECT min(hit_count), max(hit_count), sum(hit_count) "
                          "FROM pg.reasoning_patterns").fetchone()
        cek(hit[2] == 2 * con.execute("SELECT count(*) FROM pasangan").fetchone()[0],
            f"hit_count = baris x 2 job ({hit})")

        con.execute("DELETE FROM pg.reasoning_patterns")
        R.MAKS_POLA = 2
        info3 = R.isi(con, JOB)
        cek(info3.get("llm_dipanggil") == 2 and info3.get("terlewat") == n_pola - 2,
            f"anggaran 2 pola per job ({info3})")
        R.MAKS_POLA = asal[3]

        con.execute("DELETE FROM pg.reasoning_patterns")
        R.BASE_URL = "http://127.0.0.1:9"      # port discard: koneksi ditolak
        info4 = R.isi(con, JOB)
        n_cache = con.execute("SELECT count(*) FROM pg.reasoning_patterns").fetchone()[0]
        cek(info4.get("llm_gagal") == 1 and info4.get("terlewat") == n_pola - 1
            and n_cache == 0, f"endpoint mati: berhenti setelah 1 gagal, tak disimpan ({info4})")

        R.BASE_URL = "https://api.openai.com/v1"
        sebelum = len(DITERIMA_SERVER)
        info5 = R.isi(con, JOB)
        cek(info5.get("endpoint_ditolak") == 1 and "llm_dipanggil" not in info5,
            f"endpoint publik ditolak ({info5})")

        # Dengan izin eksplisit, endpoint luar dipakai. Server tiruannya lokal,
        # jadi pemeriksa endpoint dipaksa menganggapnya "luar".
        R.BASE_URL = f"http://127.0.0.1:{server.server_port}"
        con.execute("DELETE FROM pg.reasoning_patterns")
        aman_asli, R.endpoint_aman, R.IZIN_LUAR = R.endpoint_aman, (lambda u: False), True
        try:
            info6 = R.isi(con, JOB)
        finally:
            R.endpoint_aman, R.IZIN_LUAR = aman_asli, False
        cek(info6.get("llm_dipanggil") == n_pola and "llm_ms" in info6,
            f"endpoint luar dengan REASONING_AI_ALLOW_EXTERNAL=1 dipanggil ({info6})")
        cek(R.endpoint_aman("http://ollama:11434") and R.endpoint_aman("http://172.16.12.98:11434")
            and not R.endpoint_aman("https://ollama.com"),
            "nama layanan Docker & IP privat boleh, ollama.com (hosted) tidak")
    finally:
        R.BASE_URL, R.RETRIES, R.TIMEOUT, R.MAKS_POLA = asal
        server.shutdown()


# ── 5. Galat reasoning tidak menggagalkan matching ──────────────────────────

def uji_isolasi(con) -> None:
    print("\n5. Galat reasoning tidak menggagalkan matching")
    con.execute("DROP TABLE pasangan")
    con.execute("ALTER TABLE pasangan_pipeline RENAME TO pasangan")
    asli = R.isi
    R.isi = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("galat buatan"))
    try:
        M._isi_reasoning(con, JOB)
    finally:
        R.isi = asli
    M.susun_hasil(con, JOB)
    n, n_isi = con.execute("SELECT count(*), count(reasoning) FROM hasil").fetchone()
    cek(n == len(INCOMING) and n_isi == 0,
        f"hasil tetap {n} baris, reasoning kosong ({n_isi} terisi)")


def main() -> int:
    # Uji TIDAK boleh mewarisi setelan LLM container. Pernah terjadi: container
    # berjalan dengan REASONING_AI_ALLOW_EXTERNAL=1 dan kunci ollama.com, uji
    # "endpoint publik ditolak" tidak menolak, dan kunci itu ikut terkirim ke
    # api.openai.com. Semua setelan dinetralkan di sini; kunci palsu.
    R.BASE_URL, R.IZIN_LUAR, R.API_KEY, R.MODEL = "", False, "kunci-uji-palsu", "model-uji"
    con = duckdb.connect()
    con.execute(SQL_MACRO)
    with tempfile.TemporaryDirectory() as folder:
        uji_pipeline(con, folder)
        uji_pola(con)
        uji_periksa()
        uji_llm(con)
        uji_isolasi(con)
    print(f"\n{'SEMUA LULUS' if not GAGAL else f'{len(GAGAL)} GAGAL'}")
    return 1 if GAGAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
