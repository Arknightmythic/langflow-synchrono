"""
Apakah salinan konverter di sini SAMA dengan milik langflow-synchrono?

    python infra/cek_konverter.py [jalur-langflow-synchrono]

Konverter jalur B hidup di DUA branch — keputusan 28 Sep 2026: Langflow dan
service masing-masing membangunnya sendiri. Dua salinan yang dibiarkan
menyimpang berarti dump yang sama bisa lolos di satu dan gagal di yang lain,
atau celah keamanan yang ditutup di satu tetap terbuka di yang lain. Skrip ini
menangkapnya sebelum deploy.

Akhir baris diabaikan (CRLF vs LF bukan perbedaan). Keluar dengan kode 1 kalau
ada yang berbeda — bisa dipakai di skrip deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

DISINI = Path(__file__).resolve().parent.parent
LANGFLOW = Path(sys.argv[1]) if len(sys.argv) > 1 else DISINI.parent / "langflow-synchrono"

POLA = ["konverter/*.py", "infra/Dockerfile.konverter*",
        "infra/konverter*-nyalakan.sh", "infra/konverter-*.cnf"]


def isi(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def main() -> int:
    if not (LANGFLOW / "konverter").is_dir():
        print(f"langflow-synchrono tidak ditemukan di {LANGFLOW}")
        return 2
    nama = sorted({str(p.relative_to(akar)).replace("\\", "/")
                   for akar in (DISINI, LANGFLOW) for pola in POLA
                   for p in akar.glob(pola)})
    beda = 0
    for n in nama:
        a, b = DISINI / n, LANGFLOW / n
        if not a.exists() or not b.exists():
            print(f"  HANYA DI {'langflow' if b.exists() else 'service '}  {n}")
            beda += 1
        elif isi(a) != isi(b):
            print(f"  BEDA               {n}")
            beda += 1
        else:
            print(f"  sama               {n}")
    print(f"\n{len(nama) - beda}/{len(nama)} sama"
          + ("" if not beda else " — samakan dulu sebelum deploy"))
    return 1 if beda else 0


if __name__ == "__main__":
    raise SystemExit(main())
