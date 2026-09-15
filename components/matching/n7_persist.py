"""
NODE 7 — Persist Results

sesi  ->  tulis ke PostgreSQL  ->  ringkasan kecil (payload balasan API)

Dua tabel ditulis:
  * `institution`    — vonis untuk SETIAP baris incoming
  * `manual_matches` — hanya baris ber-match_result = 2, plus snapshot nilai
                       incoming supaya reasoning tidak perlu membaca parquet
                       ulang per baris

UPSERT, BUKAN APPEND
--------------------
Keduanya memakai ON CONFLICT (file_id, id_incoming) DO UPDATE, memanfaatkan
UNIQUE constraint di skema.

Ini memperbaiki cacat nyata sistem lama. StarRocks tidak bisa menegakkan
constraint semacam itu, sehingga matching ulang selalu MENAMBAH baris: tabel
institution di sana berisi 22,9 juta baris untuk 14,2 juta record unik — 34%
duplikat, satu file mencapai rasio 15x, dan semua COUNT jadi menggelembung.
Dengan upsert, menjalankan matching dua kali menghasilkan data yang sama persis.

YANG SENGAJA TIDAK DIKERJAKAN DI SINI
-------------------------------------
Status alur (`sync_status`, `matching_task_status`), pemicu reasoning/export,
dan audit. Itu urusan pemanggil. Service ini hanya mengembalikan hasil; backend
Synchrono yang memutuskan apa yang dilakukan setelahnya.
"""

import json
import time

from _shared import (
    TABEL_HASIL,
    Component,
    Data,
    HandleInput,
    Message,
    Output,
    ambil,
)


class PersistResults(Component):
    display_name = "7. Persist Results"
    description = "Upsert institution + manual_matches ke PostgreSQL."
    icon = "save"
    name = "PersistResults"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Summary", name="summary", method="simpan")]

    # Mengembalikan Message, BUKAN Data. Endpoint /api/v1/run hanya menampilkan
    # hasil dari komponen bertipe output (chat/text); komponen yang mengembalikan
    # Data dieksekusi tapi hasilnya TIDAK ikut di badan respons — flow-nya sukses
    # dan datanya tertulis, tapi pemanggil menerima "outputs": [] yang kosong.
    # Ini berlaku untuk output_type "text" maupun "any".
    def simpan(self) -> Message:
        hasil = self._simpan(self.session, dry_run=False)
        return Message(text=json.dumps(hasil, ensure_ascii=False, indent=2))

    @staticmethod
    def _simpan(session, dry_run: bool = False) -> Data:
        con = ambil(session, "con")
        file_id = ambil(session, "file_id")
        grade = ambil(session, "grade")
        ringkas = ambil(session, "ringkasan")

        n_hasil = con.execute(f"SELECT COUNT(*) FROM {TABEL_HASIL}").fetchone()[0]
        n_review = ringkas["manual_review"]

        if dry_run:
            print(f"[N7] DRY RUN — akan upsert {n_hasil:,} institution "
                  f"+ {n_review:,} manual_matches")
            return _ringkasan(session, dry_run=True, detik=0.0)

        mulai = time.perf_counter()

        # SEMUA kolom diisi eksplisit dan urutannya harus sama persis dengan
        # definisi tabel: DuckDB menulis ke PostgreSQL lewat COPY dan MENGABAIKAN
        # daftar kolom yang ditulis di INSERT. Kalau ada kolom yang dilewati, ia
        # terkirim sebagai NULL — itulah sebabnya skema ini tidak memakai
        # bigserial maupun DEFAULT.
        con.execute(f"""
            INSERT INTO pg.public.institution
            SELECT file_id, id_incoming, nik_master, match_score, match_result,
                   now(), now()
            FROM {TABEL_HASIL}
            ON CONFLICT (file_id, id_incoming) DO UPDATE SET
                nik_master    = EXCLUDED.nik_master,
                match_score   = EXCLUDED.match_score,
                match_result  = EXCLUDED.match_result,
                inserted_date = EXCLUDED.inserted_date
        """)

        if n_review:
            # Nilai mentah diambil dari view incoming_df — kolom yang tidak ada
            # di parquet diisi NULL lewat TRY, supaya file grade 1/2 yang tanpa
            # tanggal_lahir tetap bisa diproses.
            kolom = set(ambil(session, "incoming_columns"))

            def opsional(nama):
                return f"i.{nama}" if nama in kolom else "NULL"

            con.execute(f"""
                INSERT INTO pg.public.manual_matches
                SELECT
                    h.file_id, h.id_incoming,
                    {opsional('nama')},
                    {opsional('tempat_lahir')},
                    CAST({opsional('tanggal_lahir')} AS VARCHAR),
                    {opsional('jenis_kelamin')},
                    {opsional('nama_ibu')},
                    NULL, NULL, NULL,   -- reason, pattern_name, reasoning_source
                    'PENDING'           -- reasoning_status
                FROM {TABEL_HASIL} h
                JOIN incoming_df i ON i.id = h.id_incoming
                WHERE h.match_result = 2
                ON CONFLICT (file_id, id_incoming) DO UPDATE SET
                    nama_incoming          = EXCLUDED.nama_incoming,
                    tempat_lahir_incoming  = EXCLUDED.tempat_lahir_incoming,
                    tanggal_lahir_incoming = EXCLUDED.tanggal_lahir_incoming,
                    jenis_kelamin_incoming = EXCLUDED.jenis_kelamin_incoming,
                    nama_ibu_incoming      = EXCLUDED.nama_ibu_incoming,
                    reasoning_status       = 'PENDING',
                    reason                 = NULL
            """)

        durasi = time.perf_counter() - mulai
        print(f"[N7] tersimpan: {n_hasil:,} institution, {n_review:,} manual_matches "
              f"({durasi:.1f} detik)")
        return _ringkasan(session, dry_run=False, detik=round(durasi, 2))


def _ringkasan(session, dry_run: bool, detik: float) -> dict:
    ringkas = ambil(session, "ringkasan")
    return {
        "message": f"Grade {ambil(session, 'grade')} matching completed",
        "file_id": ambil(session, "file_id"),
        "grade": ambil(session, "grade"),
        "processed_rows": ambil(session, "processed_rows"),
        "matched_rows": ringkas["auto_match"],
        "manual_review_rows": ringkas["manual_review"],
        "unmatched_rows": ringkas["unmatch"],
        "sync_status": ambil(session, "sync_status"),
        "dry_run": dry_run,
        "persist_seconds": detik,
    }


def jalankan(session, dry_run: bool = False) -> Data:
    """Entry point run_local.py — membungkus dict jadi Data agar antarmukanya tetap sama."""
    return Data(data=PersistResults._simpan(session, dry_run=dry_run))
