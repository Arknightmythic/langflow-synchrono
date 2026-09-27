"""
Bangun flow `matching-dispatch` di Langflow.

    POST /api/v1/run/matching-dispatch?stream=false

Node dispatch-nya DIPAKSA ber-id `MatchingDispatch-b4819`, sesuai spesifikasi
integrasi §3.1. Skema id turunan di flow_util akan menghasilkan
`MatchingDispatch-c793c` — dan karena Langflow mengabaikan tweak ke node yang
tidak ada tanpa galat, id yang berbeda berarti setiap job portal berjalan dengan
payload kosong.

Menjalankan (dari dalam container, sesudah komponennya termuat):
    docker exec synchrono-langflow python /synchrono/infra/buat_flow_matching_dispatch.py
"""

from flow_util import bangun, katalog, masuk

ID_SPESIFIKASI = {"MatchingDispatch": "MatchingDispatch-b4819"}

DISPATCH = [
    ("MatchingDispatch", None, "accepted"),
    ("ChatOutput", "input_value", "message"),
]


def main() -> int:
    token = masuk()
    per_nama = katalog(token, "matching")
    fid, node = bangun(
        token, "Synchrono Matching Dispatch",
        "Terima job matching dari portal, lepas pekerja, balas IN_PROGRESS.",
        "matching-dispatch", per_nama, DISPATCH, id_paksa=ID_SPESIFIKASI)
    print(f"  endpoint : POST /api/v1/run/matching-dispatch?stream=false")
    print(f"  node     : {node}")
    if node != ID_SPESIFIKASI["MatchingDispatch"]:
        print("  PERINGATAN: id node TIDAK sama dengan spesifikasi")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
