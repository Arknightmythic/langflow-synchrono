"""
Bagian konverter yang tidak bergantung mesin basis datanya.

Dua hal tinggal di sini, dan keduanya harus sama persis untuk `.sql`, `.mdf`,
maupun `.dmp`: **kolom apa yang boleh keluar**, dan **tabel mana yang dipilih**.
Kalau tiap mesin punya jawabannya sendiri, dump yang sama akan menghasilkan
berkas yang berbeda tergantung format kirimannya — dan itu jenis perbedaan yang
tidak akan pernah ada yang menyadarinya.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getenv("LIB_SYNCHRONO", "/synchrono/lib"))

from _normalisasi import _lapis_alias, _lapis_mirip  # noqa: E402

# Enam kolom yang boleh keluar. Apa pun selain ini — trigger, prosedur, tabel
# lain, kolom tambahan — tidak ikut. Namanya sengaja sama persis dengan
# ENAM_ELEMEN di engine grading, supaya pengenalan kolom di sana selesai di
# lapis 1 (alias) dan tidak perlu menebak-nebak.
KONTRAK = ["nik", "nama", "tempat_lahir", "tanggal_lahir",
           "jenis_kelamin", "nama_ibu"]


def kenali(kolom: list[str]) -> dict:
    """
    Petakan nama kolom ke elemen, DARI NAMANYA SAJA.

    Sengaja hanya lapis 1 (alias) dan lapis 2 (kemiripan). Lapis 3-5 milik
    `petakan_kolom()` membaca isi datanya, dan untuk MEMILIH TABEL itu terlalu
    mahal: ia berarti menyampel setiap tabel di dalam dump, yang bisa puluhan.

    Yang penting, kosakatanya sama — `ALIAS` yang itu juga. Jadi ini bukan mesin
    pengenalan kedua yang bisa berbeda pendapat dengan yang di grading,
    melainkan dua lapis pertama dari mesin yang sama.
    """
    peta, _ = _lapis_alias(kolom)
    if len([k for k in kolom if k not in peta.values()]):
        baru, _ = _lapis_mirip(kolom, peta)
        peta.update(baru)
    return peta


def pilih_dari_kandidat(kandidat: dict, paksa: str | None = None
                        ) -> tuple[str, dict]:
    """
    Tabel mana yang berisi data kependudukan?

    `kandidat` = {nama_tabel: {"kolom": [...], "baris": n}}.

    Dump bisa memuat puluhan tabel, dan memilih yang keliru **tidak menimbulkan
    galat** — hasilnya cuma grade yang aneh berbulan-bulan kemudian. Kalau
    pemanggil tahu namanya, itu yang dipakai: tidak ada tebakan yang lebih baik
    daripada keterangan.

    Tanpa keterangan, skornya berapa dari enam elemen inti yang dikenali dari
    nama kolomnya. Tabel `pegawai` dengan kolom nama dan jabatan mendapat 1-2;
    tabel penduduk sungguhan mendapat 5 atau 6. Seri dimenangkan tabel dengan
    baris terbanyak, karena tabel rujukan hampir selalu kecil.
    """
    if not kandidat:
        raise RuntimeError("Berkas berhasil dibuka tapi tidak memuat satu tabel pun.")

    if paksa:
        if paksa not in kandidat:
            raise RuntimeError(
                f"Tabel '{paksa}' tidak ada. Yang ada: "
                f"{', '.join(sorted(kandidat)[:20])}")
        return paksa, {"dasar": "ditentukan pemanggil",
                       "peta": kenali(kandidat[paksa]["kolom"])}

    nilai = []
    for nama, isi in kandidat.items():
        peta = kenali(isi["kolom"])
        cocok = [e for e in KONTRAK if e in peta]
        nilai.append({"tabel": nama, "skor": len(cocok), "baris": isi["baris"],
                      "elemen": cocok, "peta": peta})

    nilai.sort(key=lambda x: (-x["skor"], -x["baris"], x["tabel"]))
    menang = nilai[0]
    if menang["skor"] < 2:
        raise RuntimeError(
            f"Tidak ada tabel yang terlihat berisi data kependudukan. Terbaik: "
            f"'{menang['tabel']}' dengan {menang['skor']} dari 6 elemen inti "
            f"dikenali. Sebutkan nama tabelnya secara eksplisit kalau pilihan "
            f"otomatis ini keliru.")

    return menang["tabel"], {
        "dasar": f"{menang['skor']}/6 elemen dikenali dari nama kolomnya",
        "elemen": menang["elemen"],
        "baris": menang["baris"],
        "peta": menang["peta"],
        "kandidat": [{"tabel": n["tabel"], "skor": n["skor"], "baris": n["baris"]}
                     for n in nilai[:5]],
    }


def sql_kontrak(peta: dict, kutip: str = '""', cast: str = "VARCHAR") -> str:
    """
    Daftar kolom SELECT yang menghasilkan enam kolom kontrak, seluruhnya teks.

    Kolom yang tidak ada di sumbernya tetap ikut sebagai NULL bertipe. Itu bukan
    kerapian: parquet yang bentuk kolomnya berubah-ubah memaksa pembaca di sisi
    grading bercabang, dan cabang seperti itu adalah tempat bug bersembunyi.
    """
    buka, tutup = kutip[0], kutip[-1]
    bagian = []
    for e in KONTRAK:
        asal = peta.get(e)
        bagian.append(f"CAST({buka}{asal}{tutup} AS {cast}) AS {e}" if asal
                      else f"CAST(NULL AS {cast}) AS {e}")
    return ", ".join(bagian)
