"""
Siapkan SeaweedFS LOKAL dengan tata letak folder yang SAMA dengan server.

    docker compose exec synchrono-service python infra/siapkan_seaweed.py

KENAPA LOKAL, BUKAN SERVER

Benchmark ini mengukur service-nya, bukan jaringan kantor. Membaca parquet
lewat VPN menambahkan puluhan sampai ratusan milidetik yang berubah-ubah
sepanjang hari, dan angka sebesar itu akan menenggelamkan justru selisih yang
sedang dicari. Lebih buruk lagi: kedua sisi yang dibandingkan akan menanggung
jitter yang berbeda, karena tidak diuji pada detik yang sama.

KENAPA TETAP DENGAN NAMA FOLDER SERVER

Kalau tata letaknya berbeda, `parquetKey` yang dipakai saat menguji bukan
`parquetKey` yang akan dikirim portal — dan perbedaan sekecil apa pun di situ
selalu baru ketahuan saat dipasang di server. Jadi nama bucket dan bentuk
kuncinya disamakan persis:

    syncrono-uploads/uploads/{fileId}/data.parquet      berkas unggahan
    syncrono-master/wilayah/master_wilayah_nik.parquet  rujukan wilayah
    syncrono-exports/                                   keluaran portal

Dengan rujukan wilayah ikut disalin ke SeaweedFS lokal, `WILAYAH_S3_ENDPOINT`
bisa DIKOSONGKAN: `_wilayah.muat()` lalu memakai secret utama, dan tidak ada
satu pun permintaan yang keluar ke jaringan selama benchmark berjalan.

Berkas unggahan disalin dari `bucket-test`, yang isinya berkas uji yang sudah
dibangkitkan `buat_data_uji_grading.py`. Tidak ada data baru yang dikarang di
sini.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, "/synchrono/lib")

import duckdb  # noqa: E402

# ── Sumber dan tujuan ──────────────────────────────────────────────────────

WILAYAH_SUMBER = os.getenv(
    "SIAP_WILAYAH_SUMBER",
    "s3://syncrono-master/wilayah/master_wilayah_nik.parquet",
)
SERVER_ENDPOINT = os.getenv("SIAP_SERVER_ENDPOINT", "172.16.12.98:8333")
SERVER_KEY = os.getenv("SIAP_SERVER_KEY", "ADMIN")
SERVER_SECRET = os.getenv("SIAP_SERVER_SECRET", "Password1234")

LOKAL_ENDPOINT = os.getenv("S3_ENDPOINT", "seaweedfs:8333")
LOKAL_KEY = os.getenv("S3_ACCESS_KEY", "synchrono")
LOKAL_SECRET = os.getenv("S3_SECRET_KEY", "synchrono123")

BUCKET_UPLOAD = "syncrono-uploads"
BUCKET_MASTER = "syncrono-master"
BUCKET_EXPORT = "syncrono-exports"

# Bucket berkas uji yang sudah ada — sumber salinan unggahan.
BUCKET_UJI = os.getenv("SIAP_BUCKET_UJI", "bucket-test")


def bersih(endpoint: str) -> str:
    return re.sub(r"^https?://", "", endpoint).rstrip("/")


def buka() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")

    # Secret ber-SCOPE, jadi server dan lokal bisa dipakai dalam satu statement
    # COPY. Tanpa SCOPE, yang belakangan menimpa yang duluan dan salinan
    # lintas-SeaweedFS jadi mustahil.
    for bucket in (BUCKET_UPLOAD, BUCKET_MASTER, BUCKET_EXPORT, BUCKET_UJI, "synchrono"):
        nama = bucket.replace("-", "_")
        con.execute(f"""
            CREATE OR REPLACE SECRET lokal_{nama} (
                TYPE s3, KEY_ID '{LOKAL_KEY}', SECRET '{LOKAL_SECRET}',
                ENDPOINT '{bersih(LOKAL_ENDPOINT)}', URL_STYLE 'path',
                USE_SSL false, SCOPE 's3://{bucket}'
            )
        """)
    return con


def buka_server() -> duckdb.DuckDBPyConnection:
    """
    Koneksi TERPISAH khusus membaca SeaweedFS server.

    Tidak digabung dengan koneksi lokal, dan itu bukan kerapian semata.
    Bucket rujukannya bernama sama di kedua sisi (`syncrono-master`), jadi dua
    secret ber-SCOPE identik akan hidup berdampingan dan DuckDB memilih salah
    satunya tanpa bisa diatur — pada percobaan pertama ia memilih yang lokal
    dan menjawab 404 untuk berkas yang jelas-jelas ada di server.

    Dua koneksi menghapus pilihan itu sama sekali: yang ini hanya tahu server,
    yang satunya hanya tahu lokal.
    """
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute(f"""
        CREATE OR REPLACE SECRET server_wilayah (
            TYPE s3, KEY_ID '{SERVER_KEY}', SECRET '{SERVER_SECRET}',
            ENDPOINT '{bersih(SERVER_ENDPOINT)}', URL_STYLE 'path',
            USE_SSL {str(SERVER_ENDPOINT.lower().startswith("https://")).lower()}
        )
    """)
    return con


# ── Langkah ────────────────────────────────────────────────────────────────

def salin_wilayah(con) -> bool:
    """
    Tarik rujukan wilayah dari SeaweedFS server ke lokal, path-nya sama persis.

    Butuh VPN. Kalau salinannya sudah ada dari sebelumnya, ketiadaan VPN bukan
    penghalang — itu justru gunanya menyalin.
    """
    tujuan = f"s3://{BUCKET_MASTER}/wilayah/master_wilayah_nik.parquet"

    try:
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{tujuan}')").fetchone()[0]
        print(f"  wilayah : sudah ada di lokal, {n:,} baris — dilewati")
        return True
    except Exception:  # noqa: BLE001 — belum ada; memang itu yang diperiksa
        pass

    # Singgah dulu di disk container. 7 ribu baris, jadi biayanya tidak berarti,
    # dan ia yang memungkinkan sumber dan tujuan dipegang dua koneksi berbeda.
    antara = "/tmp/master_wilayah_nik.parquet"
    try:
        srv = buka_server()
        n = srv.execute(
            f"SELECT count(*) FROM read_parquet('{WILAYAH_SUMBER}')").fetchone()[0]
        srv.execute(f"COPY (SELECT * FROM read_parquet('{WILAYAH_SUMBER}')) "
                    f"TO '{antara}' (FORMAT PARQUET)")
        srv.close()

        con.execute(f"COPY (SELECT * FROM read_parquet('{antara}')) "
                    f"TO '{tujuan}' (FORMAT PARQUET)")
        os.remove(antara)
        print(f"  wilayah : {n:,} baris disalin dari server -> {tujuan}")
        return True
    except Exception as e:  # noqa: BLE001
        pesan = " ".join(str(e).split())[:160]
        print(f"  wilayah : GAGAL disalin — {type(e).__name__}: {pesan}")
        print("            Nyalakan VPN lalu jalankan ulang. Tanpa berkas ini "
              "grading tetap jalan,")
        print("            tapi pemeriksaan wilayah DIMATIKAN dan hasilnya "
              "tidak setara dengan produksi.")
        return False


def salin_unggahan(con) -> int:
    """Salin berkas uji ke tata letak kunci milik server."""
    try:
        berkas = [r[0] for r in con.execute(
            f"SELECT file FROM glob('s3://{BUCKET_UJI}/uploads/**/data.parquet') "
            f"ORDER BY 1").fetchall()]
    except Exception as e:  # noqa: BLE001
        print(f"  unggahan: bucket {BUCKET_UJI} tidak terbaca — {e}")
        return 0

    if not berkas:
        print(f"  unggahan: {BUCKET_UJI} kosong. Bangkitkan dulu berkas ujinya:")
        print("            docker exec synchrono-langflow python "
              "/synchrono/infra/buat_data_uji_grading.py")
        return 0

    disalin = 0
    for asal in berkas:
        kunci = asal.split(f"s3://{BUCKET_UJI}/", 1)[1]
        tujuan = f"s3://{BUCKET_UPLOAD}/{kunci}"
        try:
            con.execute(f"SELECT 1 FROM read_parquet('{tujuan}') LIMIT 1")
            continue  # sudah ada
        except Exception:  # noqa: BLE001
            pass
        n = con.execute(f"SELECT count(*) FROM read_parquet('{asal}')").fetchone()[0]
        con.execute(f"COPY (SELECT * FROM read_parquet('{asal}')) "
                    f"TO '{tujuan}' (FORMAT PARQUET)")
        print(f"  unggahan: {kunci:<40s} {n:>8,} baris")
        disalin += 1

    if not disalin:
        print(f"  unggahan: {len(berkas)} berkas sudah ada — dilewati")
    return disalin


def buat_bucket_export(con) -> None:
    """
    Bucket kosong tidak bisa dibuat lewat DuckDB — SeaweedFS baru membuat
    bucket saat objek pertama masuk. Jadi ditanam satu penanda nol baris,
    semata supaya bucket-nya ADA dan portal yang menulis ke sana tidak
    menemukan NoSuchBucket.
    """
    tujuan = f"s3://{BUCKET_EXPORT}/.bucket-init.parquet"
    try:
        con.execute(f"SELECT 1 FROM read_parquet('{tujuan}') LIMIT 1")
        print(f"  export  : {BUCKET_EXPORT} sudah ada — dilewati")
        return
    except Exception:  # noqa: BLE001
        pass
    con.execute(f"COPY (SELECT 1 AS penanda WHERE false) TO '{tujuan}' "
                f"(FORMAT PARQUET)")
    print(f"  export  : {BUCKET_EXPORT} dibuat")


def main() -> int:
    print(f"SeaweedFS lokal : {LOKAL_ENDPOINT}")
    print("tata letak      : seperti server "
          "(syncrono-uploads / -master / -exports)\n")

    con = buka()
    ada_wilayah = salin_wilayah(con)
    salin_unggahan(con)
    buat_bucket_export(con)

    print("\nisi akhir:")
    for b in (BUCKET_UPLOAD, BUCKET_MASTER, BUCKET_EXPORT):
        try:
            n = len(con.execute(f"SELECT file FROM glob('s3://{b}/**')").fetchall())
            print(f"  s3://{b:<20s} {n:>3} berkas")
        except Exception as e:  # noqa: BLE001
            print(f"  s3://{b:<20s} tidak terbaca: {type(e).__name__}")

    if not ada_wilayah:
        print("\nPERINGATAN: rujukan wilayah belum ada. "
              "Hasil grading akan berbeda dari produksi.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
