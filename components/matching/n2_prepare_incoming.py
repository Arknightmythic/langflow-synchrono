"""
NODE 2 — Prepare Incoming

sesi  ->  view `incoming_df` di dalam DuckDB

Parquet TIDAK di-download. `read_parquet('s3://...')` membacanya langsung dari
SeaweedFS, dan DuckDB hanya menarik kolom serta baris yang benar-benar dipakai.

Normalisasi kolom (lowercase+trim, 6 format tanggal, whitelist gender) dilakukan
di dalam view. Ini menentukan hasil matching, jadi harus sama persis dengan
sistem lama — lihat _shared.sql_view_incoming().
"""

from _shared import (
    VIEW_INCOMING, Component, Data, HandleInput, Output, ambil, sql_view_incoming,
)


class PrepareIncoming(Component):
    display_name = "2. Prepare Incoming"
    description = "View atas parquet di SeaweedFS + normalisasi kolom."
    icon = "file-down"
    name = "PrepareIncoming"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Session (incoming siap)", name="incoming_ready", method="siapkan")]

    def siapkan(self) -> Data:
        return self._siapkan(self.session)

    @staticmethod
    def _siapkan(session) -> Data:
        con = ambil(session, "con")
        path = ambil(session, "parquet_path")

        kolom_ada = {
            r[0].strip().lower()
            for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
        }
        if "id" not in kolom_ada:
            raise ValueError(
                f"Parquet tidak punya kolom `id`. Kolom yang ada: {sorted(kolom_ada)}"
            )

        con.execute(f"""
            CREATE OR REPLACE VIEW {VIEW_INCOMING} AS
            SELECT
                {sql_view_incoming(kolom_ada)}
            FROM read_parquet('{path}')
        """)

        n = con.execute(f"SELECT COUNT(*) FROM {VIEW_INCOMING}").fetchone()[0]
        print(f"[N2] incoming rows={n:,} kolom terbaca={len(kolom_ada)}")

        isi = dict(getattr(session, "data", session))
        isi.update({"incoming_rows": n, "incoming_columns": sorted(kolom_ada)})
        return Data(data=isi)


def jalankan(session):
    return PrepareIncoming._siapkan(session)
