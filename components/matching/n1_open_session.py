"""
NODE 1 — Open Matching Session

{file_id, parquet_path, grade}  ->  sesi (koneksi DuckDB + parameter)

Titik masuk flow. Payload dari backend Synchrono membawa SEMUA yang dibutuhkan
(kontrak b), jadi service ini tidak perlu tabel `uploaded_files` dan tidak
menuntut skema apa pun di sisi backend.

Node ini yang membuka koneksi DuckDB, memasang extension, menyambungkan
SeaweedFS dan PostgreSQL. Node berikutnya memakai ulang koneksi yang sama —
itulah sebabnya data tidak pernah berpindah antar node.
"""

from _shared import Component, Data, IntInput, MessageTextInput, Output, buka_koneksi


class OpenMatchingSession(Component):
    display_name = "1. Open Matching Session"
    description = "Buka koneksi DuckDB + sambungkan SeaweedFS & PostgreSQL."
    icon = "plug"
    name = "OpenMatchingSession"

    inputs = [
        MessageTextInput(name="file_id", display_name="File ID", required=True,
                         info="Penanda hasil, mis. 75efa4be"),
        MessageTextInput(name="parquet_path", display_name="Parquet Path", required=True,
                         info="Path S3 lengkap, mis. s3://synchrono/curated/xxx.parquet"),
        IntInput(name="grade", display_name="Grade", value=1,
                 info="1-5. Grade 6 (custom mapping) belum didukung."),
    ]
    outputs = [Output(display_name="Session", name="session", method="buka")]

    def buka(self) -> Data:
        return self._buka(self.file_id, self.parquet_path, int(self.grade))

    @staticmethod
    def _buka(file_id: str, parquet_path: str, grade: int) -> Data:
        file_id = str(file_id).strip()
        parquet_path = str(parquet_path).strip()

        if not file_id:
            raise ValueError("file_id kosong")
        if not parquet_path.startswith("s3://"):
            raise ValueError(f"parquet_path harus diawali s3:// — dapat: {parquet_path!r}")
        if grade not in (1, 2, 3, 4, 5):
            raise ValueError(
                f"Grade {grade} tidak didukung. Grade 6 butuh pairing kolom custom "
                "yang belum diimplementasikan di service ini."
            )

        con = buka_koneksi()
        print(f"[N1] sesi dibuka — file_id={file_id} grade={grade}")
        print(f"[N1] parquet: {parquet_path}")

        return Data(data={
            "con": con,
            "file_id": file_id,
            "parquet_path": parquet_path,
            "grade": grade,
        })


def jalankan(file_id, parquet_path, grade):
    return OpenMatchingSession._buka(file_id, parquet_path, grade)
