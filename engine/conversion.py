import json
import os
import urllib.error
import urllib.request

from . import settings as cfg
from .grading import EXECUTABLE_DUMP

ENGINE_BY_EXTENSION = {".sql": "postgresql", ".mdf": "sqlserver", ".dmp": "oracle"}
ENGINE_BY_DIALECT = {"mysql": "mysql", "mariadb": "mysql"}
PROFILES = {"sqlserver": "mssql", "oracle": "oracle", "mysql": "mysql"}


def needs_conversion(job: dict) -> str | None:
    key = (job.get("raw_source_key") or job.get("csv_key") or "").strip()
    return key if key and EXECUTABLE_DUMP.search(key) else None


def _send(engine: str, ext: str, body: dict) -> dict:
    address = cfg.CONVERTER_URLS[engine]
    request = urllib.request.Request(f"{address.rstrip('/')}/konversi",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=cfg.CONVERTER_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        try:
            return {**json.loads(e.read()), "ok": False}
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    except urllib.error.URLError as e:
        profile = PROFILES.get(engine)
        hint = (f" Layanan ini ada di balik profil compose `{profile}` dan TIDAK "
                f"ikut naik dengan `up -d` biasa. Nyalakan dengan:\n"
                f"    docker compose -f docker-compose.server.yml --env-file .env "
                f"--profile {profile} up -d --build konverter-{profile}") if profile else ""
        raise RuntimeError(
            f"Layanan konversi {engine} untuk berkas {ext} tidak bisa dihubungi "
            f"di {address} ({e.reason}). Format lain (parquet, CSV, xlsx) tidak "
            f"membutuhkannya dan tetap bisa digrading.{hint}") from e


def convert_first(job: dict, report=lambda stage: None) -> dict | None:
    source = needs_conversion(job)
    if not source:
        return None
    file_id = job["file_id"]
    target = f"uploads/{file_id}/source.parquet"
    ext = os.path.splitext(source)[1].lower()
    engine = ENGINE_BY_EXTENSION.get(ext)
    if not engine:
        raise RuntimeError(f"Belum ada layanan konversi untuk berkas {ext}. Yang sudah: "
                           f"{', '.join(sorted(ENGINE_BY_EXTENSION))}.")
    dialect = str(job.get("sql_dialect") or "").strip().lower() or None
    if ext == ".sql" and dialect in ENGINE_BY_DIALECT:
        engine = ENGINE_BY_DIALECT[dialect]
    body = {"jobId": job["job_id"], "fileId": file_id, "s3Bucket": job["s3_bucket"],
            "sourceKey": source, "targetKey": target, "dialect": dialect,
            "table": job.get("source_table"), "logKey": job.get("log_key")}

    report(f"K0 kirim ke layanan konversi ({ext}, {engine})")
    result = _send(engine, ext, body)
    redirect = ENGINE_BY_DIALECT.get(str(result.get("dialek") or "").lower())
    if not result.get("ok") and engine == "postgresql" and redirect:
        report(f"K0 dump berdialek {result['dialek']} — dibelokkan ke konverter {redirect}")
        engine, body["dialect"] = redirect, result["dialek"]
        result = _send(engine, ext, body)
    if not result.get("ok"):
        raise RuntimeError(f"Konversi gagal: {result.get('error') or 'tanpa sebab'}")

    job["parquet_key"] = target
    job["raw_source_key"] = None
    job["csv_key"] = None
    print(f"[K] {job['job_id']} conversion done ({engine}): {result['row_count']:,} rows "
          f"from table '{result['tabel']}' -> {target}", flush=True)
    return result
