"""
NODE G4 — Score & Grade

anomali_df  ->  grade A-F + skor mutu 0-100 + metrik agregat

Grade ditentukan aturan STRUKTURAL (kolom apa yang ada, seberapa terisi),
persis seperti GraderService yang sudah berjalan. Skor adalah ukuran menerus
yang lalu dipetakan ke dalam pita milik grade itu, diambil dari tabel
`grade_bands` di PostgreSQL.

Urutannya sengaja begitu, bukan sebaliknya: matching memilih rumus pembobotan
berdasarkan grade, dan berkas lima elemen tanpa kolom NIK harus tetap grade C
meski skornya tinggi — kalau tidak, matching akan memakai rumus ber-NIK
terhadap kolom yang tidak ada.
"""

from _grading import skor_dan_grade
from _shared import Component, Data, HandleInput, Output


class ScoreAndGrade(Component):
    display_name = "G4. Score & Grade"
    description = "Hitung metrik agregat, tentukan grade A-F dan skor mutu."
    icon = "gauge"
    name = "ScoreAndGrade"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Scored", name="scored", method="nilai")]

    def nilai(self) -> Data:
        sesi = getattr(self.session, "data", self.session)
        return Data(data=skor_dan_grade(sesi))
