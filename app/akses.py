"""
Autentikasi — tiruan persis Langflow, supaya portal tidak perlu berubah.

Portal mendapatkan `x-api-key` dengan cara Langflow (langflow-synchrono/API.md
bagian 1), dan cara yang sama berlaku di sini:

    POST /api/v1/login          form: username, password  -> access_token
    POST /api/v1/api_key/       Bearer access_token       -> {"api_key": "sk-..."}
    GET  /api/v1/api_key/       Bearer                    -> daftar (tersamar)
    DELETE /api/v1/api_key/{id} Bearer                    -> {"detail": "API Key deleted"}
    POST /api/v1/run/...        x-api-key (header ATAU query)

Setiap balasan galat — kode status dan teks `detail` — direkam dari Langflow
yang jalan, bukan ditebak. Lihat `rute_langflow.py`.

DUA SUMBER KUNCI

  1. `SERVICE_API_KEY` di environment (boleh beberapa, dipisah koma). Isi
     dengan kunci `sk-...` yang SEKARANG dipakai portal untuk Langflow, dan
     portal bisa dipindah ke service ini tanpa mengubah apa pun — termasuk
     setelan kuncinya.
  2. Kunci yang dibuat lewat `/api/v1/api_key/`, disimpan di tabel
     `service_api_keys` di PostgreSQL engine. Yang disimpan hanya SHA-256-nya;
     nilai kunci hanya muncul sekali, di balasan pembuatannya — sama seperti
     Langflow.

Pemeriksaan kunci SELALU aktif, kecuali `SERVICE_AUTH=off` disetel secara
sadar (mesin lokal). Dulu `SERVICE_API_KEY` kosong berarti pemeriksaan mati;
sekarang itu berbahaya, karena kunci dari basis data membuat variabel itu boleh
kosong di server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from datetime import timezone

from fastapi import Header, HTTPException, Query

from _jobs import q
from _kolam import pinjam

PENGGUNA = (os.getenv("SERVICE_SUPERUSER") or os.getenv("LANGFLOW_SUPERUSER")
            or "admin").strip()
SANDI = (os.getenv("SERVICE_SUPERUSER_PASSWORD")
         or os.getenv("LANGFLOW_SUPERUSER_PASSWORD") or "").strip()

# Penanda tangan token login. Kosong = acak per proses: token lama tidak
# berlaku lagi setelah restart (cukup login ulang), sedangkan API key tetap
# berlaku karena disimpan di basis data.
RAHASIA = (os.getenv("SERVICE_SECRET_KEY") or "").strip() or secrets.token_urlsafe(48)

# Umur token: sama dengan Langflow — access 1 jam (terukur dari klaim `exp`
# token Langflow), refresh 7 hari.
UMUR_AKSES = int(os.getenv("SERVICE_ACCESS_TOKEN_SECONDS", "3600"))
UMUR_SEGAR = int(os.getenv("SERVICE_REFRESH_TOKEN_SECONDS", str(7 * 86400)))

KUNCI_STATIS = [k.strip() for k in os.getenv("SERVICE_API_KEY", "").split(",") if k.strip()]
AUTH_AKTIF = os.getenv("SERVICE_AUTH", "on").strip().lower() not in ("off", "0", "false")

ID_PENGGUNA = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synchrono-service/user/{PENGGUNA}"))

# Balasan galat Langflow, terekam 28 Sep 2026.
TANPA_KUNCI = "An API key must be passed as query or header"
KUNCI_SALAH = "Invalid or missing API key"


# ── Token login (JWT HS256, tanpa dependensi tambahan) ──────────────────────

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _tanda(isi: str) -> str:
    return _b64(hmac.new(RAHASIA.encode(), isi.encode(), hashlib.sha256).digest())


def _sama(a: str, b: str) -> bool:
    # Dibandingkan sebagai bytes: compare_digest pada str melempar TypeError
    # untuk karakter non-ASCII, dan masukan pengguna boleh berisi apa saja.
    return hmac.compare_digest(a.encode(), b.encode())


def buat_token(jenis: str, umur: int) -> str:
    kepala = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    klaim = _b64(json.dumps({"sub": ID_PENGGUNA, "type": jenis,
                             "exp": int(time.time()) + umur},
                            separators=(",", ":")).encode())
    return f"{kepala}.{klaim}.{_tanda(f'{kepala}.{klaim}')}"


def token_sah(token: str) -> bool:
    try:
        kepala, klaim, tanda = token.split(".")
        if not _sama(tanda, _tanda(f"{kepala}.{klaim}")):
            return False
        isi = json.loads(_unb64(klaim))
        return (isi.get("type") == "access" and isi.get("sub") == ID_PENGGUNA
                and isi.get("exp", 0) > time.time())
    except Exception:  # noqa: BLE001 — token rusak dalam bentuk apa pun
        return False


def login(pengguna: str, sandi: str) -> dict | None:
    # Tanpa kata sandi di environment, login mati sama sekali — tidak ada
    # "sandi kosong" yang bisa ditebak.
    if not SANDI:
        return None
    if not (_sama(pengguna, PENGGUNA) and _sama(sandi, SANDI)):
        return None
    return {"access_token": buat_token("access", UMUR_AKSES),
            "refresh_token": buat_token("refresh", UMUR_SEGAR),
            "token_type": "bearer"}


# ── Penyimpanan API key ─────────────────────────────────────────────────────
#
# Semua lewat SQL PostgreSQL asli (`postgres_query` / `postgres_execute`),
# bukan tabel `pg.service_api_keys` milik katalog DuckDB. Koneksi di kolam
# menyimpan salinan katalog PostgreSQL saat dibuka; tabel yang dibuat sesudahnya
# tidak terlihat oleh koneksi lain sampai katalognya dibersihkan. SQL asli
# tidak melewati katalog itu sama sekali.

TABEL = "service_api_keys"
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABEL} (
    id           uuid PRIMARY KEY,
    name         text,
    key_hash     text NOT NULL UNIQUE,
    key_prefix   text NOT NULL,
    key_length   integer NOT NULL,
    user_id      uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    total_uses   integer NOT NULL DEFAULT 0,
    is_active    boolean NOT NULL DEFAULT true,
    expires_at   timestamptz
)"""

