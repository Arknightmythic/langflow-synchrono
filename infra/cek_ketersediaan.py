"""
Berkas mana yang BENAR-BENAR bisa dipakai tanpa VPN?

Menyilangkan metadata `uploaded_files` di PostgreSQL dengan isi SeaweedFS yang
sesungguhnya. Metadata mencatat setiap unggahan yang pernah terjadi; parquet
yang lama sudah dibersihkan dari penyimpanan. Tanpa pemeriksaan ini, mudah
memilih file_id yang metadatanya ada tapi berkasnya tidak.

Dijalankan DI DALAM container (butuh httpfs):

    docker exec synchrono-langflow python /synchrono/infra/cek_ketersediaan.py
"""

import sys

sys.path.insert(0, "/synchrono/lib")

from _shared import buka_koneksi  # noqa: E402

con = buka_koneksi()

ada = {r[0] for r in con.execute("SELECT file FROM glob('s3://synchrono/**')").fetchall()}
print(f"{len(ada)} berkas ada di SeaweedFS lokal\n")

baris = con.execute("""
    SELECT file_id, grade, row_count, parquet_path, original_filename
      FROM pg.public.uploaded_files
     ORDER BY grade, upload_timestamp DESC
""").fetchall()

siap, hilang = [], 0
for file_id, grade, n, jalur, nama in baris:
    if jalur in ada:
        siap.append((file_id, grade, n, jalur, nama))
    else:
        hilang += 1

print(f"SIAP DIPAKAI OFFLINE: {len(siap)} berkas   (metadata tanpa berkas: {hilang})\n")
print(f"  {'file_id':10s} {'grade':>5s} {'baris':>9s}  nama")
print("  " + "-" * 70)
for file_id, grade, n, jalur, nama in siap:
    print(f"  {file_id:10s} {grade:>5} {n or 0:>9,}  {(nama or '')[:42]}")

print("\ncontoh perintah matching untuk salah satunya:")
if siap:
    f, g, _, jalur, _ = siap[0]
    print(f"  docker exec synchrono-langflow python /synchrono/run_local.py \\")
    print(f"      --file-id {f} --parquet {jalur} --grade {g}")
