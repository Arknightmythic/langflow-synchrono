"""
Uji kecocokan kontrak: permintaan yang SAMA ke Langflow dan ke service ini,
balasannya dibandingkan.

Portal ditulis untuk Langflow. Kalau satu saja dari hal di bawah berbeda, portal
yang dipindah ke service ini bisa berperilaku lain tanpa ada yang tahu:

  * kode status dan teks `detail` galat — persis sama
  * bentuk selubung `/api/v1/run` — setiap jalur kunci sama
  * isi `results.message.text` — sama (dikurangi nilai yang memang berubah
    tiap panggilan: id, waktu)
  * cara mendapatkan kunci: login -> api_key, daftar, hapus

Tidak ada efek samping yang tertinggal: API key sementara dihapus lagi di
kedua sisi, grading-dispatch & matching-dispatch hanya diuji dengan muatan
yang DITOLAK sebelum ada job, dan config-rules-update hanya dengan dryRun.

Menjalankan (dari host; hanya pustaka standar Python):
    python infra/uji_kompat.py

Env (bawaan = stack lokal):
    LANGFLOW_URL=http://localhost:7860   SERVICE_URL=http://localhost:8000
    LANGFLOW_USER / LANGFLOW_PASS        SERVICE_USER / SERVICE_PASS
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

LANGFLOW = os.getenv("LANGFLOW_URL", "http://localhost:7860")
SERVICE = os.getenv("SERVICE_URL", "http://localhost:8000")
AKUN = {
    LANGFLOW: (os.getenv("LANGFLOW_USER", "admin"), os.getenv("LANGFLOW_PASS", "synchrono123")),
    SERVICE: (os.getenv("SERVICE_USER", "admin"), os.getenv("SERVICE_PASS", "synchrono123")),
}

GAGAL: list[str] = []


def cek(benar: bool, pesan: str) -> None:
    print(f"  {'ok   ' if benar else 'GAGAL'} {pesan}")
    if not benar:
        GAGAL.append(pesan)


def minta(base, metode, jalur, data=None, header=None, form=False):
    h = {"Accept-Encoding": "identity", **(header or {})}
    tubuh = None
    if data is not None:
        if form:
            tubuh = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            tubuh = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + jalur, data=tubuh, headers=h, method=metode)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, isi = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, isi = e.code, e.read()
    try:
        return status, json.loads(isi)
    except json.JSONDecodeError:
        return status, isi.decode(errors="replace")


def keduanya(*a, **k):
    return minta(LANGFLOW, *a, **k), minta(SERVICE, *a, **k)


def jalur_kunci(o, awal="") -> set[str]:
    """Semua jalur kunci; indeks list diratakan jadi []."""
    hasil = set()
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{awal}.{k}" if awal else k
            hasil.add(p)
            hasil |= jalur_kunci(v, p)
    elif isinstance(o, list):
        for v in o:
            hasil |= jalur_kunci(v, awal + "[]")
    return hasil


def sama_persis(nama, lf, sv):
    cek(lf == sv, f"{nama}: Langflow {lf[0]} {str(lf[1])[:90]!r} | service {sv[0]} {str(sv[1])[:90]!r}")


# ── 1. Autentikasi ──────────────────────────────────────────────────────────

def uji_auth() -> dict:
    print("\n1. Autentikasi (login -> api_key)")
    sama_persis("login sandi salah",
                *keduanya("POST", "/api/v1/login", {"username": "admin", "password": "salah"}, form=True))
    sama_persis("login badan JSON (bukan form)",
                *keduanya("POST", "/api/v1/login", {"username": "admin", "password": "x"}))
    sama_persis("api_key tanpa bearer", *keduanya("GET", "/api/v1/api_key/"))
    sama_persis("api_key bearer rusak",
                *keduanya("GET", "/api/v1/api_key/", header={"Authorization": "Bearer a.b.c"}))

    kunci = {}
    for base in (LANGFLOW, SERVICE):
        u, p = AKUN[base]
        s, tok = minta(base, "POST", "/api/v1/login", {"username": u, "password": p}, form=True)
        cek(s == 200 and sorted(tok) == ["access_token", "refresh_token", "token_type"]
            and tok["token_type"] == "bearer", f"login {base}: {s} {sorted(tok) if s == 200 else tok}")
        b = {"Authorization": f"Bearer {tok['access_token']}"}
        s, baru = minta(base, "POST", "/api/v1/api_key/", {"name": "uji-kompat-sementara"}, header=b)
        s2, daftar = minta(base, "GET", "/api/v1/api_key/", header=b)
        kunci[base] = {"bearer": b, "baru": baru, "daftar": daftar, "status": (s, s2)}

    lf, sv = kunci[LANGFLOW], kunci[SERVICE]
    cek(lf["status"] == sv["status"] == (200, 200), f"api_key buat+daftar: {lf['status']} vs {sv['status']}")
    cek(sorted(lf["baru"]) == sorted(sv["baru"]),
        f"kunci balasan pembuatan sama: {sorted(sv['baru'])}")
    k_lf, k_sv = lf["baru"]["api_key"], sv["baru"]["api_key"]
    cek(k_sv.startswith("sk-") and len(k_sv) == len(k_lf), f"bentuk api_key: sk-… panjang {len(k_sv)}")
    cek(sorted(lf["daftar"]) == sorted(sv["daftar"]), f"kunci daftar sama: {sorted(sv['daftar'])}")
    it_lf = next(k for k in lf["daftar"]["api_keys"] if k["id"] == lf["baru"]["id"])
    it_sv = next(k for k in sv["daftar"]["api_keys"] if k["id"] == sv["baru"]["id"])
    cek(sorted(it_lf) == sorted(it_sv), f"kunci tiap item daftar sama: {sorted(it_sv)}")
    cek(it_sv["api_key"] == k_sv[:8] + "*" * (len(k_sv) - 8)
        and it_lf["api_key"] == k_lf[:8] + "*" * (len(k_lf) - 8), "penyamaran: 8 karakter + bintang")
    return {LANGFLOW: k_lf, SERVICE: k_sv, "_simpan": kunci}


def hapus_kunci(k: dict) -> None:
    print("\n   (membersihkan API key sementara)")
    hasil = []
    for base in (LANGFLOW, SERVICE):
        d = k["_simpan"][base]
        hasil.append(minta(base, "DELETE", f"/api/v1/api_key/{d['baru']['id']}", header=d["bearer"]))
    sama_persis("api_key hapus", *hasil)


# ── 2. /api/v1/run ──────────────────────────────────────────────────────────

def badan(node, param, input_type="text"):
    return {"output_type": "chat", "input_type": input_type, "input_value": "",
            "tweaks": {node: param}}


def run_keduanya(kunci, endpoint, isi, lewat_query=False):
    hasil = []
    for base in (LANGFLOW, SERVICE):
        jalur = f"/api/v1/run/{endpoint}?stream=false"
        if lewat_query:
            hasil.append(minta(base, "POST", jalur + f"&x-api-key={kunci[base]}", isi))
        else:
            hasil.append(minta(base, "POST", jalur, isi, header={"x-api-key": kunci[base]}))
    return hasil


def uji_run(kunci) -> None:
    print("\n2. /api/v1/run — autentikasi & galat")
    isi = badan("GradingRuleGet-9c9c5", {"grade_id": "2"})
    sama_persis("tanpa kunci", *keduanya("POST", "/api/v1/run/config-rules?stream=false", isi))
    sama_persis("kunci salah", *keduanya("POST", "/api/v1/run/config-rules?stream=false", isi,
                                         header={"x-api-key": "sk-salah"}))
    sama_persis("bearer saja (bukan x-api-key)",
                *keduanya("POST", "/api/v1/run/config-rules?stream=false", isi,
                          header={"Authorization": "Bearer sk-apa-saja"}))
    lf, sv = run_keduanya(kunci, "flow-tidak-ada", isi)
    sama_persis("flow tak dikenal", lf, sv)

    for nama, endpoint, isi in [
        ("grade di luar 1-6", "config-rules", badan("GradingRuleGet-9c9c5", {"grade_id": "9"})),
        ("grading-dispatch payload bukan JSON", "grading-dispatch",
         badan("GradingDispatch-a3967", {"payload": "{bukan json"})),
        ("grading-dispatch tanpa fileId", "grading-dispatch",
         badan("GradingDispatch-a3967", {"payload": json.dumps({"filename": "a.csv"})})),
        ("grading-dispatch format ditolak (.pdf)", "grading-dispatch",
         badan("GradingDispatch-a3967", {"payload": json.dumps(
             {"fileId": "uji-kompat", "s3Bucket": "bucket-test",
              "rawSourceKey": "uploads/uji-kompat/raw/berkas.pdf"})})),
        ("matching-dispatch field kurang", "matching-dispatch",
         badan("MatchingDispatch-b4819", {"payload": json.dumps({"jobId": "x"})}, "chat")),
        ("grading-status tanpa file_id/job_id", "grading-status",
         badan("GradingStatus-3cc03", {"file_id": ""})),
        ("config-rules-update tanpa gradeId", "config-rules-update",
         badan("GradingRuleUpdate-ea0f7", {"payload": json.dumps({"dryRun": True})})),
    ]:
        sama_persis(nama, *run_keduanya(kunci, endpoint, isi))

    print("\n3. /api/v1/run — sukses (butuh PostgreSQL)")
    for nama, endpoint, isi, query in [
        ("config-rules semua", "config-rules", badan("GradingRuleGet-9c9c5", {"grade_id": ""}), False),
        ("config-rules grade 2 (kunci lewat query)", "config-rules",
         badan("GradingRuleGet-9c9c5", {"grade_id": "2"}), True),
        ("config-rules grade_id angka, bukan teks", "config-rules",
         badan("GradingRuleGet-9c9c5", {"grade_id": 3}), False),
        ("config-rules grade_id objek {value}", "config-rules",
         badan("GradingRuleGet-9c9c5", {"grade_id": {"value": "5"}}), False),
        ("config-rules grade_id objek tanpa value", "config-rules",
         badan("GradingRuleGet-9c9c5", {"grade_id": {"a": 1}}), False),
        ("config-rules grade_id array", "config-rules",
         badan("GradingRuleGet-9c9c5", {"grade_id": [1, 2]}), False),
        ("config-rules-update dry_run 'true' (teks)", "config-rules-update",
         badan("GradingRuleUpdate-ea0f7", {"dry_run": "true", "payload": json.dumps(
             {"gradeId": 2, "updatedBy": "uji-kompat", "score": {"min": 99999}})}), False),
        ("grading-status berkas tak ada", "grading-status",
         badan("GradingStatus-3cc03", {"file_id": "uji-kompat-tidak-ada"}), False),
        ("config-rules-update dryRun", "config-rules-update",
         badan("GradingRuleUpdate-ea0f7", {"payload": json.dumps(
             {"gradeId": 2, "dryRun": True, "updatedBy": "uji-kompat",
              "score": {"min": 72}})}), False),
        ("tweak ke node yang tak ada diabaikan", "config-rules",
         {**badan("GradingRuleGet-9c9c5", {"grade_id": "4"}),
          "tweaks": {"GradingRuleGet-9c9c5": {"grade_id": "4"}, "NodeLain-00000": {"x": 1}}}, False),
    ]:
        lf, sv = run_keduanya(kunci, endpoint, isi, query)
        if lf[0] != 200 or sv[0] != 200:
            cek(lf == sv, f"{nama}: Langflow {lf[0]} | service {sv[0]} {str(sv[1])[:160]!r}")
            continue
        teks_lf = lf[1]["outputs"][0]["outputs"][0]["results"]["message"]["text"]
        teks_sv = sv[1]["outputs"][0]["outputs"][0]["results"]["message"]["text"]
        cek(json.loads(teks_lf) == json.loads(teks_sv), f"{nama}: isi text sama")
        beda = jalur_kunci(lf[1]) ^ jalur_kunci(sv[1])
        cek(not beda, f"{nama}: selubung sama ({len(jalur_kunci(sv[1]))} jalur){' beda: ' + str(sorted(beda)[:6]) if beda else ''}")
        o_lf, o_sv = lf[1]["outputs"][0]["outputs"][0], sv[1]["outputs"][0]["outputs"][0]
        cek(o_lf["component_id"] == o_sv["component_id"]
            and o_lf["results"]["message"]["properties"]["source"]
            == o_sv["results"]["message"]["properties"]["source"],
            f"{nama}: component_id {o_sv['component_id']}, sumber "
            f"{o_sv['results']['message']['properties']['source']['id']}")


def main() -> int:
    print(f"Langflow {LANGFLOW}  vs  service {SERVICE}")
    kunci = uji_auth()
    try:
        uji_run(kunci)
    finally:
        hapus_kunci(kunci)
    print(f"\n{'SEMUA SAMA' if not GAGAL else f'{len(GAGAL)} BERBEDA'}")
    return 1 if GAGAL else 0


if __name__ == "__main__":
    sys.exit(main())
