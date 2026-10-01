"""
Pembersihan nama — gelar dan bin/binti.

    "Prof. Manah Salahudin Binti Raina Nuraini, M.Kom."  ->  "manah salahudin"

KENAPA PERLU

Matching mencocokkan nama dengan Jaro-Winkler terhadap tabel master. Master
menyimpan nama polos, sedangkan berkas instansi kerap membawa gelar akademik
di depan dan belakang, serta patronimik "bin"/"binti". Ketiganya menggeser skor
kemiripan tanpa alasan yang ada hubungannya dengan identitas orangnya.

TIGA BAGIAN YANG DIBUANG, URUTANNYA PENTING

  1. gelar belakang   ", S.Pd." / ", M.Kom."   dibuang lebih dulu, karena
                      koma menandai batasnya dengan jelas
  2. gelar depan      "Dr. " / "Hj. " / "Prof. "
  3. patronimik       " Bin X" / " Binti X"    dipotong dari kata itu ke belakang

Kalau gelar depan dibuang lebih dulu, "Dr. Hj. Siti, S.Pd." menyisakan koma
menggantung yang lebih sulit dirapikan.

YANG TIDAK DIBUANG

Nama yang seluruhnya adalah gelar — mis. kolom berisi "Dr." saja — dikembalikan
apa adanya. Membersihkannya jadi string kosong berarti menghapus satu-satunya
isi yang dipunyai baris itu, dan itu lebih merugikan daripada membiarkannya.
"""

from __future__ import annotations

# Gelar depan. Ditulis tanpa titik; titik opsional ditangani regex.
# Yang panjang harus lebih dulu: "drs" sebelum "dr", kalau tidak "drs" akan
# dipotong jadi "s" oleh pola "dr".
GELAR_DEPAN = [
    "prof", "drs", "dra", "dr", "ir", "hj", "h", "kh", "tgk", "ust", "ustadz",
    "st", "sr", "ny", "tn", "mr", "mrs", "ms",
]

# Pola gelar belakang: segmen sesudah koma yang memuat titik, atau yang
# pendek dan hanya huruf. Mencakup "S.Pd.", "M.Kom.", "Ph.D", "SE", "MM".
POLA_GELAR_BELAKANG = r",\s*[a-z][a-z.]{0,14}\.?\s*$"

POLA_PATRONIMIK = r"\s+(bin|binti|ibnu|binte)\s+.*$"

# Singkatan "Muhammad" di AWAL nama: "M. Rizki", "Muh Rizki", "Moch. Rizki".
# Hanya kata PERTAMA — "Rina M." di belakang lebih mungkin inisial nama lain
# (Marlina, Maharani) daripada Muhammad. Varian ejaan lengkap ("Mohammad",
# "Muhamad") tidak disentuh: itu urusan Jaro-Winkler, bukan singkatan.
SINGKATAN_MUHAMMAD = ["mochd", "moch", "muhd", "muh", "moh", "mhd", "mch", "m"]
POLA_SINGKATAN = rf"^({'|'.join(SINGKATAN_MUHAMMAD)})\.?\s+"


def _pola_depan() -> str:
    """Satu regex untuk semua gelar depan, boleh beruntun."""
    isi = "|".join(GELAR_DEPAN)
    # ^(gelar)\.?\s+  diulang selama masih cocok
    return rf"^(({isi})\.?\s+)+"


def sql_bersih(kolom: str) -> str:
    """
    Ekspresi DuckDB: nama mentah -> nama bersih, huruf kecil.

    Satu ekspresi untuk seluruh kolom sekaligus. Tidak ada pemrosesan per baris
    di Python — berkas 200 ribu baris selesai dalam satu pemindaian.
    """
    return sql_bersih_pilih(kolom, gelar=True, patronimik=True, singkatan=False)


def sql_bersih_pilih(kolom: str, gelar: bool, patronimik: bool,
                     singkatan: bool) -> str:
    """
    Seperti `sql_bersih`, tapi tiap bagian bisa dinyalakan sendiri.

    Dipakai matching, yang pembersihannya diatur per grade lewat konfigurasi
    (`nameCleaning`). Grading tetap memakai `sql_bersih` — ketiganya menyala
    kecuali singkatan, dan ekspresinya persis sama dengan sebelum fungsi ini ada.
    """
    mentah = f"lower(trim(CAST({kolom} AS VARCHAR)))"
    x = mentah

    if gelar:
        # 1. gelar belakang, dua kali untuk "..., S.Pd., M.Kom."
        x = (f"regexp_replace(regexp_replace({x}, "
             f"'{POLA_GELAR_BELAKANG}', ''), "
             f"'{POLA_GELAR_BELAKANG}', '')")
        # 2. gelar depan
        x = f"regexp_replace({x}, '{_pola_depan()}', '')"
    if patronimik:
        # 3. patronimik
        x = f"regexp_replace({x}, '{POLA_PATRONIMIK}', '')"
    if singkatan:
        # Sesudah gelar dibuang: "H. M. Rizki" -> "m. rizki" -> "muhammad rizki".
        x = f"regexp_replace({x}, '{POLA_SINGKATAN}', 'muhammad ')"
    # 4. rapikan spasi ganda
    rapi = f"trim(regexp_replace({x}, '\\s+', ' ', 'g'))"

    # Kalau pembersihan menyisakan kosong, kembalikan nilai mentahnya.
    return f"CASE WHEN nullif({rapi}, '') IS NULL THEN {mentah} ELSE {rapi} END"


def sql_ada_gelar(kolom: str) -> str:
    """Benar kalau nama memuat gelar depan maupun belakang."""
    mentah = f"lower(trim(CAST({kolom} AS VARCHAR)))"
    return (f"(regexp_matches({mentah}, '{_pola_depan()}') "
            f" OR regexp_matches({mentah}, '{POLA_GELAR_BELAKANG}'))")


def sql_ada_patronimik(kolom: str) -> str:
    """Benar kalau nama memuat bin/binti/ibnu."""
    mentah = f"lower(trim(CAST({kolom} AS VARCHAR)))"
    return f"regexp_matches({mentah}, '{POLA_PATRONIMIK}')"
