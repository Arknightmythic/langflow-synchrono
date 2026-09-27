"""
Simulasi portal -> engine -> portal, persis spesifikasi integrasi matching.

  1. portal membuat baris syncrono_matching_job (PENDING)        §1 langkah 1
  2. portal POST matching-dispatch, body & header persis §3      §3
  3. engine membalas IN_PROGRESS                                  §3.3
  4. engine: matching -> result.parquet -> suntik DB portal       §4, §5
  5. engine: callback ke portal                                   §7

Prasyarat:
  * database `portal_sim` dengan skema.sql (lihat MATCHING.md)
  * penerima callback jalan:  python penerima_callback.py
  * berkas incoming sudah digrading lewat grading-dispatch (grade dicari
    engine dari grading_jobs), dan master ada di S3

Menjalankan (dari host):
  set LANGFLOW_API_KEY=sk-...
  uv run --with "psycopg[binary]" python simulasi.py sim-grade-b sim-grade-d
"""
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import psycopg

BASE = os.getenv("LANGFLOW_URL", "http://localhost:7860")
KUNCI = os.getenv("LANGFLOW_API_KEY", "")
PORTAL = os.getenv("PORTAL_SIM_DSN",
                   "host=localhost port=5432 dbname=portal_sim user=postgres client_encoding=UTF8")
MASTER_ID = os.getenv("MASTER_FILE_ID", "md-master-uji-299k")
BUCKET = os.getenv("S3_BUCKET", "bucket-test")
# Alamat yang dipakai ENGINE untuk menjangkau penerima callback. Dari dalam
# container, host terlihat sebagai host.docker.internal.
CALLBACK = os.getenv("CALLBACK_URL",
                     "http://host.docker.internal:3999/api/internal/matching/callback")


def buat_job(file_id: str) -> str:
    job_id = str(uuid.uuid4())
    with psycopg.connect(PORTAL, autocommit=True) as c:
        c.execute("""INSERT INTO syncrono_matching_job
                     (id, file_id, master_file_id, status, created_by)
                     VALUES (%s, %s, %s, 'PENDING', 'admin@dukcapil.go.id')""",
                  (job_id, file_id, MASTER_ID))
    return job_id


def dispatch(job_id: str, file_id: str) -> dict:
    payload = {
        "jobId": job_id, "fileId": file_id, "masterFileId": MASTER_ID,
        "actor": "admin@dukcapil.go.id", "callbackUrl": CALLBACK,
        "s3Bucket": BUCKET,
        # Sengaja persis contoh spesifikasi — engine harus mengabaikannya.
        "s3Endpoint": "http://localhost:8333",
        "incomingFile": {"fileId": file_id, "filename": "data.csv",
                         "s3Key": f"uploads/{file_id}/enriched.parquet", "format": "parquet"},
        "masterDataFile": {"masterFileId": MASTER_ID, "filename": f"{MASTER_ID}.parquet",
                           "s3Key": f"master-data/{MASTER_ID}.parquet", "format": "parquet"},
        "rulePreset": "FAST",
    }
    badan = {"output_type": "chat", "input_type": "chat", "input_value": "",
             "tweaks": {"MatchingDispatch-b4819": {"payload": json.dumps(payload)}}}
    req = urllib.request.Request(
        f"{BASE}/api/v1/run/matching-dispatch?stream=false",
        data=json.dumps(badan).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-api-key": KUNCI,
                 "Authorization": f"Bearer {KUNCI}",   # persis §3: dua header
                 "Accept-Encoding": "identity"})
    try:
        d = urllib.request.urlopen(req, timeout=120).read()
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_isi": e.read().decode(errors="replace")[:300]}
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    return json.loads(json.loads(d)["outputs"][0]["outputs"][0]["results"]["message"]["text"])


def tunggu(job_id: str, batas_detik: int = 900) -> tuple:
    for _ in range(batas_detik // 2):
        with psycopg.connect(PORTAL) as c:
            r = c.execute("""SELECT status, auto_count, review_count, unmatch_count,
                             conflict_count, pass1_count, pass2_count, scoring_count,
                             result_parquet_key, last_error,
                             (SELECT count(*) FROM syncrono_matching_result WHERE job_id = %s)
                             FROM syncrono_matching_job WHERE id = %s""",
                          (job_id, job_id)).fetchone()
        if r[0] in ("COMPLETED", "FAILED", "CANCELLED"):
            return r
        time.sleep(2)
    return r


if __name__ == "__main__":
    if not KUNCI:
        raise SystemExit("LANGFLOW_API_KEY belum diisi.")
    for berkas in sys.argv[1:] or ["sim-grade-b"]:
        job_id = buat_job(berkas)
        balas = dispatch(job_id, berkas)
        print(f"\n{berkas}  job {job_id}")
        print(f"  dispatch -> {balas}")
        if balas.get("status") != "IN_PROGRESS":
            continue
        r = tunggu(job_id)
        if r[0] == "COMPLETED":
            print(f"  portal   -> COMPLETED  AUTO {r[1]:,}  REVIEW {r[2]:,}  UNMATCH {r[3]:,}  "
                  f"CONFLICT {r[4]:,}")
            print(f"              pass1 {r[5]:,}  pass2 {r[6]:,}  scoring {r[7]:,}")
            print(f"              {r[10]:,} baris hasil | s3://{BUCKET}/{r[8]}")
        else:
            print(f"  portal   -> {r[0]}  {r[9] or ''}")
