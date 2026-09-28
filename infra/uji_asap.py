"""
Uji asap: setiap endpoint dijalankan sekali, lalu hasilnya DIBANDINGKAN dengan
Langflow.

    docker compose exec synchrono-service python infra/uji_asap.py
    docker compose exec synchrono-service python infra/uji_asap.py --tanpa-langflow

KENAPA PERBANDINGANNYA WAJIB, BUKAN TAMBAHAN

Benchmark yang membandingkan dua sistem hanya berarti kalau keduanya
mengerjakan hal yang sama. Service yang diam-diam melewatkan pemeriksaan
wilayah, atau memakai rapidfuzz yang tidak terpasang, akan tampak lebih cepat —
dan itu bukan kemenangan, itu pengukuran yang salah. Jadi sebelum k6 dijalankan,
jalankan ini: kalau muatan hasil grading kedua sisi tidak identik, angka
apa pun yang keluar dari k6 tidak bisa dipakai.

Yang dibandingkan adalah muatan callback grading — bagian yang paling banyak
menyentuh logika (normalisasi kolom, pembersihan NIK, pembersihan nama,
rujukan wilayah, skor, grade).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

SERVICE = "http://127.0.0.1:8000"
LANGFLOW = "http://synchrono-langflow:7860"

KUNCI_SERVICE = "synchrono-bench-key"

BERKAS = "uji-b"
BUCKET = "syncrono-uploads"
KEY = f"uploads/{BERKAS}/data.parquet"

# Node id flow grading bersifat tetap — diturunkan dari (nama endpoint, nama
# komponen), jadi membangun ulang flow tidak mengubahnya.
NODE_DISPATCH = "GradingDispatch-a3967"
NODE_STATUS = "GradingStatus-3cc03"
NODE_RULES = "GradingRuleGet-9c9c5"


def minta(url: str, metode: str = "GET", badan: dict | None = None,
          header: dict | None = None, timeout: int = 120) -> tuple[int, object]:
    data = json.dumps(badan).encode() if badan is not None else None
    h = {"Content-Type": "application/json", **(header or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=metode)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            isi = r.read().decode()
            return r.status, (json.loads(isi) if isi else None)
    except urllib.error.HTTPError as e:
        isi = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(isi)
        except json.JSONDecodeError:
            return e.code, isi


def h_service() -> dict:
    return {"x-api-key": KUNCI_SERVICE}


# ── Bagian 1: setiap endpoint sekali ───────────────────────────────────────

LULUS = []


def periksa(nama: str, didapat: int, diharap: int, catatan: str = "") -> bool:
    ok = didapat == diharap
    LULUS.append(ok)
    tanda = "  ok  " if ok else " GAGAL"
    print(f"[{tanda}] {nama:<46s} {didapat} (harap {diharap})"
          + (f"  {catatan}" if catatan else ""))
    return ok


def uji_service() -> dict | None:
    print("\n== SERVICE ==")

    kode, isi = minta(f"{SERVICE}/health")
    periksa("GET  /health", kode, 200, f"mode={isi.get('duckdbMode')}")

    kode, isi = minta(f"{SERVICE}/health/db")
    periksa("GET  /health/db", kode, 200, f"{isi.get('roundtripMs')} ms")

    kode, _ = minta(f"{SERVICE}/api/v1/config/rules")
    periksa("GET  /config/rules  (tanpa kunci)", kode, 401)

    kode, isi = minta(f"{SERVICE}/api/v1/config/rules", header=h_service())
    periksa("GET  /config/rules", kode, 200,
            f"{len(isi.get('grades', []))} grade")

    kode, _ = minta(f"{SERVICE}/api/v1/config/rules/2", header=h_service())
    periksa("GET  /config/rules/2", kode, 200)

    kode, _ = minta(f"{SERVICE}/api/v1/config/rules/9", header=h_service())
    periksa("GET  /config/rules/9  (di luar 1-6)", kode, 422)

    kode, isi = minta(f"{SERVICE}/api/v1/config/rules/2", "PATCH",
                      {"criteria": {"minCompleteness": {"tempat_lahir": 0.75}},
                       "dryRun": True}, h_service())
    periksa("PATCH /config/rules/2  (dry run sah)", kode, 200,
            f"{len(isi.get('changed') or [])} field berubah")

    kode, isi = minta(f"{SERVICE}/api/v1/config/rules/2", "PATCH",
                      {"criteria": {"minCompleteness": {"nama": 1.5}},
                       "dryRun": True}, h_service())
    periksa("PATCH /config/rules/2  (ambang > 1)", kode, 409,
            str((isi or {}).get("problems", ["?"])[0])[:60])

    kode, isi = minta(f"{SERVICE}/api/v1/config/rules/2", "PATCH",
                      {"score": {"min": 95, "max": 10}, "dryRun": True},
                      h_service())
    periksa("PATCH /config/rules/2  (pita terbalik)", kode, 409,
            str((isi or {}).get("problems", ["?"])[0])[:60])

    kode, isi = minta(f"{SERVICE}/api/v1/grading/jobs", "POST",
                      {"s3Bucket": "x"}, h_service())
    periksa("POST /grading/jobs  (tanpa fileId)", kode, 400,
            str((isi or {}).get("detail", ""))[:60])

    kode, isi = minta(f"{SERVICE}/api/v1/grading/jobs/tidak-ada-berkas",
                      header=h_service())
    periksa("GET  /grading/jobs/<tak ada>", kode, 200,
            f"found={(isi or {}).get('found')}")

    # Yang sesungguhnya: kirim job, tunggu selesai.
    kode, isi = minta(f"{SERVICE}/api/v1/grading/jobs", "POST",
                      {"fileId": BERKAS, "s3Bucket": BUCKET, "parquetKey": KEY},
                      h_service())
    periksa("POST /grading/jobs", kode, 202, isi.get("jobId", ""))
    job_id = (isi or {}).get("jobId")

    hasil = None
    for _ in range(60):
        time.sleep(1)
        kode, isi = minta(f"{SERVICE}/api/v1/grading/jobs/by-id/{job_id}",
                          header=h_service())
        if isi and isi.get("done"):
            hasil = isi
            break

    if hasil is None:
        periksa("GET  /grading/jobs/by-id/… sampai selesai", 0, 200, "timeout")
        return None

    r = hasil.get("result") or {}
    s = r.get("summary") or {}
    periksa("GET  /grading/jobs/by-id/…", hasil["status"] == "COMPLETED" and 200
            or 0, 200,
            f"grade {s.get('gradeLetter')} skor {s.get('qualityScore')} "
            f"{hasil.get('gradingDurationMs')} ms")

    uji_muatan_portal()
    uji_matching()
    return r


def uji_muatan_portal() -> None:
    """
    Muatan berbentuk PERSIS seperti yang dikirim portal, dengan dua keanehannya.

    Portal mengirim `rawSourceKey` (berkas sungguhan), tapi juga `parquetKey`
    yang isinya SAMA dengan `enrichedParquetKey` — nama berkas keluaran, bukan
    masukan. Dan `s3Endpoint` diisi `http://localhost:8333`, yang dari dalam
    container menunjuk container itu sendiri.

    Keduanya sudah ditangani engine, dan uji ini yang menjaganya tetap begitu.
    Tanpa uji ini, perubahan di `_pilih_sumber` bisa membuat portal gagal lagi
    tanpa ada yang menyadarinya sampai berkas berikutnya diunggah.
    """
    muatan = {
        "fileId": f"{BERKAS}-portal",
        "filename": "uji_gradeB.csv",
        "s3Bucket": BUCKET,
        "s3Endpoint": "http://localhost:8333",      # sengaja salah
        "rawSourceKey": KEY,                        # berkas yang sungguhan ada
        "parquetKey": f"uploads/{BERKAS}-portal/enriched.parquet",   # = keluaran
        "enrichedParquetKey": f"uploads/{BERKAS}-portal/enriched.parquet",
        "institution": {"id": "inst-001", "name": "Kemensos"},
    }
    kode, isi = minta(f"{SERVICE}/api/v1/grading/jobs", "POST", muatan, h_service())
    periksa("POST /grading/jobs  (bentuk muatan portal)", kode, 202,
            (isi or {}).get("jobId", ""))
    job_id = (isi or {}).get("jobId")
    if not job_id:
        return

    for _ in range(60):
        time.sleep(1)
        kode, isi = minta(f"{SERVICE}/api/v1/grading/jobs/by-id/{job_id}",
                          header=h_service())
        if isi and isi.get("done"):
            break

    status = (isi or {}).get("status")
    galat = (isi or {}).get("error") or ""
    periksa("  muatan portal selesai COMPLETED",
            200 if status == "COMPLETED" else 0, 200, galat[:70])


# Berkas produksi yang sudah pernah dicocokkan, jadi hasilnya sudah diketahui:
# 200.000 baris -> 199.386 cocok, 0 perlu tinjauan, 614 tidak cocok. Angka itu
# yang dibandingkan, bukan sekadar "balasannya 200".
MATCHING = {
    "fileId": "d88150c5",
    "parquetPath": ("s3://synchrono/curated/"
                    "20260908_100245_d88150c5_data_dukcapil_gradeA.parquet"),
    "grade": 1,
    "pakaiEnriched": True,
    # WAJIB true di sini. Tanpanya satu uji asap menulis 200 ribu baris ke
    # tabel `institution`.
    "dryRun": True,
}
HARAP_MATCHING = (200000, 199386, 0, 614)


def uji_matching() -> None:
    kode, isi = minta(f"{SERVICE}/api/v1/matching/run", "POST", MATCHING,
                      h_service(), timeout=600)
    if kode != 200 or not isinstance(isi, dict):
        periksa("POST /matching/run", kode, 200, str(isi)[:70])
        return

    dapat = (isi.get("processed_rows"), isi.get("matched_rows"),
             isi.get("manual_review_rows"), isi.get("unmatched_rows"))
    catatan = (f"{dapat[0]:,} baris -> {dapat[1]:,}/{dapat[2]}/{dapat[3]} "
               f"dalam {isi.get('total_seconds')} dtk")
    periksa("POST /matching/run", kode, 200, catatan)
    periksa("  hasil matching sesuai yang diketahui",
            200 if dapat == HARAP_MATCHING else 0, 200,
            "" if dapat == HARAP_MATCHING else f"harap {HARAP_MATCHING}")


# ── Bagian 2: Langflow, lalu dibandingkan ──────────────────────────────────

def kunci_langflow() -> str | None:
    """Login sebagai superuser, lalu buat API key sekali pakai."""
    try:
        data = b"username=admin&password=synchrono123"
        req = urllib.request.Request(
            f"{LANGFLOW}/api/v1/login", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            token = json.loads(r.read())["access_token"]
    except Exception as e:  # noqa: BLE001
        print(f"  login Langflow gagal: {type(e).__name__}: {e}")
        return None

    kode, isi = minta(f"{LANGFLOW}/api/v1/api_key/", "POST",
                      {"name": f"uji-asap-{int(time.time())}"},
                      {"Authorization": f"Bearer {token}"})
    if kode != 200 or not isinstance(isi, dict):
        print(f"  pembuatan API key gagal: {kode} {isi}")
        return None
    return isi.get("api_key")


def lewat_langflow(endpoint: str, node: str, param: dict, kunci: str):
    """
    Panggil satu flow Langflow dan kupas selubungnya.

    Hasil yang berguna terkubur di outputs[0].outputs[0].results.message.text,
    dan isinya masih berupa STRING yang perlu diurai lagi. Selubung ini bawaan
    Langflow — dan ia salah satu hal yang hilang begitu pindah ke service.
    """
    kode, isi = minta(
        f"{LANGFLOW}/api/v1/run/{endpoint}?stream=false", "POST",
        {"output_type": "chat", "input_type": "text", "input_value": "",
         "tweaks": {node: param}},
        {"x-api-key": kunci}, timeout=300)
    if kode != 200:
        return kode, isi
    try:
        teks = isi["outputs"][0]["outputs"][0]["results"]["message"]["text"]
        return kode, json.loads(teks)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        return kode, {"__gagal_dikupas__": str(e), "mentah": isi}


def uji_langflow() -> dict | None:
    print("\n== LANGFLOW ==")
    kunci = kunci_langflow()
    if not kunci:
        print("  dilewati — Langflow tidak terjangkau atau kredensialnya beda.")
        return None

    kode, isi = lewat_langflow("config-rules", NODE_RULES, {"grade_id": ""}, kunci)
    periksa("POST /api/v1/run/config-rules", kode, 200,
            f"{len(isi.get('grades', []))} grade" if isinstance(isi, dict) else "")

    kode, isi = lewat_langflow("grading-dispatch", NODE_DISPATCH, {
        "file_id": BERKAS, "s3_bucket": BUCKET, "parquet_key": KEY,
        "s3_endpoint": "", "callback_url": "", "callback_token": ""}, kunci)
    periksa("POST /api/v1/run/grading-dispatch", kode, 200,
            (isi or {}).get("jobId", ""))
    job_id = (isi or {}).get("jobId")

    for _ in range(60):
        time.sleep(1)
        kode, isi = lewat_langflow("grading-status", NODE_STATUS,
                                   {"job_id": job_id, "file_id": ""}, kunci)
        if isinstance(isi, dict) and isi.get("done"):
            r = isi.get("result") or {}
            s = r.get("summary") or {}
            periksa("POST /api/v1/run/grading-status", kode, 200,
                    f"grade {s.get('gradeLetter')} skor {s.get('qualityScore')} "
                    f"{isi.get('gradingDurationMs')} ms")
            return r

    periksa("POST /api/v1/run/grading-status sampai selesai", 0, 200, "timeout")
    return None


# ── Bagian 3: apakah hasilnya benar-benar sama? ────────────────────────────

# Dua field yang memang TIDAK BOLEH dituntut sama:
#
#   gradingDurationMs   justru inilah yang sedang diukur.
#
#   parquetSizeBytes    DuckDB menulis parquet secara paralel, dan pembagian
#                       row group ikut menentukan hasil kompresi. Diuji: dua
#                       kali menjalankan grading yang SAMA di service yang SAMA
#                       menghasilkan 124.318 dan 124.410 byte. Isinya dibuktikan
#                       identik — `EXCEPT ALL` ke dua arah mengembalikan nol
#                       baris — dan selisih per kolomnya berayun ke dua arah
#                       (nik -66, nik_hari +74). Jadi ini derau kompresi, bukan
#                       perbedaan data. Menuntutnya sama akan membuat uji ini
#                       gagal secara acak dan lama-lama diabaikan orang.
ABAIKAN = {"gradingDurationMs", "parquetSizeBytes"}


def ratakan(o, awalan: str = "") -> dict:
    if isinstance(o, dict):
        hasil = {}
        for k, v in o.items():
            hasil.update(ratakan(v, f"{awalan}.{k}" if awalan else k))
        return hasil
    if isinstance(o, list):
        return {awalan: json.dumps(o, sort_keys=True, ensure_ascii=False)}
    return {awalan: o}


def bandingkan(a: dict, b: dict) -> None:
    print("\n== PERBANDINGAN MUATAN HASIL ==")
    ra, rb = ratakan(a), ratakan(b)
    kunci = sorted(set(ra) | set(rb))
    beda = []
    for k in kunci:
        if k.split(".")[-1] in ABAIKAN:
            continue
        if ra.get(k) != rb.get(k):
            beda.append((k, ra.get(k), rb.get(k)))

    print(f"  {len(kunci)} field dibandingkan, {len(ABAIKAN)} diabaikan "
          f"(waktu proses)")
    if not beda:
        LULUS.append(True)
        print("  [  ok  ] IDENTIK — benchmark-nya sah.")
        return

    LULUS.append(False)
    print(f"  [ GAGAL] {len(beda)} field BERBEDA. Angka benchmark tidak bisa "
          f"dipakai sampai ini beres:\n")
    for k, va, vb in beda[:25]:
        print(f"    {k}")
        print(f"        service : {str(va)[:90]}")
        print(f"        langflow: {str(vb)[:90]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tanpa-langflow", action="store_true",
                   help="Uji service saja, tanpa membandingkan.")
    args = p.parse_args()

    hasil_service = uji_service()
    if not args.tanpa_langflow:
        hasil_langflow = uji_langflow()
        if hasil_service and hasil_langflow:
            bandingkan(hasil_service, hasil_langflow)

    total, lulus = len(LULUS), sum(LULUS)
    print(f"\n{'=' * 62}\n{lulus}/{total} pemeriksaan lulus")
    return 0 if lulus == total else 1


if __name__ == "__main__":
    sys.exit(main())
