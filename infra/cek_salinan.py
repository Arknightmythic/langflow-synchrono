"""
Apa saja yang berbeda antara salinan di repo ini dan langflow-synchrono?

    python infra/cek_salinan.py [jalur-langflow-synchrono]

Sejak 28 Sep 2026 synchrono-service BERDIRI SENDIRI: lib/, components/,
konverter/, dan migrasi/seed disalin ke repo ini, dan service tidak lagi butuh
checkout Langflow untuk jalan. Kedua repo boleh berkembang sendiri-sendiri —
tapi perbaikan bug di satu sisi biasanya perlu dibawa ke sisi lain (contoh: dump
tanpa kolom NIK yang diberi grade E, diperbaiki di konverter keduanya). Skrip ini
menunjukkan apa yang berbeda supaya pembawaan itu tidak terlupa.

Tidak ada langflow-synchrono di sebelahnya = tidak ada yang dibandingkan; itu
bukan galat. Akhir baris diabaikan. Keluar dengan kode 1 kalau ada perbedaan.
"""

from __future__ import annotations

import sys
from pathlib import Path

DISINI = Path(__file__).resolve().parent.parent
LANGFLOW = Path(sys.argv[1]) if len(sys.argv) > 1 else DISINI.parent / "langflow-synchrono"

# (pola di repo ini, folder padanannya di langflow-synchrono)
PASANGAN = [
    ("lib/*.py", "lib"),
    ("components/*/*.py", "components"),
    ("konverter/*.py", "konverter"),
    ("infra/Dockerfile.konverter*", "infra"),
    ("infra/konverter*-nyalakan.sh", "infra"),
    ("infra/konverter-*.cnf", "infra"),
    ("infra/skema/migrate.py", "infra"),
    ("infra/skema/seed.py", "infra"),
    ("infra/skema/matching_queries.json", "infra"),
    ("infra/skema/db/*/*", "infra/db"),
]


def isi(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def padanan(pola: str, folder: str) -> list[tuple[str, Path, Path]]:
    """(nama tampil, berkas di sini, berkas di langflow) — dari kedua sisi."""
    # Akar = segmen sebelum segmen pertama yang berwildcard (atau folder berkasnya).
    bagian = pola.split("/")
    i = next((k for k, s in enumerate(bagian) if "*" in s), len(bagian) - 1)
    akar_sini = "/".join(bagian[:i])
    hasil = {}
    for p in DISINI.glob(pola):
        rel = p.relative_to(DISINI / akar_sini)
        hasil[str(rel)] = (p, LANGFLOW / folder / rel)
    # Berkas yang hanya ada di Langflow, dengan pola yang sama di foldernya.
    pola_lf = pola[len(akar_sini) + 1:] if pola.startswith(akar_sini + "/") else pola
    for q in (LANGFLOW / folder).glob(pola_lf):
        rel = q.relative_to(LANGFLOW / folder)
        hasil.setdefault(str(rel), (DISINI / akar_sini / rel, q))
    return [(f"{akar_sini}/{k}".replace("\\", "/"), a, b) for k, (a, b) in sorted(hasil.items())]


def main() -> int:
    if not (LANGFLOW / "lib").is_dir():
        print(f"langflow-synchrono tidak ada di {LANGFLOW} — tidak ada yang dibandingkan.")
        return 0
    semua, beda = 0, 0
    for pola, folder in PASANGAN:
        for nama, a, b in padanan(pola, folder):
            semua += 1
            if not a.exists() or not b.exists():
                print(f"  HANYA DI {'langflow' if b.exists() else 'service '}  {nama}")
                beda += 1
            elif isi(a) != isi(b):
                print(f"  BEDA               {nama}")
                beda += 1
    print(f"\n{semua - beda}/{semua} sama"
          + ("" if not beda else " — periksa apakah perbedaannya disengaja"))
    return 1 if beda else 0


if __name__ == "__main__":
    raise SystemExit(main())
