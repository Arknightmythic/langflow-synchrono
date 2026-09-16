"""
NODE G1 — Open Grading Session

{file_id, s3_bucket, parquet_key}  ->  sesi (koneksi DuckDB + parameter)

Titik masuk flow grading. Membuka koneksi DuckDB, memasang httpfs dan
postgres, lalu menyambungkan SeaweedFS. Node berikutnya memakai ulang koneksi
yang sama — itulah sebabnya data tidak pernah berpindah antar node.

`s3_endpoint` boleh dikosongkan; kalau diisi, ia menimpa nilai environment.
Portal berhak menunjuk SeaweedFS yang berbeda dari milik matching.
"""

from _grading import buka
from _shared import Component, Data, MessageTextInput, Output


class OpenGradingSession(Component):
    display_name = "G1. Open Grading Session"
    description = "Buka koneksi DuckDB + sambungkan SeaweedFS & PostgreSQL."
    icon = "plug"
    name = "OpenGradingSession"

    inputs = [
        MessageTextInput(name="file_id", display_name="File ID", required=True,
                         info="ID berkas di database Synchrono, mis. csv_1789441689523_iq5ws"),
        MessageTextInput(name="s3_bucket", display_name="S3 Bucket", required=True,
                         value="bucket-test", info="Bucket tempat parquet mentah berada"),
        MessageTextInput(name="parquet_key", display_name="Parquet Key", required=True,
                         info="Kunci objek parquet mentah, mis. uploads/{fileId}/data.parquet"),
        MessageTextInput(name="s3_endpoint", display_name="S3 Endpoint", required=False,
                         info="Kosongkan untuk memakai S3_ENDPOINT dari environment"),
        MessageTextInput(name="enriched_key", display_name="Enriched Key", required=False,
                         info="Kosongkan untuk memakai uploads/{fileId}/enriched.parquet"),
    ]
    outputs = [Output(display_name="Session", name="session", method="buka_sesi")]

    def buka_sesi(self) -> Data:
        return Data(data=buka({
            "file_id": self.file_id,
            "s3_bucket": self.s3_bucket,
            "parquet_key": self.parquet_key,
            "s3_endpoint": self.s3_endpoint,
            "enriched_key": self.enriched_key,
        }))
