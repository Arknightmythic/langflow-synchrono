"""
NODE 3 — Prepare Master

sesi  ->  view `master_df` di dalam DuckDB

Master TIDAK ditarik ke memori aplikasi. View ini menunjuk langsung ke tabel
PostgreSQL yang sudah di-ATTACH; DuckDB yang mengatur pengambilannya saat join.

Di arsitektur lama, tahap setara menarik 299 ribu baris ke memori worker dan
memakan 84 detik dari laptop (6 detik dari server). Di sini biaya itu hilang —
tidak ada transfer penuh, hanya kolom dan baris yang dibutuhkan join.
"""

from _shared import (
    SQL_VIEW_MASTER, VIEW_MASTER, Component, Data, HandleInput, Output, ambil,
)


class PrepareMaster(Component):
    display_name = "3. Prepare Master"
    description = "View atas tabel master PostgreSQL + normalisasi kolom."
    icon = "users"
    name = "PrepareMaster"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Session (master siap)", name="master_ready", method="siapkan")]

    def siapkan(self) -> Data:
        return self._siapkan(self.session)

    @staticmethod
    def _siapkan(session) -> Data:
        con = ambil(session, "con")

        con.execute(f"""
            CREATE OR REPLACE VIEW {VIEW_MASTER} AS
            SELECT {SQL_VIEW_MASTER}
            FROM pg.public.master
        """)

        n = con.execute(f"SELECT COUNT(*) FROM {VIEW_MASTER}").fetchone()[0]
        print(f"[N3] master rows={n:,}")
        if n == 0:
            raise ValueError(
                "Tabel master di PostgreSQL kosong — matching akan menghasilkan "
                "unmatch untuk semua baris. Isi dulu lewat infra/migrate_master.py."
            )

        isi = dict(getattr(session, "data", session))
        isi["master_rows"] = n
        return Data(data=isi)


def jalankan(session):
    return PrepareMaster._siapkan(session)
