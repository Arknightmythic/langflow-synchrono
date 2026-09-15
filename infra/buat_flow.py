"""
Bangun flow matching di Langflow lewat API — tanpa merangkai manual di kanvas.

Mengambil template ketujuh komponen dari /api/v1/all, menyusunnya jadi rantai
linear, lalu menyimpannya sebagai flow. Mencetak flow_id yang siap dipakai di
Postman.

    python buat_flow.py

Idempoten: kalau flow dengan nama sama sudah ada, ia DIPERBARUI, bukan digandakan.
"""

import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.getenv("LANGFLOW_URL", "http://localhost:7860")
USER = os.getenv("LANGFLOW_SUPERUSER", "admin")
PASS = os.getenv("LANGFLOW_SUPERUSER_PASSWORD", "synchrono123")
NAMA_FLOW = "Synchrono Matching"

# Urutan node + nama port. Output sengaja berbeda dari input: Langflow menolak
# komponen yang nama input dan output-nya bertabrakan (node-nya hilang diam-diam
# dari sidebar, hanya muncul sebagai warning di log).
RANTAI = [
    ("OpenMatchingSession", None,      "session"),
    ("PrepareIncoming",     "session", "incoming_ready"),
    ("PrepareMaster",       "session", "master_ready"),
    ("LoadMatchingConfig",  "session", "config_ready"),
    ("RunMatchingJoin",     "session", "joined"),
    ("ScoreAndClassify",    "session", "scored"),
    ("PersistResults",      "session", "summary"),
]


def minta(metode, jalur, data=None, token=None, form=False):
    url = f"{BASE}{jalur}"
    header = {}
    tubuh = None
    if data is not None:
        if form:
            tubuh = urllib.parse.urlencode(data).encode()
            header["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            tubuh = json.dumps(data).encode()
            header["Content-Type"] = "application/json"
    if token:
        header["Authorization"] = f"Bearer {token}"

    # Langflow mengirim /api/v1/all dalam keadaan ter-gzip; urllib tidak
    # membukanya sendiri, jadi ditangani manual di sini.
    header["Accept-Encoding"] = "gzip"

    req = urllib.request.Request(url, data=tubuh, headers=header, method=metode)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            mentah = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                mentah = gzip.decompress(mentah)
            isi = mentah.decode()
            return json.loads(isi) if isi else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{metode} {jalur} -> {e.code}\n{e.read().decode()[:600]}")


def main():
    token = minta("POST", "/api/v1/login",
                  {"username": USER, "password": PASS}, form=True)["access_token"]
    print("login OK")

    semua = minta("GET", "/api/v1/all", token=token)
    katalog = semua.get("matching") or {}
    # Kunci di /api/v1/all berbentuk "ext:matching:NamaKomponen@extra"
    per_nama = {k.split(":")[-1].split("@")[0]: v for k, v in katalog.items()}

    # ChatOutput dipakai sebagai node terakhir. Tanpa ini, endpoint
    # /api/v1/run membalas "outputs": [] — flow tetap jalan dan datanya tertulis
    # ke PostgreSQL, tapi pemanggil tidak menerima ringkasan apa pun. Langflow
    # hanya menyertakan hasil dari komponen output resminya, dan mengembalikan
    # Message dari komponen biasa TIDAK cukup.
    chat_out = (semua.get("input_output") or {}).get("ChatOutput")
    if not chat_out:
        raise SystemExit("Komponen ChatOutput tidak ditemukan di /api/v1/all")
    per_nama["ChatOutput"] = chat_out
    RANTAI.append(("ChatOutput", "input_value", "message"))

    kurang = [n for n, _, _ in RANTAI if n not in per_nama]
    if kurang:
        raise SystemExit(
            f"Komponen belum terdaftar di Langflow: {kurang}\n"
            "Cek: docker compose logs langflow | grep -i 'could not build template'"
        )
    print(f"ketujuh komponen ditemukan di kategori 'matching'")

    nodes, edges, id_node = [], [], {}

    for i, (komp, _, _) in enumerate(RANTAI):
        nid = f"{komp}-{uuid.uuid4().hex[:5]}"
        id_node[komp] = nid
        tpl = json.loads(json.dumps(per_nama[komp]))  # salinan dalam
        nodes.append({
            "id": nid,
            "type": "genericNode",
            "position": {"x": 360 * i, "y": 220 * (i % 2)},
            "data": {"id": nid, "type": komp, "node": tpl},
        })

    for i in range(len(RANTAI) - 1):
        komp_a, _, out = RANTAI[i]
        komp_b, inp, _ = RANTAI[i + 1]
        a, b = id_node[komp_a], id_node[komp_b]

        spec_out = next(o for o in per_nama[komp_a]["outputs"] if o["name"] == out)
        spec_in = per_nama[komp_b]["template"][inp]

        edges.append({
            "id": f"reactflow__edge-{a}-{b}-{uuid.uuid4().hex[:5]}",
            "source": a,
            "target": b,
            "data": {
                "sourceHandle": {
                    "dataType": komp_a,
                    "id": a,
                    "name": out,
                    "output_types": spec_out.get("types", []),
                },
                "targetHandle": {
                    "fieldName": inp,
                    "id": b,
                    "inputTypes": spec_in.get("input_types", []),
                    "type": spec_in.get("type", "other"),
                },
            },
        })

    flow = {
        "name": NAMA_FLOW,
        "description": "Matching Synchrono: parquet SeaweedFS x master PostgreSQL, "
                       "seluruhnya di DuckDB.",
        "data": {"nodes": nodes, "edges": edges},
    }

    lama = [f for f in (minta("GET", "/api/v1/flows/", token=token) or [])
            if f.get("name") == NAMA_FLOW]
    if lama:
        fid = lama[0]["id"]
        minta("PATCH", f"/api/v1/flows/{fid}", flow, token=token)
        print(f"flow diperbarui (nama sama sudah ada)")
    else:
        fid = minta("POST", "/api/v1/flows/", flow, token=token)["id"]
        print("flow dibuat")

    node1 = id_node["OpenMatchingSession"]

    print(f"\n{'=' * 66}")
    print(f"  FLOW_ID  : {fid}")
    print(f"  kanvas   : {BASE}/flow/{fid}")
    print(f"  endpoint : POST {BASE}/api/v1/run/{fid}?stream=false")
    print(f"  {len(nodes)} node, {len(edges)} sambungan")
    print(f"{'=' * 66}")

    # Kunci `tweaks` HARUS node id, bukan nama komponen. Kalau memakai nama
    # komponen, flow tetap jalan tapi parameternya tidak tersampaikan dan node 1
    # gagal dengan "file_id kosong".
    contoh = {
        "output_type": "any",
        "input_type": "text",
        "input_value": "",
        "tweaks": {
            node1: {
                "file_id": "825fc484",
                "parquet_path": "s3://synchrono/curated/xxx.parquet",
                "grade": 4,
            }
        },
    }
    print("\n  BODY untuk Postman (kunci tweaks = node id):\n")
    print("\n".join("    " + b for b in json.dumps(contoh, indent=2).splitlines()))
    print("\n  Header wajib:")
    print("    x-api-key: <API KEY>        <- BUKAN Authorization: Bearer")
    print("    Content-Type: application/json")

    berkas = os.path.join(os.path.dirname(__file__), "postman_body.json")
    with open(berkas, "w", encoding="utf-8") as f:
        json.dump(contoh, f, indent=2)
    print(f"\n  body juga disimpan ke: {berkas}")


if __name__ == "__main__":
    main()
