"""
NODE API-2 — Grading Status   (GET /jobs/{fileId} versi Langflow)

{file_id}  ->  status job terbaru untuk berkas itu

Inilah yang dipanggil berulang (polling) oleh backend Synchrono selama menunggu.
Balasannya membawa `result` — muatan callback yang SAMA PERSIS dengan yang
dikirim lewat webhook — sehingga backend cukup punya satu jalur penanganan
hasil, apa pun cara ia mengetahuinya.

Setiap panggilan juga memanen job mangkrak. Karena pekerja grading adalah thread
di dalam container Langflow, container yang restart di tengah proses akan
meninggalkan job berstatus RUNNING tanpa ada yang mengerjakannya. Detak yang
berhenti membuat keadaan itu terdeteksi di polling berikutnya: job dilaporkan
FAILED dan backend bisa mengirim ulang, alih-alih menunggu selamanya.
"""

import json

from _jobs import ambil_job, panen_mangkrak
from _shared import Component, Message, MessageTextInput, Output, buka_koneksi


class GradingStatus(Component):
    display_name = "API 2. Grading Status"
    description = "Status job grading terbaru untuk satu file_id (untuk polling)."
    icon = "activity"
    name = "GradingStatus"

    inputs = [
        MessageTextInput(name="file_id", display_name="File ID", required=False,
                         info="Diabaikan kalau job_id diisi."),
        MessageTextInput(name="job_id", display_name="Job ID", required=False,
                         info="Untuk menanyakan job tertentu, termasuk yang lama."),
    ]
    outputs = [Output(display_name="Status", name="status", method="lihat")]

    def lihat(self) -> Message:
        file_id = (self.file_id or "").strip() or None
        job_id = (self.job_id or "").strip() or None
        if not file_id and not job_id:
            raise ValueError("Isi salah satu: file_id atau job_id")

        con = buka_koneksi()
        try:
            panen_mangkrak(con)
            job = ambil_job(con, file_id=file_id, job_id=job_id)
        finally:
            con.close()

        if job is None:
            jawab = {
                "found": False,
                "status": "NOT_FOUND",
                "fileId": file_id,
                "jobId": job_id,
                "message": "Belum ada job grading untuk berkas ini.",
            }
        else:
            jawab = {
                "found": True,
                # QUEUED | RUNNING | COMPLETED | FAILED
                "status": job["status"],
                "jobId": job["job_id"],
                "fileId": job["file_id"],
                # Selesai atau tidak — satu boolean supaya backend tidak perlu
                # menghafal daftar status untuk memutuskan berhenti polling.
                "done": job["status"] in ("COMPLETED", "FAILED"),
                "stage": job["stage"],
                "queuedAt": job["created_at"],
                "startedAt": job["started_at"],
                "finishedAt": job["finished_at"],
                "gradingDurationMs": job["grading_duration_ms"],
                "enrichedParquetKey": job["enriched_key"],
                "parquetSizeBytes": job["parquet_size_bytes"],
                "recordCount": job["row_count"],
                "callbackStatus": job["callback_status"],
                "callbackError": job["callback_error"],
                "error": job["error"],
                "result": job["result"],
            }

        return Message(text=json.dumps(jawab, ensure_ascii=False))
