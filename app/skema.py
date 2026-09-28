"""
Bentuk muatan masuk.

SENGAJA LONGGAR. Muatan grading datang dari portal dengan bentuk yang sudah
ditetapkan spesifikasi (OutboundGradingJobPayload), dan penerjemahnya sudah ada
dan sudah teruji: `_jobs.susun_job()`. Menulis ulang aturan yang sama sebagai
model Pydantic berarti dua tempat yang harus dijaga tetap sama, dan yang kedua
pasti akan tertinggal.

Jadi model di sini hanya untuk DOKUMENTASI OpenAPI — supaya halaman `/docs`
menunjukkan bentuk yang benar kepada orang yang mengintegrasikan — sementara
validasi yang sesungguhnya tetap di `susun_job()`, satu-satunya tempat.

Yang divalidasi ketat hanyalah muatan config, karena di sana tidak ada
penerjemah lain yang sudah menanganinya.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Institusi(BaseModel):
    id: str | None = None
    name: str | None = None


class Callback(BaseModel):
    url: str | None = None
    secretToken: str | None = None


class MuatanGrading(BaseModel):
    """
    OutboundGradingJobPayload — spesifikasi integrasi bagian 3.1.

    `extra: "allow"` berarti field yang tidak disebut di sini pun tetap
    diteruskan ke `susun_job()`. Jadi muatan portal tidak pernah ditolak hanya
    karena memuat sesuatu yang belum terdaftar. Tapi yang TIDAK terdaftar juga
    tidak muncul di halaman /docs — dan itulah satu-satunya alasan seluruh
    field di bawah ditulis lengkap: supaya orang yang mengintegrasikan melihat
    kontraknya, bukan menebaknya.
    """

    model_config = {"extra": "allow", "json_schema_extra": {"examples": [{
        "fileId": "pop_1789441689523_iq5ws",
        "filename": "data_dukcapil_2026.csv",
        "s3Bucket": "syncrono-uploads",
        "s3Endpoint": "",
        "rawSourceKey": "uploads/pop_1789441689523_iq5ws/raw/data_dukcapil_2026.csv",
        "enrichedParquetKey": "uploads/pop_1789441689523_iq5ws/enriched.parquet",
        "institution": {"id": "inst-001", "name": "Kemensos"},
        "callback": {"url": "http://portal:3000/api/internal/grading/callback",
                     "secretToken": "rahasia"},
    }]}}

    fileId: str | None = None
    filename: str | None = None
    s3Bucket: str | None = None
    # Kosongkan. Diisi `localhost` justru menggagalkan job: dari dalam container
    # itu menunjuk container ini sendiri, bukan SeaweedFS. Engine mengabaikan
    # nilai localhost dan memakai S3_ENDPOINT dari environment-nya.
    s3Endpoint: str | None = None

    # Berkas MASUKAN. `rawSourceKey` adalah satu-satunya kunci sumber di
    # spesifikasi bagian 3.1; `csvKey` dan `parquetKey` diterima sebagai
    # warisan karena portal masih mengirimkannya.
    rawSourceKey: str | None = None
    csvKey: str | None = None
    parquetKey: str | None = None

    # Berkas KELUARAN. Wajib menurut spesifikasi bagian 3.2 — engine menulis
    # hasil grading tepat ke kunci ini.
    enrichedParquetKey: str | None = None

    rowCount: int | None = None
    institution: Institusi | None = None
    callback: Callback | None = None


class MuatanAturan(BaseModel):
    """
    Perubahan sebagian pada satu grade.

    `gradeId` tidak ada di sini — ia diambil dari path URL. Itu satu perbedaan
    nyata dengan versi Langflow, yang harus menaruhnya di dalam muatan karena
    tidak punya konsep path.
    """

    model_config = {"json_schema_extra": {"examples": [{
        "criteria": {"minCompleteness": {"tempat_lahir": 0.75}},
        "score": {"min": 72},
        "updatedBy": "reno",
        "dryRun": True,
    }]}}

    criteria: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    matching: dict[str, Any] | None = None
    updatedBy: str | None = None
    dryRun: bool = False


class MuatanMatching(BaseModel):
    model_config = {"json_schema_extra": {"examples": [{
        "fileId": "d88150c5",
        "parquetPath": "s3://synchrono/curated/contoh.parquet",
        "grade": 1,
        "pakaiEnriched": True,
        "dryRun": True,
    }]}}

    fileId: str
    parquetPath: str
    grade: int = Field(ge=1, le=5)
    pakaiEnriched: bool = True
    # Bawaannya TRUE, dan itu disengaja. Sekali salah panggil tanpa sadar
    # berarti ratusan ribu baris tertulis ke tabel `institution`. Di Langflow
    # bawaannya false dan hal itu sudah sempat terjadi sekali.
    dryRun: bool = True
