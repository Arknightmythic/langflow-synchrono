"""
NODE 4 — Load Matching Config

sesi  ->  SQL join + ambang klasifikasi

Konfigurasi matching adalah DATA DI DATABASE, bukan kode:

  * `matching_queries.matching_query` — SQL join DuckDB, satu baris per grade
  * `grade_rules`                     — ambang auto/review per grade

Perilaku matching bisa diubah lewat UPDATE satu baris tanpa deploy ulang.
Node ini sengaja tidak meng-hardcode apa pun supaya sifat itu tetap terjaga.

Kelima query merujuk view `incoming_df` dan `master_df` — nama yang dibuat oleh
node 2 dan 3 — sehingga bisa dipakai apa adanya dari sistem lama.
"""

from _shared import Component, Data, HandleInput, Output, ambil


class LoadMatchingConfig(Component):
    display_name = "4. Load Matching Config"
    description = "Ambil SQL join & ambang dari PostgreSQL."
    icon = "settings"
    name = "LoadMatchingConfig"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Session (config siap)", name="config_ready", method="muat")]

    def muat(self) -> Data:
        return self._muat(self.session)

    @staticmethod
    def _muat(session) -> Data:
        con = ambil(session, "con")
        grade = ambil(session, "grade")

        q = con.execute(
            "SELECT matching_query FROM pg.public.matching_queries WHERE grade_code = ?",
            [grade],
        ).fetchone()
        if not q:
            raise ValueError(
                f"Tidak ada matching_query untuk grade {grade} di PostgreSQL. "
                "Jalankan infra/apply_schema.py untuk seed-nya."
            )

        r = con.execute("""
            SELECT auto_missing_max, auto_score_min,
                   review_missing_count, review_score_min, review_score_max
            FROM pg.public.grade_rules WHERE grade_code = ?
        """, [grade]).fetchone()
        if not r:
            raise ValueError(f"Tidak ada grade_rules untuk grade {grade}.")

        aturan = dict(zip(
            ["auto_missing_max", "auto_score_min", "review_missing_count",
             "review_score_min", "review_score_max"], r
        ))
        print(f"[N4] grade={grade} query={len(q[0])} chars aturan={aturan}")

        isi = dict(getattr(session, "data", session))
        isi.update({"matching_query": q[0], "grade_rules": aturan})
        return Data(data=isi)


def jalankan(session):
    return LoadMatchingConfig._muat(session)
