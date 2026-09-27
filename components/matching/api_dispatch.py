"""
NODE API — Dispatch Matching Job   (POST /api/v1/run/matching-dispatch)

muatan  ->  { status: "IN_PROGRESS", jobId, message }

Node ini SENGAJA tidak melakukan matching. Ia hanya:

    1. mengurai & memeriksa muatan (spesifikasi integrasi §3.2),
    2. melepas thread pekerja,
    3. langsung membalas IN_PROGRESS (§3.3).

Muatannya diterima di kolom `payload` sebagai JSON utuh — persis bentuk
`tweaks.MatchingDispatch-b4819.payload` di spesifikasi §3.1.

ID NODE-NYA DIPAKSA `MatchingDispatch-b4819`

Id itu datang dari spesifikasi, bukan dari skema id turunan kita (yang akan
menghasilkan `MatchingDispatch-c793c`). Portal mengirim tweak dengan kunci dari
spesifikasi, dan tweak ke node yang tidak ada TIDAK menimbulkan galat —
Langflow mengabaikannya diam-diam. Lihat infra/buat_flow_matching_dispatch.py.
"""

import json

from _matching import susun_job
from _matching_worker import lepas
from _shared import Component, Message, MessageTextInput, Output


class MatchingDispatch(Component):
    display_name = "API. Dispatch Matching Job"
    description = "Periksa muatan, lepas pekerja matching, balas IN_PROGRESS."
    icon = "send"
    name = "MatchingDispatch"

    inputs = [
        MessageTextInput(
            name="payload", display_name="Payload JSON", required=True,
            info="Isi `payload` dari spesifikasi integrasi matching §3.1.",
        ),
    ]
    outputs = [Output(display_name="Diterima", name="accepted", method="terima")]

    def terima(self) -> Message:
        teks = (self.payload or "").strip()
        try:
            muatan = json.loads(teks)
        except json.JSONDecodeError as e:
            raise ValueError(f"payload bukan JSON yang sah: {e}") from e
        if not isinstance(muatan, dict):
            raise ValueError("payload harus objek JSON, bukan array atau skalar")

        # Galat muatan dilempar DI SINI, sebelum ada thread apa pun. Portal
        # mendapat penolakan seketika, bukan IN_PROGRESS yang gagal beberapa
        # detik kemudian lewat callback.
        job = susun_job(muatan)
        lepas(job)

        print(f"[M] {job['job_id']} diterima untuk file {job['file_id']} "
              f"x master {job['master_file_id']}")
        return Message(text=json.dumps({
            "status": "IN_PROGRESS",
            "jobId": job["job_id"],
            "message": "Job pencocokan data diterima dan sedang diproses oleh "
                       "Data Science worker.",
        }, ensure_ascii=False))
