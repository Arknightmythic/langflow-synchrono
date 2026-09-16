"""
NODE API-3 — Get Grading Rules   (untuk menu "Rule" di UI Synchrono)

{grade_id?}  ->  seluruh konfigurasi per grade

Mengembalikan TIGA tabel sekaligus sebagai satu gambaran, karena itulah yang
dilihat pengguna sebagai "aturan grade":

    criteria  ambang kelengkapan & mutu NIK   (grade A-D, bisa diedit)
    score     pita skor, label, kelayakan     (grade A-F)
    matching  ambang similarity saat mencocokkan

Grade E dan F ikut dikembalikan dengan `criteria: null` dan `note` yang
menjelaskan alasannya, bukan dihilangkan diam-diam — UI perlu bisa menampilkan
keenam grade dan menerangkan kenapa dua di antaranya tidak punya tombol edit.
"""

import json

from _config import baca_semua
from _shared import Component, Message, MessageTextInput, Output, buka_koneksi


class GradingRuleGet(Component):
    display_name = "API 3. Get Grading Rules"
    description = "Konfigurasi aturan grading & matching per grade."
    icon = "list"
    name = "GradingRuleGet"

    inputs = [
        MessageTextInput(
            name="grade_id", display_name="Grade ID", required=False,
            info="1-6. Kosongkan untuk mengambil semuanya.",
        ),
    ]
    outputs = [Output(display_name="Rules", name="rules", method="ambil")]

    def ambil(self) -> Message:
        mentah = (self.grade_id or "").strip()
        gid = None
        if mentah:
            try:
                gid = int(mentah)
            except ValueError as e:
                raise ValueError(f"grade_id harus angka 1-6, dapat: {mentah!r}") from e
            if gid not in range(1, 7):
                raise ValueError(f"grade_id harus 1-6, dapat: {gid}")

        con = buka_koneksi()
        try:
            hasil = baca_semua(con, gid)
        finally:
            con.close()

        if gid is not None and not hasil["grades"]:
            raise ValueError(f"Grade {gid} tidak ada di konfigurasi.")

        return Message(text=json.dumps(hasil, ensure_ascii=False, default=str))
