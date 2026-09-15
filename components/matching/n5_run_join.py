"""
NODE 5 — Run Matching Join

sesi  ->  tabel `joined_df` di dalam DuckDB

Menjalankan SQL blocking milik grade yang bersangkutan. Ini bukan cross join:
tiap grade punya kunci blocking sendiri — grade 1 langsung pada `nik`, grade 3
pada kombinasi gender + 3 huruf pertama nama + hari/bulan lahir. Tanpa blocking,
200 ribu baris x 299 ribu master = 60 miliar perbandingan.

Hasilnya disimpan sebagai TABLE (bukan VIEW) supaya join hanya dieksekusi sekali,
lalu dipakai ulang oleh node scoring.
"""

import time

from _shared import TABEL_JOIN, Component, Data, HandleInput, Output, ambil


class RunMatchingJoin(Component):
    display_name = "5. Run Matching Join"
    description = "Eksekusi SQL blocking join incoming x master."
    icon = "git-merge"
    name = "RunMatchingJoin"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Session (join selesai)", name="joined", method="join")]

    def join(self) -> Data:
        return self._join(self.session)

    @staticmethod
    def _join(session) -> Data:
        con = ambil(session, "con")
        query = ambil(session, "matching_query")

        mulai = time.perf_counter()
        con.execute(f"CREATE OR REPLACE TABLE {TABEL_JOIN} AS {query}")
        durasi = time.perf_counter() - mulai

        n = con.execute(f"SELECT COUNT(*) FROM {TABEL_JOIN}").fetchone()[0]
        cocok = con.execute(
            f"SELECT COUNT(*) FROM {TABEL_JOIN} WHERE nik_master IS NOT NULL"
        ).fetchone()[0]
        print(f"[N5] joined={n:,} (punya kandidat master: {cocok:,}) "
              f"dalam {durasi:.1f} detik")

        isi = dict(getattr(session, "data", session))
        isi.update({"joined_rows": n, "join_seconds": round(durasi, 2)})
        return Data(data=isi)


def jalankan(session):
    return RunMatchingJoin._join(session)
