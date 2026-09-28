"""
Endpoint konfigurasi aturan grade.

    GET   /api/v1/config/rules              semua grade
    GET   /api/v1/config/rules/{gradeId}    satu grade
    PATCH /api/v1/config/rules/{gradeId}    ubah sebagian

Di Langflow keduanya POST, karena Langflow memang tidak menyediakan GET untuk
menjalankan flow — membaca aturan pun harus lewat POST. Di sini membaca adalah
GET, sehingga bisa di-cache, bisa dibuka langsung di browser, dan aman diulang.

PATCH, bukan PUT: yang dikirim hanya field yang berubah. UI tidak perlu
mengirim ulang seluruh konfigurasi hanya untuk menggeser satu ambang, dan dua
orang yang menyunting bagian berbeda tidak saling menimpa.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import JSONResponse

from _config import baca_semua, perbarui

from _kolam import pinjam
from .skema import MuatanAturan

rute = APIRouter(prefix="/api/v1/config", tags=["config"])

GRADE = Path(ge=1, le=6, description="1=A sampai 6=F")


@rute.get("/rules", summary="Seluruh aturan grading & matching")
def semua() -> dict:
    """
    Tiga tabel sekaligus sebagai satu gambaran, karena itulah yang dilihat
    pengguna sebagai "aturan grade": `criteria` (ambang kelengkapan & mutu NIK,
    grade A-D), `score` (pita skor dan kelayakan, A-F), dan `matching` (ambang
    similarity).

    Grade E dan F ikut dikembalikan dengan `criteria: null` dan `note` yang
    menjelaskan alasannya, bukan dihilangkan diam-diam — UI perlu bisa
    menampilkan keenam grade dan menerangkan kenapa dua di antaranya tidak
    punya tombol edit.
    """
    with pinjam() as con:
        return baca_semua(con, None)


@rute.get("/rules/{grade_id}", summary="Aturan satu grade")
def satu(grade_id: int = GRADE) -> dict:
    with pinjam() as con:
        hasil = baca_semua(con, grade_id)
    if not hasil["grades"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Grade {grade_id} tidak ada di konfigurasi.")
    return hasil


@rute.patch("/rules/{grade_id}", summary="Ubah ambang satu grade")
def ubah(muatan: MuatanAturan, grade_id: int = GRADE):
    """
    Divalidasi sebelum ditulis.

    Perubahan digabungkan dulu ke salinan konfigurasi, seluruhnya diperiksa,
    baru disimpan — sehingga konfigurasi yang merusak tidak pernah sempat masuk
    ke basis data. Yang paling penting dijaga adalah grade yang menjadi TIDAK
    PERNAH TERCAPAI: kalau ambang B dibuat sama ketat dengan A, semua berkas
    tertangkap di A lebih dulu dan B mati tanpa pesan galat apa pun.

    `dryRun: true` menjalankan seluruh validasi tanpa menulis — untuk tombol
    "periksa" di UI sebelum benar-benar menyimpan.

    KODE STATUS. Badan balasannya identik dengan versi Langflow dalam segala
    keadaan, jadi kode UI yang memeriksa `applied` tetap jalan apa adanya. Yang
    ditambahkan hanyalah 409 saat validasi menolak, supaya klien yang membaca
    kode status pun tahu tanpa harus mengurai badan. Langflow tidak bisa
    membedakan ini: ditolak atau diterima, jawabannya sama-sama 200.
    """
    with pinjam() as con:
        hasil = perbarui(
            con, grade_id,
            {"criteria": muatan.criteria, "score": muatan.score,
             "matching": muatan.matching},
            oleh=muatan.updatedBy,
            dry_run=muatan.dryRun,
        )

    if hasil["problems"]:
        print(f"[config] grade {grade_id} DITOLAK: {len(hasil['problems'])} masalah")
        for p in hasil["problems"]:
            print(f"   - {p}")
        return JSONResponse(hasil, status_code=status.HTTP_409_CONFLICT)

    aksi = "divalidasi" if muatan.dryRun else "disimpan"
    print(f"[config] grade {grade_id} {aksi}, "
          f"{len(hasil.get('changed') or [])} field berubah")
    return hasil
