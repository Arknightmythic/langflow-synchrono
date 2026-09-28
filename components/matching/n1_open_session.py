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

from _shared import (BoolInput, Component, Data, IntInput, MessageTextInput,
                     Output, buka_koneksi)


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
        BoolInput(name="pakai_enriched", display_name="Pakai Enriched", value=True,
                  info="Pakai hasil grading yang kolomnya sudah ternormalisasi."),
    ]
    outputs = [Output(display_name="Session", name="session", method="buka")]

    def buka(self) -> Data:
        return self._buka(self.file_id, self.parquet_path, int(self.grade),
                          bool(self.pakai_enriched))

    @staticmethod
    def _buka(file_id: str, parquet_path: str, grade: int,
              pakai_enriched: bool = True) -> Data:
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

        # Kalau grading sudah selesai untuk berkas ini, yang dipakai adalah
        # HASILNYA, bukan parquet mentah dari muatan backend.
        #
        # Di berkas enriched, kolomnya sudah bernama baku dan tanggalnya sudah
        # satu format — dua hal yang sebelumnya harus ditebak ulang di sini dan
        # sering gagal: satu berkas uji kehilangan 19% tanggalnya karena
        # formatnya bercampur. Backend tidak perlu diubah; jalurnya dicari dari
        # `grading_jobs` berdasarkan file_id yang sudah dikirim.
        sumber, catatan = parquet_path, "parquet mentah dari muatan backend"
        if pakai_enriched:
            baris = con.execute("""
                SELECT s3_bucket, enriched_key FROM pg.public.grading_jobs
                 WHERE file_id = ? AND status = 'COMPLETED'
                   AND enriched_key IS NOT NULL
                 ORDER BY created_at DESC LIMIT 1
            """, [file_id]).fetchall()
            if baris:
                sumber = f"s3://{baris[0][0]}/{baris[0][1]}"
                catatan = "hasil grading (kolom ternormalisasi + enrichment)"
            else:
                catatan = ("belum ada hasil grading untuk file_id ini — "
                           "memakai parquet mentah dari muatan")

        print(f"[N1] sesi dibuka — file_id={file_id} grade={grade}")
        print(f"[N1] parquet: {sumber}")
        print(f"[N1] sumber : {catatan}")

        return Data(data={
            "con": con,
            "file_id": file_id,
            "parquet_path": sumber,
            "parquet_diminta": parquet_path,
            "sumber_parquet": catatan,
            "grade": grade,
        })


def jalankan(file_id, parquet_path, grade, pakai_enriched=True):
    return OpenMatchingSession._buka(file_id, parquet_path, grade, pakai_enriched)
