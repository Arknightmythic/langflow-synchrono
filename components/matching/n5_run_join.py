"""
NODE 5 — Run Matching Join

sesi  ->  tabel `joined_df` di dalam DuckDB

Menjalankan SQL blocking milik grade yang bersangkutan. Ini bukan cross join:
tiap grade punya kunci blocking sendiri — grade 1 langsung pada `nik`, grade 3
pada kombinasi gender + 3 huruf pertama nama + hari/bulan lahir. Tanpa blocking,
200 ribu baris x 299 ribu master = 60 miliar perbandingan.

KENAPA VIEW, BUKAN TABLE
------------------------
Versi awal menyimpan hasilnya sebagai TABLE supaya join hanya dieksekusi sekali.
Itu masuk akal pada master 299 ribu baris, di mana hasil join berukuran beberapa
juta baris.

Pada master yang jauh lebih besar, ia berbalik jadi penghalang. Diukur dari
distribusi nama master: dengan master 230 juta baris, blocking grade 3/5
menghasilkan ~2.784 kandidat per baris incoming — untuk berkas 1 juta baris
berarti sekitar 2,8 MILIAR pasangan. Menuliskannya ke disk lebih dulu tidak akan
pernah selesai, padahal hampir seluruhnya langsung dibuang node berikutnya yang
hanya menyimpan SATU pemenang per baris incoming.

Sebagai VIEW, kandidatnya mengalir langsung ke penilaian di N6 tanpa pernah
mendarat. Hasilnya identik — yang hilang hanya hasil antara yang memang tidak
pernah dipakai.

`MATCHING_JOIN_MATERIAL=tabel` mengembalikan perilaku lama tanpa menyentuh kode,
untuk membandingkan keduanya berdampingan.
"""

import os
import time

from _shared import TABEL_JOIN, Component, Data, HandleInput, Output, ambil

MATERIAL = os.getenv("MATCHING_JOIN_MATERIAL", "view").strip().lower()


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
        if MATERIAL == "tabel":
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

        con.execute(f"CREATE OR REPLACE VIEW {TABEL_JOIN} AS {query}")
        durasi = time.perf_counter() - mulai
        # Sengaja TIDAK menghitung barisnya di sini: pada view, COUNT(*) berarti
        # menjalankan seluruh join sekali lagi. Jumlah kandidatnya dilaporkan N6
        # sebagai hasil sampingan dari satu-satunya lintasan yang memang harus
        # dilakukannya.
        print(f"[N5] view kandidat disiapkan ({durasi:.2f} detik); "
              f"join dijalankan menyatu dengan penilaian di N6")

        isi = dict(getattr(session, "data", session))
        isi.update({"join_seconds": round(durasi, 2)})
        return Data(data=isi)


def jalankan(session):
    return RunMatchingJoin._join(session)
