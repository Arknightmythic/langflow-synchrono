"""
Bangun kedua flow service KONFIGURASI di Langflow.

    python buat_flow_config.py

Idempoten: flow dengan nama sama DIPERBARUI, dan id node-nya tetap sama karena
diturunkan dari (endpoint, komponen) — aman di-hardcode di UI Synchrono.

  config-rules         API 3. Baca seluruh konfigurasi aturan per grade.
  config-rules-update  API 4. Ubah ambang satu grade, dengan validasi.

Terpisah dari flow grading: yang ini melayani menu pengaturan di UI dan
dipanggil oleh manusia, sedangkan grading dipanggil mesin saat berkas diunggah.
Keduanya berbagi container dan basis data yang sama.
"""

import json
from pathlib import Path

from flow_util import BASE, bangun, katalog, masuk, simpan_contoh

DISINI = Path(__file__).parent

GET = [
    ("GradingRuleGet", None,          "rules"),
    ("ChatOutput",     "input_value", "message"),
]

UPDATE = [
    ("GradingRuleUpdate", None,          "result"),
    ("ChatOutput",        "input_value", "message"),
]


def main() -> int:
    token = masuk()
    print("login OK")

    per_nama = katalog(token, "config")
    komponen = sorted(k for k in per_nama if k != "ChatOutput")
    print(f"komponen di kategori 'config': {komponen}\n")

    fid_g, node_g = bangun(
        token, "Synchrono Config Rules",
        "API 3 — baca konfigurasi aturan grading & matching per grade.",
        "config-rules", per_nama, GET)

    fid_u, node_u = bangun(
        token, "Synchrono Config Rules Update",
        "API 4 — ubah ambang satu grade, divalidasi sebelum ditulis.",
        "config-rules-update", per_nama, UPDATE)

    contoh_get = {
        "output_type": "chat", "input_type": "text", "input_value": "",
        "tweaks": {node_g: {"grade_id": ""}},
    }
    contoh_update = {
        "output_type": "chat", "input_type": "text", "input_value": "",
        "tweaks": {
            node_u: {
                "payload": json.dumps({
                    "gradeId": 2,
                    "updatedBy": "reno",
                    "dryRun": True,
                    "criteria": {
                        "minCompleteness": {"tempat_lahir": 0.75},
                        "minNikTrusted": 0.75,
                    },
                    "score": {"min": 72},
                    "matching": {"autoScoreMin": 86.0},
                }, ensure_ascii=False),
                "dry_run": False,
            }
        },
    }

    simpan_contoh(DISINI / "postman_config_get.json", contoh_get)
    simpan_contoh(DISINI / "postman_config_update.json", contoh_update)

    garis = "=" * 74
    print(f"\n{garis}")
    print("  API 3 — GET RULES")
    print(f"    POST {BASE}/api/v1/run/config-rules?stream=false")
    print(f"    node id : {node_g}")
    print(f"    kanvas  : {BASE}/flow/{fid_g}")
    print(f"    body    : infra/postman_config_get.json")
    print()
    print("  API 4 — UPDATE RULE")
    print(f"    POST {BASE}/api/v1/run/config-rules-update?stream=false")
    print(f"    node id : {node_u}")
    print(f"    kanvas  : {BASE}/flow/{fid_u}")
    print(f"    body    : infra/postman_config_update.json")
    print(garis)
    print("\n  Header wajib: x-api-key (BUKAN Authorization: Bearer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
