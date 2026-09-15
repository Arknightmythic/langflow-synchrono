"""
NODE 6 — Score & Classify

sesi  ->  tabel `match_results` di dalam DuckDB

Tiga hal dikerjakan dalam SATU statement SQL:

  1. Skor Jaro-Winkler berbobot per grade. Macro `j()` menirukan safe_jaro()
     Python: 0 kalau salah satu sisi NULL/kosong.
  2. Hitung atribut yang kosong (count_missing_attributes).
  3. Klasifikasi lewat ambang di grade_rules -> 1 auto / 2 review / 3 unmatch.

Lalu QUALIFY memilih kandidat berskor tertinggi per baris incoming.

CATATAN KESETARAAN
  * `jaro_winkler_similarity` DuckDB sudah diverifikasi IDENTIK bit-per-bit
    dengan rapidfuzz, termasuk rumus grade 5 yang memakai rata-rata wilayah
    bersyarat (selisih 0.00e+00).
  * Baris tanpa kandidat master (nik_master NULL) langsung jadi skor 0 /
    result 3, tanpa dihitung — sama seperti short-circuit di versi Python.
  * Untuk skor seri, versi Python mempertahankan kandidat yang DITEMUI LEBIH
    DULU (urutan iterasi, praktis acak). Di sini tie-break-nya eksplisit pakai
    nik_master supaya hasilnya deterministik dan bisa direproduksi.
"""

import time

from _shared import (
    SQL_KLASIFIKASI,
    TABEL_HASIL,
    TABEL_JOIN,
    Component,
    Data,
    HandleInput,
    Output,
    ambil,
    sql_missing,
    sql_skor,
)


class ScoreAndClassify(Component):
    display_name = "6. Score & Classify"
    description = "Jaro-Winkler berbobot + klasifikasi ambang, seluruhnya di SQL."
    icon = "calculator"
    name = "ScoreAndClassify"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Session (skor selesai)", name="scored", method="hitung")]

    def hitung(self) -> Data:
        return self._hitung(self.session)

    @staticmethod
    def _hitung(session) -> Data:
        con = ambil(session, "con")
        grade = ambil(session, "grade")
        file_id = ambil(session, "file_id")
        aturan = ambil(session, "grade_rules")

        skor = sql_skor(grade)
        missing = sql_missing(grade)

        # Aturan disisipkan sebagai baris konstanta supaya SQL klasifikasinya
        # generik dan tetap merujuk nama kolom grade_rules apa adanya.
        r = aturan
        sql_aturan = f"""
            SELECT
                {'NULL' if r['auto_missing_max'] is None else r['auto_missing_max']}::INTEGER AS auto_missing_max,
                {r['auto_score_min']}::DOUBLE AS auto_score_min,
                {'NULL' if r['review_missing_count'] is None else r['review_missing_count']}::INTEGER AS review_missing_count,
                {r['review_score_min']}::DOUBLE AS review_score_min,
                {r['review_score_max']}::DOUBLE AS review_score_max
        """

        mulai = time.perf_counter()
        con.execute(f"""
            CREATE OR REPLACE TABLE {TABEL_HASIL} AS
            WITH skor AS (
                SELECT
                    incoming_row_id,
                    nik_master,
                    CASE WHEN nik_master IS NULL THEN 0.0 ELSE {skor} END AS skor,
                    CASE WHEN nik_master IS NULL THEN 0   ELSE {missing} END AS missing_count
                FROM {TABEL_JOIN}
            ),
            aturan AS ({sql_aturan}),
            vonis AS (
                SELECT
                    s.incoming_row_id,
                    s.nik_master,
                    s.skor,
                    CASE WHEN s.nik_master IS NULL THEN 3 ELSE {SQL_KLASIFIKASI} END AS match_result
                FROM skor s CROSS JOIN aturan r
            )
            SELECT
                '{file_id}'          AS file_id,
                incoming_row_id      AS id_incoming,
                nik_master,
                ROUND(skor, 2)       AS match_score,
                match_result
            FROM vonis
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY incoming_row_id
                ORDER BY skor DESC, nik_master NULLS LAST
            ) = 1
        """)

        # Jaring pengaman: kalau matching_query memakai INNER JOIN, baris incoming
        # tanpa kandidat sama sekali tidak muncul di joined_df. Versi Python
        # menambalnya dengan mengisi default unmatch — perilaku itu dipertahankan.
        con.execute(f"""
            INSERT INTO {TABEL_HASIL}
            SELECT '{file_id}', i.id, NULL, 0.0, 3
            FROM incoming_df i
            WHERE NOT EXISTS (
                SELECT 1 FROM {TABEL_HASIL} h WHERE h.id_incoming = i.id
            )
        """)
        durasi = time.perf_counter() - mulai

        ringkas = dict(con.execute(f"""
            SELECT match_result, COUNT(*) FROM {TABEL_HASIL} GROUP BY match_result
        """).fetchall())
        hasil = {
            "auto_match": ringkas.get(1, 0),
            "manual_review": ringkas.get(2, 0),
            "unmatch": ringkas.get(3, 0),
        }
        total = con.execute(f"SELECT COUNT(*) FROM {TABEL_HASIL}").fetchone()[0]
        print(f"[N6] {hasil} total={total:,} ({durasi:.1f} detik)")

        isi = dict(getattr(session, "data", session))
        isi.update({
            "ringkasan": hasil,
            "processed_rows": total,
            "score_seconds": round(durasi, 2),
            # sync_status 1 = masih ada yang perlu ditinjau manusia, 3 = selesai
            "sync_status": 1 if hasil["manual_review"] else 3,
        })
        return Data(data=isi)


def jalankan(session):
    return ScoreAndClassify._hitung(session)
