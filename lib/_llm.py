"""
Klien LLM minimal, OpenAI-compatible.

Dipakai HANYA sebagai jaring terakhir pengenalan kolom, setelah seluruh lapis
deterministik gagal. Satu panggilan per berkas, bukan per baris.

DUA ENDPOINT, DAN PERBEDAANNYA PENTING

    OLLAMA_LOCAL_BASE_URL   http://172.16.12.98:11434   on-prem
    OLLAMA_CLOUD_BASE_URL   https://ollama.com          internet publik

Keduanya sama-sama "Ollama", tapi yang kedua adalah layanan hosted milik pihak
ketiga. Mengirim contoh nilai ke sana berarti NIK, nama, dan nama ibu kandung
keluar dari jaringan — itu keputusan yang harus diambil sadar, bukan efek
samping dari nilai environment yang kebetulan terisi.

Karena itu `kirim_sampel` DITOLAK pada endpoint non-lokal kecuali ada
persetujuan eksplisit lewat NORMALISASI_AI_IZIN_SAMPEL_LUAR=1.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

# off        : tidak memanggil AI sama sekali
# nama_saja  : AI hanya menerima nama kolom (bawaan)
# dengan_sampel : AI juga menerima beberapa contoh nilai
MODE = os.getenv("NORMALISASI_AI", "nama_saja").strip().lower()

MODEL = os.getenv("NORMALISASI_AI_MODEL", "gemma4:31b")
TIMEOUT = int(os.getenv("NORMALISASI_AI_TIMEOUT", "60"))
JUMLAH_SAMPEL = int(os.getenv("NORMALISASI_AI_SAMPEL", "5"))

# Panggilan AI sesekali gagal sendiri; tanpa percobaan ulang, grade
# berkas yang sama bisa berbeda antar-run.
PERCOBAAN = int(os.getenv("NORMALISASI_AI_PERCOBAAN", "3"))
JEDA_ULANG = float(os.getenv("NORMALISASI_AI_JEDA", "1.5"))

_LOKAL = os.getenv("OLLAMA_LOCAL_BASE_URL", "").strip().strip('"')
_CLOUD = os.getenv("OLLAMA_CLOUD_BASE_URL", "").strip().strip('"')

BASE_URL = (os.getenv("NORMALISASI_AI_BASE_URL", "").strip().strip('"')
            or _LOKAL or _CLOUD)

API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")

IZIN_SAMPEL_LUAR = os.getenv("NORMALISASI_AI_IZIN_SAMPEL_LUAR", "0") == "1"


def aktif() -> bool:
    return MODE != "off" and bool(BASE_URL)


def alasan_mati() -> str:
    """Kenapa AI tidak dipanggil — dibedakan supaya bisa diperbaiki."""
    if MODE == "off":
        return "NORMALISASI_AI=off"
    if not BASE_URL:
        return ("endpoint AI belum dikonfigurasi — setel NORMALISASI_AI_BASE_URL "
                "atau OLLAMA_LOCAL_BASE_URL di environment container")
    return "aktif"


def _lokal(url: str) -> bool:
    """Alamat privat/loopback dianggap on-prem."""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    return bool(
        host in ("localhost", "127.0.0.1", "host.docker.internal")
        or re.match(r"^10\.", host)
        or re.match(r"^192\.168\.", host)
        or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host)
    )


def boleh_kirim_sampel() -> tuple[bool, str]:
    """(boleh, alasan). Alasan selalu diisi supaya bisa dicatat di jejak."""
    if MODE != "dengan_sampel":
        return False, f"mode AI '{MODE}' — contoh nilai tidak dikirim"
    if _lokal(BASE_URL):
        return True, f"endpoint lokal ({BASE_URL}) — contoh nilai boleh dikirim"
    if IZIN_SAMPEL_LUAR:
        return True, (f"endpoint LUAR ({BASE_URL}) dengan izin eksplisit "
                      "NORMALISASI_AI_IZIN_SAMPEL_LUAR=1")
    return False, (
        f"endpoint {BASE_URL} BUKAN jaringan lokal. Contoh nilai tidak dikirim "
        "karena akan membawa NIK dan nama keluar jaringan. Arahkan "
        "NORMALISASI_AI_BASE_URL ke Ollama on-prem, atau setel "
        "NORMALISASI_AI_IZIN_SAMPEL_LUAR=1 kalau itu memang disengaja."
    )


def tanya(prompt: str) -> dict:
    """
    Satu panggilan chat. Mengembalikan {'teks', 'detik', 'token', 'model'}.

    DICOBA ULANG kalau gagal sesaat, dan ini bukan kemewahan. Saat menjalankan
    tujuh berkas beruntun, satu panggilan pernah gagal sendiri — berkasnya lalu
    kehilangan dua elemen dan turun grade, padahal berkas dan modenya sama
    persis dengan run sebelumnya yang berhasil. Grade yang berubah-ubah antar
    percobaan jauh lebih merepotkan daripada grade yang salah secara konsisten.

    Galat 4xx selain 429 tidak diulang — muatannya yang bermasalah, bukan
    jaringannya.
    """
    if not BASE_URL:
        raise RuntimeError(
            "Endpoint AI belum dikonfigurasi. Setel NORMALISASI_AI_BASE_URL "
            "atau OLLAMA_LOCAL_BASE_URL."
        )

    badan = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "stream": False,
    }).encode()

    galat = None
    mulai = time.perf_counter()

    for percobaan in range(1, PERCOBAAN + 1):
        req = urllib.request.Request(
            BASE_URL.rstrip("/") + "/v1/chat/completions",
            data=badan,
            headers={"Authorization": f"Bearer {API_KEY}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                hasil = json.loads(r.read())
            pakai = hasil.get("usage") or {}
            return {
                "teks": hasil["choices"][0]["message"]["content"],
                "detik": round(time.perf_counter() - mulai, 2),
                "token": pakai.get("total_tokens"),
                "model": MODEL,
                "endpoint": BASE_URL,
                "percobaan": percobaan,
            }
        except urllib.error.HTTPError as e:
            galat = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001 — timeout, DNS, koneksi putus
            galat = f"{type(e).__name__}: {e}"

        if percobaan < PERCOBAAN:
            time.sleep(JEDA_ULANG * percobaan)

    raise RuntimeError(f"AI gagal setelah {PERCOBAAN} percobaan — {galat}")


def urai_json(teks: str) -> dict:
    """
    Ambil objek JSON dari balasan model.

    Model kerap membungkusnya dalam pagar ```json meski diminta JSON polos,
    jadi pagar itu dilepas lebih dulu, dan sebagai upaya terakhir objek `{...}`
    pertama dicomot dari teks bebas.
    """
    isi = teks.strip()
    if isi.startswith("```"):
        potong = isi.split("```")
        if len(potong) >= 2:
            isi = potong[1]
            if isi.lower().startswith("json"):
                isi = isi[4:]
    isi = isi.strip()

    try:
        return json.loads(isi)
    except json.JSONDecodeError:
        pass

    m = re.search(r"\{.*\}", isi, re.S)
    if not m:
        raise ValueError(f"Balasan AI tidak memuat JSON: {teks[:200]!r}")
    return json.loads(m.group(0))
