"""
NODE G3 — Clean NIK & Flag Anomalies

raw_df  ->  tabel `anomali_df` (kolom asli + vonis per baris)

Yang diperiksa per baris:
  * panjang NIK 16 digit, dan dua digit provinsinya termasuk 38 kode resmi
  * digit 7-8 (hari, +40 untuk perempuan), 9-10 (bulan), 11-12 (tahun) cocok
    dengan kolom tanggal lahir dan jenis kelamin
  * NIK tidak kembar di dalam berkas
  * enam elemen kependudukan terisi

Kasus Excel ditangani khusus: NIK yang tersimpan sebagai 3.20101E+15 dikembangkan
dulu supaya panjangnya pulih, tetapi barisnya tetap dinyatakan tidak tepercaya —
digit belakangnya sudah benar-benar hilang dan tidak bisa dikarang.
"""

from _grading import bersihkan_dan_tandai
from _shared import Component, Data, HandleInput, Output


class FlagAnomalies(Component):
    display_name = "G3. Clean NIK & Flag Anomalies"
    description = "Bersihkan NIK, periksa konsistensi, tandai anomali per baris."
    icon = "scan-search"
    name = "FlagAnomalies"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Flagged", name="flagged", method="tandai")]

    def tandai(self) -> Data:
        sesi = getattr(self.session, "data", self.session)
        return Data(data=bersihkan_dan_tandai(sesi))
