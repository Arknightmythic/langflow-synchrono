"""
NODE G6 — Build Callback Payload

sesi  ->  JSON GradingCallbackPayload

Bentuknya persis seperti bagian 4.1 spesifikasi integrasi, sehingga keluaran
node ini bisa dikirim apa adanya ke portal Synchrono. JSON yang sama juga
disimpan ke kolom `grading_jobs.result` — API polling mengembalikan objek itu
tanpa diubah, jadi backend tidak perlu menangani dua bentuk data yang berbeda
antara jalur webhook dan jalur polling.
"""

from _grading import ringkas, susun_hasil
from _shared import Component, HandleInput, Message, Output


class BuildCallbackPayload(Component):
    display_name = "G6. Build Callback Payload"
    description = "Susun GradingCallbackPayload dari metrik grading."
    icon = "file-json"
    name = "BuildCallbackPayload"

    inputs = [HandleInput(name="session", display_name="Session",
                          input_types=["Data"], required=True)]
    outputs = [Output(display_name="Payload", name="payload", method="susun")]

    def susun(self) -> Message:
        sesi = getattr(self.session, "data", self.session)
        return Message(text=ringkas(susun_hasil(sesi)))
