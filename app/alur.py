"""
Flow Langflow, dijalankan TANPA Langflow.

Portal sudah terikat pada kontrak Langflow: `POST /api/v1/run/<endpoint>`,
`tweaks` yang dikunci id node, dan balasan yang isinya terkubur di
`outputs[0].outputs[0].results.message.text`. Modul ini memenuhi kontrak itu
apa adanya, supaya portal bisa dipindahkan ke service ini tanpa mengubah satu
baris kode pun.

KOMPONENNYA SAMA PERSIS, BUKAN SALINAN

Yang dijalankan di sini adalah KELAS KOMPONEN YANG SAMA dengan yang dipakai
Langflow — berkas di `langflow-synchrono/components/`, dimuat langsung. Tanpa
Langflow terpasang, `_shared.py` memberinya kelas pengganti (`Component`,
`Message`, ...), dan metode keluarannya dipanggil persis seperti Langflow
memanggilnya. Akibatnya:

  * teks balasan identik dengan versi Langflow — bukan karena ditiru, tapi
    karena kode yang menghasilkannya sama;
  * perubahan komponen berikutnya otomatis berlaku di sini juga. Tidak ada dua
    salinan logika yang bisa berbeda diam-diam.

ID NODE DITURUNKAN DENGAN RUMUS YANG SAMA

`{Komponen}-{md5("{endpoint}:{Komponen}")[:5]}` — rumus `infra/flow_util.py`
milik Langflow. Portal menulis id itu di kodenya (`GradingDispatch-a3967`), dan
Langflow MENGABAIKAN tweak ke id yang tidak dikenalnya tanpa galat apa pun.
Id yang meleset satu huruf berarti parameter tidak pernah sampai. Id matching
dipaksa `MatchingDispatch-b4819`, sama seperti di Langflow, karena id itu
ditetapkan spesifikasi integrasi.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import _shared

KOMPONEN = Path(os.getenv("SYNCHRONO_COMPONENTS", "/components"))


def _pasang_stub_bertipe() -> None:
    """
    Stub input yang MENCATAT JENISNYA, dipasang dari sini — bukan di lib/.

    Tanpa Langflow, `_shared.py` memberi komponen stub `MessageTextInput(...)`
    dsb. yang hanya mengembalikan kwargs; jenis inputnya hilang. Padahal service
    ini harus memperlakukan nilai tweak sesuai jenisnya, persis Langflow
    ("true" untuk BoolInput, angka dibuang untuk input teks — lihat
    `_sesuaikan`).

    Diganti di atribut modul `_shared`, SEBELUM komponen mana pun dimuat:
    komponen melakukan `from _shared import MessageTextInput` saat dimuat, jadi
    mereka mendapat versi ini. lib/ milik langflow-synchrono tidak perlu diubah
    — service ini berada di branch sendiri dan tidak boleh menuntut perubahan
    di sana.
    """
    if getattr(_shared, "LANGFLOW_TERSEDIA", False):
        return

    def stub(tipe: str):
        def buat(**kwargs):
            return {"_tipe": tipe, **kwargs}
        return buat

    for nama, tipe in (("MessageTextInput", "teks"), ("HandleInput", "sambungan"),
                       ("IntInput", "angka"), ("BoolInput", "bool"),
                       ("Output", "keluaran")):
        setattr(_shared, nama, stub(tipe))


_pasang_stub_bertipe()

# flow_id tiap endpoint: tetap antar-restart, jadi `session_id`/`flow_id` di
# balasan bisa dipakai lagi untuk memanggil flow yang sama — seperti Langflow.
_RUANG_NAMA = uuid.uuid5(uuid.NAMESPACE_URL, "synchrono-service/flow")


@dataclass
class Simpul:
    berkas: str          # relatif terhadap KOMPONEN, mis. "grading/api1_dispatch.py"
    kelas: str           # nama kelas = nama komponen di Langflow
    masuk: str | None    # input yang disambung dari keluaran simpul sebelumnya
    node_id: str = ""


@dataclass
class Alur:
    endpoint: str
    nama: str
    simpul: list[Simpul]
    flow_id: str = ""
    id_chat: str = ""
    id_paksa: dict[str, str] = field(default_factory=dict)


def id_node(endpoint: str, komponen: str) -> str:
    """Rumus `infra/flow_util.py` Langflow — jangan diubah, portal bergantung padanya."""
    return f"{komponen}-{hashlib.md5(f'{endpoint}:{komponen}'.encode()).hexdigest()[:5]}"


# Sama dengan rantai di infra/buat_flow_grading.py, buat_flow_config.py, dan
# buat_flow_matching_dispatch.py milik Langflow. ChatOutput tidak ditulis di
# sini: ia hanya meneruskan Message node terakhir, dan id-nya dihitung terpisah.
ALUR = {a.endpoint: a for a in [
    Alur("grading-dispatch", "Synchrono Grading Dispatch",
         [Simpul("grading/api1_dispatch.py", "GradingDispatch", None)]),
    Alur("grading-status", "Synchrono Grading Status",
         [Simpul("grading/api2_status.py", "GradingStatus", None)]),
    Alur("grading", "Synchrono Grading Pipeline", [
        Simpul("grading/g1_open_grading.py", "OpenGradingSession", None),
        Simpul("grading/g2_load_raw.py", "LoadRawParquet", "session"),
        Simpul("grading/g3_flag_anomalies.py", "FlagAnomalies", "session"),
        Simpul("grading/g4_score_grade.py", "ScoreAndGrade", "session"),
        Simpul("grading/g5_write_enriched.py", "WriteEnrichedParquet", "session"),
        Simpul("grading/g6_build_payload.py", "BuildCallbackPayload", "session"),
    ]),
    Alur("config-rules", "Synchrono Config Rules",
         [Simpul("config/api1_get_rules.py", "GradingRuleGet", None)]),
    Alur("config-rules-update", "Synchrono Config Rules Update",
         [Simpul("config/api2_update_rule.py", "GradingRuleUpdate", None)]),
    Alur("matching-dispatch", "Synchrono Matching Dispatch",
         [Simpul("matching/api_dispatch.py", "MatchingDispatch", None)],
         id_paksa={"MatchingDispatch": "MatchingDispatch-b4819"}),
]}

for _a in ALUR.values():
    _a.flow_id = str(uuid.uuid5(_RUANG_NAMA, _a.endpoint))
    _a.id_chat = id_node(_a.endpoint, "ChatOutput")
    for _s in _a.simpul:
        _s.node_id = _a.id_paksa.get(_s.kelas) or id_node(_a.endpoint, _s.kelas)

_PER_FLOW_ID = {a.flow_id: a for a in ALUR.values()}


def cari(pengenal: str) -> Alur | None:
    """Nama endpoint atau flow_id — Langflow menerima keduanya."""
    return ALUR.get(pengenal) or _PER_FLOW_ID.get(pengenal)


# ── Memuat kelas komponen ───────────────────────────────────────────────────

_kelas: dict[str, type] = {}
_kunci_muat = threading.Lock()


def kelas_komponen(s: Simpul) -> type:
    """
    Kelas komponen dari berkasnya, dimuat sekali lalu disimpan.

    Dimuat lewat PATH, bukan `import`: beberapa kategori punya nama berkas yang
    sama (`api1_*`, `api2_*`), dan Langflow sendiri memuat tiap kategori sebagai
    bundel terpisah. Nama modulnya dibuat unik per berkas.
    """
    with _kunci_muat:
        if s.berkas not in _kelas:
            path = KOMPONEN / s.berkas
            nama = "komponen_" + s.berkas.replace("/", "_").removesuffix(".py")
            spec = importlib.util.spec_from_file_location(nama, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"komponen tidak ditemukan: {path}")
            modul = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modul)
            _kelas[s.berkas] = getattr(modul, s.kelas)
        return _kelas[s.berkas]


def periksa_semua() -> dict[str, str]:
    """{endpoint: 'ok' | galat} — untuk log saat start dan /health_check."""
    hasil = {}
    for a in ALUR.values():
        try:
            for s in a.simpul:
                kelas_komponen(s)
            hasil[a.endpoint] = "ok"
        except Exception as e:  # noqa: BLE001
            hasil[a.endpoint] = f"{type(e).__name__}: {e}"
    return hasil


# ── Menjalankan ─────────────────────────────────────────────────────────────

class GalatKomponen(Exception):
    """Komponen melempar galat — Langflow membalasnya 500 dengan pesan berbungkus."""

    def __init__(self, nama_tampilan: str, galat: Exception):
        super().__init__(str(galat))
        self.nama_tampilan = nama_tampilan
        self.galat = galat

    def detail(self) -> str:
        # Bentuknya persis balasan Langflow — terekam dari Langflow yang jalan:
        # `detail` berisi STRING JSON, bukan objek.
        return json.dumps({
            "message": f"Error running graph: Error building Component "
                       f"{self.nama_tampilan}: \n\n{self.galat}",
            "traceback": None, "description": None, "code": None, "suggestion": None,
        }, ensure_ascii=False, separators=(",", ":"))


def _nilai_awal(spec: dict):
    if "value" in spec:
        return spec["value"]
    # Langflow memberi input teks yang tak diisi string kosong, bukan None.
    return {"teks": "", "bool": False, "angka": 0}.get(spec.get("_tipe"))


_TETAP = object()   # tweak yang oleh Langflow dibuang: input tetap bernilai awal


def _sesuaikan(spec: dict, nilai):
    """
    Nilai tweak -> nilai input, PERSIS seperti Langflow — diukur, bukan ditebak
    (28 Sep 2026, flow config-rules, input teks `grade_id`):

        "2"               dipakai
        3, 3.0, true, null  DIBUANG — input tetap kosong (hasil: semua grade)
        {"value": "2"}    objek = atribut field; hanya `value` yang berarti
        {"a": 1}          dibuang
        ["x"]             galat validasi pydantic, isinya diubah jadi teks

    Versi awal service ini lebih longgar (3 menjadi "3"), dan itu justru
    salah: portal yang pindah platform akan melihat jawaban yang berbeda
    untuk permintaan yang sama.
    """
    if isinstance(nilai, dict):
        if "value" not in nilai:
            return _TETAP
        nilai = nilai["value"]
    tipe = spec.get("_tipe")
    if tipe == "teks":
        if isinstance(nilai, str):
            return nilai
        if isinstance(nilai, list):
            isi = [str(x) for x in nilai]
            raise ValueError(
                "1 validation error for MessageTextInput\nvalue\n"
                f"  Value error, Invalid value type <class 'list'> "
                f"[type=value_error, input_value={isi!r}, input_type=list]\n"
                "    For further information visit "
                "https://errors.pydantic.dev/2.13/v/value_error")
        return _TETAP
    if tipe == "bool":
        if isinstance(nilai, str):
            return nilai.strip().lower() in ("true", "1", "yes", "on")
        return bool(nilai)
    if tipe == "angka":
        return int(nilai)
    return nilai


def jalankan(alur: Alur, tweaks: dict | None) -> str:
    """
    Rantai node dijalankan berurutan; kembalikan teks Message node terakhir.

    Tweak ke id node yang tidak dikenal DIABAIKAN, begitu juga nama field yang
    bukan input komponennya — persis Langflow. Menolaknya justru akan membuat
    service ini berperilaku berbeda dari yang sudah diuji portal.
    """
    tweaks = tweaks or {}
    keluaran = None
    for s in alur.simpul:
        kelas = kelas_komponen(s)
        spesifikasi = {i["name"]: i for i in kelas.inputs}
        nilai = {nama: _nilai_awal(spec) for nama, spec in spesifikasi.items()}
        try:
            for nama, isi in (tweaks.get(s.node_id) or {}).items():
                spec = spesifikasi.get(nama)
                if spec is not None and spec.get("_tipe") != "sambungan":
                    baru = _sesuaikan(spec, isi)
                    if baru is not _TETAP:
                        nilai[nama] = baru
            if s.masuk:
                nilai[s.masuk] = keluaran
            keluaran = getattr(kelas(**nilai), kelas.outputs[0]["method"])()
        except Exception as e:  # noqa: BLE001 — apa pun, dibungkus seperti Langflow
            raise GalatKomponen(kelas.display_name, e) from e
    teks = getattr(keluaran, "text", None)
    return teks if teks is not None else str(keluaran)


# ── Selubung balasan Langflow ───────────────────────────────────────────────

def bungkus(alur: Alur, teks: str, input_value: str | None,
            session_id: str | None) -> dict:
    """
    Balasan `/api/v1/run` — bentuknya direkam dari Langflow yang jalan
    (matching-dispatch, 27 Sep 2026), kunci demi kunci.

    Portal hanya membaca `outputs[0].outputs[0].results.message.text`, tapi
    seluruh selubung ditiru: kode lain yang membaca bagian lain — `session_id`,
    `messages[0].message`, `artifacts.message` — tidak boleh mendapati kunci
    yang hilang hanya karena pindah platform.
    """
    sumber = alur.simpul[-1]
    nama_sumber = kelas_komponen(sumber).display_name
    sid = session_id or alur.flow_id
    run_id = str(uuid.uuid4())
    waktu = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")
    properti = {
        "text_color": None, "background_color": None, "edited": False,
        "source": {"id": sumber.node_id, "display_name": nama_sumber,
                   "source": nama_sumber},
        "icon": None, "allow_markdown": False, "positive_feedback": None,
        "state": "complete", "targets": [], "usage": None, "build_duration": None,
    }
    data = {
        "timestamp": waktu, "sender": "Machine", "sender_name": "AI",
        "session_id": sid, "context_id": "", "text": teks, "files": [],
        "error": False, "edit": False, "properties": properti,
        "category": "message", "content_blocks": [],
        "session_metadata": {"graph_run_id": run_id},
        "id": str(uuid.uuid4()), "flow_id": alur.flow_id, "run_id": run_id,
        "duration": None,
    }
    pesan = {
        "text_key": "text", "data": data, "default_value": "",
        "sender": "Machine", "sender_name": "AI", "files": [], "session_id": sid,
        "context_id": "", "run_id": run_id, "timestamp": waktu,
        "flow_id": alur.flow_id, "error": False, "edit": False,
        "properties": properti, "category": "message", "content_blocks": [],
        "duration": None, "session_metadata": {"graph_run_id": run_id},
        "text": teks,
    }
    return {
        "session_id": sid,
        "outputs": [{
            "inputs": {"input_value": input_value or ""},
            "outputs": [{
                "results": {"message": pesan},
                "artifacts": {"message": teks, "sender": "Machine",
                              "sender_name": "AI", "files": [], "type": "object"},
                "outputs": {"message": {"message": teks, "type": "text"}},
                "logs": {"message": []},
                "messages": [{
                    "message": teks, "sender": "Machine", "sender_name": "AI",
                    "session_id": sid, "stream_url": None,
                    "component_id": alur.id_chat, "files": [], "type": "text",
                }],
                "timedelta": None, "duration": None,
                "component_display_name": "Chat Output",
                "component_id": alur.id_chat,
                "used_frozen_result": False, "token_usage": None,
            }],
        }],
    }