_tabel_siap = False
_kunci_tabel = threading.Lock()


def _pg(con, sql: str) -> None:
    con.execute(f"CALL postgres_execute('pg', {q(sql)})")


def _baca(con, sql: str) -> list[tuple]:
    return con.execute(f"SELECT * FROM postgres_query('pg', {q(sql)})").fetchall()


def pastikan_tabel(con=None) -> None:
    global _tabel_siap
    if _tabel_siap:
        return
    with _kunci_tabel:
        if _tabel_siap:
            return
        if con is None:
            with pinjam() as c:
                _pg(c, DDL)
        else:
            _pg(con, DDL)
        _tabel_siap = True


def _hash(kunci: str) -> str:
    return hashlib.sha256(kunci.encode()).hexdigest()


def _iso(t) -> str | None:
    # Bentuk waktu Langflow: 2026-09-15T09:43:11+00:00
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def buat_kunci(nama: str | None) -> dict:
    kunci = f"sk-{secrets.token_urlsafe(32)}"
    kid = str(uuid.uuid4())
    with pinjam() as con:
        pastikan_tabel(con)
        _pg(con, f"INSERT INTO {TABEL} (id, name, key_hash, key_prefix, key_length, user_id) "
                 f"VALUES ({q(kid)}, {q(nama)}, {q(_hash(kunci))}, {q(kunci[:8])}, "
                 f"{len(kunci)}, {q(ID_PENGGUNA)})")
    return {"name": nama, "last_used_at": None, "total_uses": 0, "is_active": True,
            "expires_at": None, "id": kid, "api_key": kunci, "user_id": ID_PENGGUNA}


def daftar_kunci() -> dict:
    with pinjam() as con:
        pastikan_tabel(con)
        baris = _baca(con, f"SELECT name, last_used_at, total_uses, is_active, expires_at, "
                           f"CAST(id AS text), key_prefix, key_length, created_at "
                           f"FROM {TABEL} WHERE user_id = {q(ID_PENGGUNA)} ORDER BY created_at")
    kunci = [{
        "name": nama, "last_used_at": _iso(dipakai), "total_uses": int(jumlah or 0),
        "is_active": bool(aktif), "expires_at": _iso(kedaluwarsa), "id": kid,
        # Tersamar persis cara Langflow: 8 karakter pertama, sisanya bintang.
        "api_key": awalan + "*" * (panjang - len(awalan)),
        "user_id": ID_PENGGUNA, "created_at": _iso(dibuat),
    } for nama, dipakai, jumlah, aktif, kedaluwarsa, kid, awalan, panjang, dibuat in baris]
    return {"total_count": len(kunci), "user_id": ID_PENGGUNA, "api_keys": kunci}


def hapus_kunci(kid: str) -> bool:
    try:
        uuid.UUID(kid)
    except ValueError:
        return False
    with pinjam() as con:
        pastikan_tabel(con)
        ada = _baca(con, f"SELECT 1 FROM {TABEL} WHERE id = {q(kid)} "
                         f"AND user_id = {q(ID_PENGGUNA)}")
        if not ada:
            return False
        _pg(con, f"DELETE FROM {TABEL} WHERE id = {q(kid)}")
    # Kunci yang dihapus harus berhenti berlaku SEKETIKA, bukan setelah cache
    # di bawah kedaluwarsa.
    with _kunci_cache:
        for h in [h for h, (k, _) in _cache.items() if k == kid]:
            _cache.pop(h, None)
    return True


