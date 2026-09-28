"""
NODE G2 — Load Raw Parquet

sesi  ->  view `raw_df` + pengenalan kolom

Berkas TIDAK diunduh. DuckDB membacanya langsung dari SeaweedFS lewat httpfs,
jadi berkas 500 MB pun tidak pernah singgah di disk container.

Di sini juga enam elemen kependudukan dikenali dari nama kolom berkas —
`nama_lengkap` maupun `nama`, `nama_ibu_kandung` maupun `nama_ibu`, tidak peka
huruf besar-kecil. Kolom yang tidak dikenali tetap dibawa apa adanya ke berkas
enriched.
"""

from _grading import muat_raw
from _shared import Component, Data, HandleInput, Output, ambil


class LoadRawParquet(Component):
    display_name = "G2. Load Raw Parquet"
    description = "Baca parquet mentah dari SeaweedFS & kenali enam elemen."
    icon = "file-input"
    name = "LoadRawParquet"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Raw Ready", name="raw_ready", method="muat")]

    def muat(self) -> Data:
        sesi = getattr(self.session, "data", self.session)
        ambil(self.session, "con")  # gagal cepat kalau sambungannya salah
        return Data(data=muat_raw(sesi))
