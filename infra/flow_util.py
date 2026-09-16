"""
Pembangun flow Langflow lewat API — dipakai bersama oleh skrip matching & grading.

Merangkai node secara manual di kanvas untuk tujuh sampai delapan node adalah
pekerjaan yang mudah salah dan tidak bisa diulang. Skrip mengambil template
komponen dari /api/v1/all, menyusun rantai linear, lalu menyimpannya sebagai
flow — idempoten, jadi menjalankannya dua kali MEMPERBARUI flow yang sama.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.getenv("LANGFLOW_URL", "http://localhost:7860")
USER = os.getenv("LANGFLOW_SUPERUSER", "admin")
PASS = os.getenv("LANGFLOW_SUPERUSER_PASSWORD", "synchrono123")


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


def masuk() -> str:
    return minta("POST", "/api/v1/login",
                 {"username": USER, "password": PASS}, form=True)["access_token"]


def katalog(token: str, kategori: str) -> dict:
    """
    Template komponen dalam satu kategori, diindeks nama kelasnya.

    Kategori = nama subfolder di bawah LANGFLOW_COMPONENTS_PATH. Kunci di
    /api/v1/all berbentuk "ext:<kategori>:<NamaKelas>@extra".
    """
    semua = minta("GET", "/api/v1/all", token=token)
    isi = semua.get(kategori) or {}
    per_nama = {k.split(":")[-1].split("@")[0]: v for k, v in isi.items()}

    # ChatOutput selalu diikutkan. Tanpa komponen output RESMI di ujung,
    # /api/v1/run membalas "outputs": [] — flow tetap berjalan dan datanya
    # tertulis, tapi pemanggil tidak menerima apa pun. Mengembalikan Message
    # dari komponen biasa TIDAK cukup.
    chat = (semua.get("input_output") or {}).get("ChatOutput")
    if not chat:
        raise SystemExit("Komponen ChatOutput tidak ditemukan di /api/v1/all")
    per_nama["ChatOutput"] = chat
    return per_nama


def bangun(token: str, nama_flow: str, deskripsi: str, endpoint: str,
           per_nama: dict, rantai: list[tuple[str, str | None, str]]) -> tuple[str, str]:
    """
    Simpan satu flow linear. Mengembalikan (flow_id, id node pertama).

    `rantai` berisi (nama_komponen, nama_input_yang_disambung, nama_output).
    Output SENGAJA dinamai berbeda dari input: Langflow menolak komponen yang
    nama input dan output-nya bertabrakan — node-nya hilang diam-diam dari
    sidebar dan hanya muncul sebagai warning di log.
    """
    kurang = [n for n, _, _ in rantai if n not in per_nama]
    if kurang:
        raise SystemExit(
            f"Komponen belum terdaftar di Langflow: {kurang}\n"
            "Cek: docker compose logs langflow | grep -i 'could not build template'"
        )

    nodes, edges, id_node = [], [], {}

    for i, (komp, _, _) in enumerate(rantai):
        # Id node dibuat DETERMINISTIK, bukan acak.
        #
        # Kunci `tweaks` pada /api/v1/run harus berupa id node — nama komponen
        # tidak diterima. Kalau id-nya acak, tiap kali flow dibangun ulang
        # backend Synchrono harus mengubah kodenya. Dengan turunan dari
        # (endpoint, komponen), id-nya tetap sama selamanya dan tetap unik
        # antar flow.
        sidik = hashlib.md5(f"{endpoint}:{komp}".encode()).hexdigest()[:5]
        nid = f"{komp}-{sidik}"
        id_node[komp] = nid
        tpl = json.loads(json.dumps(per_nama[komp]))  # salinan dalam
        nodes.append({
            "id": nid,
            "type": "genericNode",
            "position": {"x": 360 * i, "y": 220 * (i % 2)},
            "data": {"id": nid, "type": komp, "node": tpl},
        })

    for i in range(len(rantai) - 1):
        komp_a, _, out = rantai[i]
        komp_b, inp, _ = rantai[i + 1]
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
        "name": nama_flow,
        "description": deskripsi,
        # Membuat URL-nya terbaca: /api/v1/run/<endpoint>, bukan UUID.
        "endpoint_name": endpoint,
        "data": {"nodes": nodes, "edges": edges},
    }

    lama = [f for f in (minta("GET", "/api/v1/flows/", token=token) or [])
            if f.get("name") == nama_flow]
    if lama:
        fid = lama[0]["id"]
        minta("PATCH", f"/api/v1/flows/{fid}", flow, token=token)
        aksi = "diperbarui"
    else:
        fid = minta("POST", "/api/v1/flows/", flow, token=token)["id"]
        aksi = "dibuat"

    print(f"  flow {aksi}: {nama_flow}  ({len(nodes)} node, {len(edges)} sambungan)")
    return fid, id_node[rantai[0][0]]