# ── Memeriksa x-api-key ─────────────────────────────────────────────────────
#
# Polling status grading memanggil ini berkali-kali per menit. Kunci yang sah
# diingat 60 detik dan yang salah 10 detik, supaya tidak setiap panggilan
# menambah satu perjalanan ke PostgreSQL.

_cache: dict[str, tuple[str | None, float]] = {}
_kunci_cache = threading.Lock()
_pakai: dict[str, int] = {}
_kunci_pakai = threading.Lock()


def _cari(h: str) -> str | None:
    with pinjam() as con:
        pastikan_tabel(con)
        baris = _baca(con, f"SELECT CAST(id AS text) FROM {TABEL} WHERE key_hash = {q(h)} "
                           f"AND is_active AND (expires_at IS NULL OR expires_at > now())")
    return baris[0][0] if baris else None


def periksa_kunci_api(kunci: str | None) -> None:
    if not AUTH_AKTIF:
        return
    if not kunci:
        raise HTTPException(status_code=403, detail=TANPA_KUNCI)
    for statis in KUNCI_STATIS:
        if _sama(kunci, statis):
            return
    h = _hash(kunci)
    sekarang = time.monotonic()
    with _kunci_cache:
        simpan = _cache.get(h)
    if simpan and simpan[1] > sekarang:
        kid = simpan[0]
    else:
        try:
            kid = _cari(h)
        except Exception as e:  # noqa: BLE001
            # Langflow memeriksa kunci di SQLite-nya sendiri, jadi keadaan ini
            # tidak ada padanannya di sana. Galatnya disebut apa adanya.
            raise HTTPException(status_code=500, detail=(
                f"API key tidak bisa diperiksa — PostgreSQL engine tidak "
                f"terjangkau: {type(e).__name__}: {e}")) from e
        with _kunci_cache:
            # Batas ukuran: klien yang mengirim ribuan kunci acak tidak boleh
            # bisa membuat cache ini tumbuh tanpa ujung.
            if len(_cache) >= 10_000:
                _cache.clear()
            _cache[h] = (kid, sekarang + (60 if kid else 10))
    if not kid:
        raise HTTPException(status_code=403, detail=KUNCI_SALAH)
    with _kunci_pakai:
        _pakai[kid] = _pakai.get(kid, 0) + 1


def siram_pemakaian() -> None:
    """`total_uses` & `last_used_at`, dikumpulkan lalu ditulis sekaligus."""
    global _pakai
    with _kunci_pakai:
        data, _pakai = _pakai, {}
    if not data:
        return
    with pinjam() as con:
        for kid, n in data.items():
            _pg(con, f"UPDATE {TABEL} SET total_uses = total_uses + {int(n)}, "
                     f"last_used_at = now() WHERE id = {q(kid)}")


def mulai_penyiram(jeda: float = 30.0) -> None:
    def ulang():
        while True:
            time.sleep(jeda)
            try:
                siram_pemakaian()
            except Exception as e:  # noqa: BLE001 — statistik, bukan jalur utama
                print(f"[akses] pemakaian kunci belum tertulis: {type(e).__name__}: {e}")
    threading.Thread(target=ulang, daemon=True, name="akses-penyiram").start()


# ── Dependensi FastAPI ──────────────────────────────────────────────────────

def dependensi_kunci(
    kunci_header: str | None = Header(default=None, alias="x-api-key"),
    kunci_query: str | None = Query(default=None, alias="x-api-key"),
) -> None:
    """x-api-key dari query ATAU header — Langflow menerima keduanya, query lebih dulu."""
    periksa_kunci_api(kunci_query or kunci_header)


def dependensi_kunci_rest(
    kunci_header: str | None = Header(default=None, alias="x-api-key"),
    kunci_query: str | None = Query(default=None, alias="x-api-key"),
) -> None:
    """
    Untuk endpoint REST service (/api/v1/grading/..., /config/...): kunci yang
    sama, tapi 401 — kontrak REST-nya sejak awal, dan perkakas benchmark
    (infra/uji_asap.py) memeriksanya. Hanya /api/v1/run yang meniru 403 Langflow.
    """
    try:
        periksa_kunci_api(kunci_query or kunci_header)
    except HTTPException as e:
        if e.status_code == 403:
            raise HTTPException(status_code=401, detail=KUNCI_SALAH) from e
        raise


def dependensi_bearer(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=403, detail="No authentication credentials provided")
    if not token_sah(authorization[7:].strip()):
        raise HTTPException(status_code=401, detail="Invalid token")
