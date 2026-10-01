"""
Endpoint konfigurasi aturan grading & matching.

    GET   /api/v1/config/rules              semua grade + global + versi
    GET   /api/v1/config/rules/{gradeId}    satu grade
    PATCH /api/v1/config/rules/{gradeId}    ubah sebagian satu grade
    PATCH /api/v1/config/global             ubah nilai global
    GET   /api/v1/config/history            riwayat perubahan (terbaru dulu)
    GET   /api/v1/config/versions/{versi}   isi konfigurasi satu versi

Di Langflow keduanya POST, karena Langflow memang tidak menyediakan GET untuk
menjalankan flow — membaca aturan pun harus lewat POST. Di sini membaca adalah
GET, sehingga bisa di-cache, bisa dibuka langsung di browser, dan aman diulang.

PATCH, bukan PUT: yang dikirim hanya field yang berubah. UI tidak perlu
mengirim ulang seluruh konfigurasi hanya untuk menggeser satu ambang, dan dua
orang yang menyunting bagian berbeda tidak saling menimpa.

Muatan yang bentuknya salah (field tak dikenal, tipe keliru) -> 400. Muatan
yang bentuknya benar tapi hasilnya merusak (bobot tidak berjumlah 100, grade
jadi tak tercapai) -> 409 dengan daftar `problems`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse

from _config import (baca_riwayat, baca_semua, baca_versi, perbarui,
                     perbarui_global)

from _kolam import pinjam
from .skema import MuatanAturan, MuatanGlobal

rute = APIRouter(prefix="/api/v1/config", tags=["config"])

GRADE = Path(ge=1, le=6, description="1=A sampai 6=F")


@rute.get("/rules", summary="Seluruh aturan grading & matching")
def semua() -> dict:
    """
    Per grade: `criteria` (ambang kelengkapan & mutu NIK, grade A-D), `score`
    (pita skor dan kelayakan, A-F), dan `matching` — ambang AUTO/REVIEW,
    `weights` (bobot skor per elemen, persen), `missingElements` (elemen yang
    dihitung kosong), `nameCleaning` (pembersihan nama), `blocking` (kueri,
    hanya dibaca), dan `analysis` (skor tertinggi per jumlah kosong beserta
    peringatannya).

    Di luar per grade: `global` (bobot skor mutu grading, kombinasi grade E,
    selisih seri, ambang nama ibu bertentangan) dan `configVersion` — sidik
    konfigurasi yang berlaku sekarang.

    Grade E dan F ikut dikembalikan dengan `criteria: null` dan `note` yang
    menjelaskan alasannya, bukan dihilangkan diam-diam — UI perlu bisa
    menampilkan keenam grade dan menerangkan kenapa dua di antaranya tidak
    punya tombol edit kriteria.
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


def _jawab(hasil: dict, label: str, dry_run: bool):
    if hasil["problems"]:
        print(f"[config] {label} DITOLAK: {len(hasil['problems'])} masalah")
        for p in hasil["problems"]:
            print(f"   - {p}")
        return JSONResponse(hasil, status_code=status.HTTP_409_CONFLICT)

    aksi = "divalidasi" if dry_run else "disimpan"
    print(f"[config] {label} {aksi}, {len(hasil.get('changed') or [])} field berubah"
          + (f", versi {hasil['configVersion']}" if hasil.get("configVersion") else ""))
    return hasil


@rute.patch("/rules/{grade_id}", summary="Ubah aturan satu grade")
def ubah(muatan: MuatanAturan, grade_id: int = GRADE):
    """
    Divalidasi sebelum ditulis.

    Perubahan digabungkan dulu ke salinan konfigurasi, seluruhnya diperiksa,
    baru disimpan — sehingga konfigurasi yang merusak tidak pernah sempat masuk
    ke basis data. Yang paling penting dijaga adalah grade yang menjadi TIDAK
    PERNAH TERCAPAI: kalau ambang B dibuat sama ketat dengan A, semua berkas
    tertangkap di A lebih dulu dan B mati tanpa pesan galat apa pun.

    `matching.weights` dan `matching.missingElements` MENGGANTI seluruh isinya
    (bobot harus berjumlah 100). `matching.nameCleaning` digabung per sakelar.
    Nilai `null` pada ketiganya = kembali ke bawaan grade.

    `warnings` menjelaskan akibat konfigurasi yang sah tapi mungkin tak
    disangka, mis. pita REVIEW yang tidak mungkin tercapai. Tidak menolak.

    `dryRun: true` menjalankan seluruh validasi tanpa menulis — untuk tombol
    "periksa" di UI sebelum benar-benar menyimpan.

    KODE STATUS. Badan balasannya identik dengan versi Langflow dalam segala
    keadaan, jadi kode UI yang memeriksa `applied` tetap jalan apa adanya. Yang
    ditambahkan hanyalah 409 saat validasi menolak, supaya klien yang membaca
    kode status pun tahu tanpa harus mengurai badan. Langflow tidak bisa
    membedakan ini: ditolak atau diterima, jawabannya sama-sama 200.
    """
    try:
        with pinjam() as con:
            hasil = perbarui(
                con, grade_id,
                {"criteria": muatan.criteria, "score": muatan.score,
                 "matching": muatan.matching},
                oleh=muatan.updatedBy,
                dry_run=muatan.dryRun,
            )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _jawab(hasil, f"grade {grade_id}", muatan.dryRun)


@rute.patch("/global", summary="Ubah nilai global grading & matching")
def ubah_global(muatan: MuatanGlobal):
    """
    `grading.scoreWeights` (boleh sebagian; jumlah harus 1),
    `grading.gradeECombinations` (diganti utuh), `matching.conflictEpsilon`,
    `matching.contradictionJw`. Nilai `null` = hapus setelan, kembali ke
    env/bawaan. `dryRun`, `warnings`, dan kode status sama dengan PATCH rules.
    """
    try:
        with pinjam() as con:
            hasil = perbarui_global(
                con, {"grading": muatan.grading, "matching": muatan.matching},
                oleh=muatan.updatedBy, dry_run=muatan.dryRun)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _jawab(hasil, "global", muatan.dryRun)


@rute.get("/history", summary="Riwayat perubahan konfigurasi")
def riwayat(
    gradeId: int | None = Query(None, ge=1, le=6,
                                description="Hanya perubahan grade ini"),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """Siapa mengubah apa, kapan, dari berapa ke berapa — terbaru lebih dulu."""
    with pinjam() as con:
        return {"items": baca_riwayat(con, gradeId, limit)}


@rute.get("/versions/{versi}", summary="Isi konfigurasi satu versi")
def versi(versi: str = Path(pattern="^[0-9a-f]{12}$",
                            description="`configVersion` dari hasil job")) -> dict:
    """
    Konfigurasi lengkap yang dipakai sebuah job. `configVersion` tercatat di
    hasil grading, di callback & `blocking_metrics` matching, dan di riwayat.
    """
    with pinjam() as con:
        hasil = baca_versi(con, versi)
    if not hasil:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Versi konfigurasi {versi} tidak dikenal.")
    return hasil
