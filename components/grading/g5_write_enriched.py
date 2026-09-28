"""
NODE G5 — Write Enriched Parquet

anomali_df  ->  s3://{bucket}/uploads/{fileId}/enriched.parquet

Berkas hasil memuat SELURUH kolom asli ditambah delapan kolom derivasi:
nik_clean, nik_prov, nik_hari, nik_bulan, nik_tahun, nik_trusted, is_anomaly,
anomaly_notes.

Node ini WAJIB selesai sebelum callback dikirim. Portal Synchrono memverifikasi
keberadaan berkas lewat HeadObject dan membalas 400 kalau belum ada.
"""

from _grading import tulis_enriched
from _shared import Component, Data, HandleInput, Output


class WriteEnrichedParquet(Component):
    display_name = "G5. Write Enriched Parquet"
    description = "Tulis parquet enriched ke SeaweedFS."
    icon = "upload"
    name = "WriteEnrichedParquet"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Written", name="written", method="tulis")]

    def tulis(self) -> Data:
        sesi = getattr(self.session, "data", self.session)
        return Data(data=tulis_enriched(sesi))
