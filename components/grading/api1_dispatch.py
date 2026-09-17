"""
NODE API-1 — Dispatch Grading Job   (POST /jobs/grade)

muatan  ->  202 { status: "QUEUED", jobId }

Node ini SENGAJA tidak melakukan grading. Ia hanya:

    1. mencatat job ke `grading_jobs` berstatus QUEUED,
    2. melepas thread pekerja,
    3. langsung balas.

Itulah yang menjaga backend dan UI Synchrono tetap responsif: spesifikasi
menuntut balasan di bawah 5 detik, sedangkan grading sendiri makan puluhan
detik sampai menit. Kemajuannya ditanyakan lewat node GradingStatus, atau
menunggu webhook kalau `callback.url` diisi.

DUA CARA MENGISINYA

  * Kolom `payload`: tempel OutboundGradingJobPayload utuh sebagai JSON.
    Inilah yang dipakai backend.
  * Kolom satuan (file_id, s3_bucket, parquet_key, ...): lebih enak untuk
    mencoba dari kanvas. Nilai dari `payload` menang kalau keduanya diisi.
"""

import json

from _jobs import catat_job, panen_mangkrak, susun_job
from _shared import Component, Message, MessageTextInput, Output, buka_koneksi
from _worker import lepas


class GradingDispatch(Component):
    display_name = "API 1. Dispatch Grading Job"
    description = "Catat job, lepas pekerja latar belakang, balas 202 QUEUED."
    icon = "send"
    name = "GradingDispatch"

    inputs = [
        MessageTextInput(
            name="payload", display_name="Payload JSON", required=False,
            info="OutboundGradingJobPayload utuh. Kosongkan untuk memakai kolom di bawah.",
        ),
        MessageTextInput(name="file_id", display_name="File ID", required=False),
        MessageTextInput(name="s3_bucket", display_name="S3 Bucket", required=False,
                         value="syncrono-uploads"),
        MessageTextInput(name="parquet_key", display_name="Parquet Key", required=False,
                         info="mis. uploads/{fileId}/data.parquet"),
        MessageTextInput(name="s3_endpoint", display_name="S3 Endpoint", required=False),
        MessageTextInput(name="callback_url", display_name="Callback URL", required=False,
                         info="Kosongkan kalau backend memilih polling saja."),
        MessageTextInput(name="callback_token", display_name="Callback Token",
                         required=False),
    ]
    outputs = [Output(display_name="Accepted", name="accepted", method="terima")]

    def terima(self) -> Message:
        muatan = {}
        teks = (self.payload or "").strip()
        if teks:
            try:
                muatan = json.loads(teks)
            except json.JSONDecodeError as e:
                raise ValueError(f"payload bukan JSON yang sah: {e}") from e
            if not isinstance(muatan, dict):
                raise ValueError("payload harus objek JSON, bukan array atau skalar")

        job = susun_job(muatan, bawaan={
            "file_id": self.file_id,
            "s3_bucket": self.s3_bucket,
            "parquet_key": self.parquet_key,
            "s3_endpoint": self.s3_endpoint,
            "callback_url": self.callback_url,
            "callback_token": self.callback_token,
        })

        con = buka_koneksi()
        try:
            # Sekalian bereskan job yang pekerjanya hilang karena restart.
            # Tempat paling masuk akal: setiap unggahan baru pasti melewati sini.
            dipanen = panen_mangkrak(con)
            if dipanen:
                print(f"[API1] {dipanen} job mangkrak ditandai gagal")
            catat_job(con, job)
        finally:
            con.close()

        lepas(job)

        print(f"[API1] {job['job_id']} diantrekan untuk file {job['file_id']}")
        return Message(text=json.dumps({
            "status": "QUEUED",
            "jobId": job["job_id"],
            "fileId": job["file_id"],
            "message": "Grading job successfully queued for processing.",
        }, ensure_ascii=False))
