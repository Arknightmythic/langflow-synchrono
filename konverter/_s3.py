"""
Unduh berkas dari S3 SAMBIL MENULISNYA KE DISK, sepotong demi sepotong.

KENAPA TIDAK PAKAI `read_blob()` DUCKDB SAJA

Karena ia mengembalikan satu objek `bytes` utuh. Diukur di container konverter:

    RAM     : 5.927 MB total, 3.571 MB tersedia
    disk    : 900.832 MB kosong

Dengan `read_blob`, batas ukuran berkas ditentukan RAM — sumber daya yang
paling sedikit tersedia — padahal disknya menganggur ratusan gigabita. Batas
2048 MB yang semula dipasang bahkan LEBIH BESAR daripada RAM yang tersisa:
berkas sebesar itu akan membunuh container sebelum sempat ditolak, dan
matinya lewat OOM kernel yang tidak meninggalkan pesan apa pun.

Dengan unduhan mengalir, pemakaian memorinya tetap beberapa ratus kilobita
berapa pun besar berkasnya. Batasnya berpindah ke disk dan waktu, yang
keduanya jauh lebih longgar dan jauh lebih mudah diterangkan.

Diukur pada data uji: dump pg_dump 15,66 MB memuat 200.000 baris, jadi sekitar
82 bita per baris. Berkas 1 GB berarti sekitar 13 juta baris.

KENAPA MENANDATANGANI SENDIRI

Yang dibutuhkan cuma satu GET bertanda tangan. Menambahkan boto3 hanya untuk
itu berarti menambah dependensi besar ke image yang justru dijaga tetap ramping,
sementara SigV4 untuk satu permintaan tanpa badan muat dalam beberapa puluh
baris pustaka standar.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import urllib.parse
import urllib.request

WILAYAH = "us-east-1"
KOSONG = hashlib.sha256(b"").hexdigest()
POTONG = 1024 * 1024


def _tanda(kunci: bytes, pesan: str) -> bytes:
    return hmac.new(kunci, pesan.encode(), hashlib.sha256).digest()


def _wewenang(metode: str, host: str, jalur: str, kunci: str, rahasia: str,
              amz: str, tgl: str) -> str:
    """Header Authorization SigV4 untuk satu permintaan tanpa badan."""
    # Jalurnya sudah ter-encode oleh pemanggil; SigV4 memakai bentuk yang sama
    # persis dengan yang dikirim di baris permintaan.
    kanonik = (f"{metode}\n{jalur}\n\n"
               f"host:{host}\nx-amz-content-sha256:{KOSONG}\nx-amz-date:{amz}\n\n"
               f"host;x-amz-content-sha256;x-amz-date\n{KOSONG}")
    lingkup = f"{tgl}/{WILAYAH}/s3/aws4_request"
    untuk_ditandatangani = (f"AWS4-HMAC-SHA256\n{amz}\n{lingkup}\n"
                            f"{hashlib.sha256(kanonik.encode()).hexdigest()}")

    k = f"AWS4{rahasia}".encode()
    for bagian in (tgl, WILAYAH, "s3", "aws4_request"):
        k = _tanda(k, bagian)
    tanda_tangan = hmac.new(k, untuk_ditandatangani.encode(),
                            hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={kunci}/{lingkup}, "
            f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
            f"Signature={tanda_tangan}")


def unduh(endpoint: str, bucket: str, kunci_objek: str, tujuan: str,
          akses: str, rahasia: str, ssl: bool = False,
          batas_bita: int | None = None) -> int:
    """
    Ambil satu objek ke berkas lokal. Kembalikan ukurannya.

    `batas_bita` diperiksa DUA KALI, dan itu disengaja: sekali dari
    `Content-Length` supaya berkas kebesaran ditolak sebelum satu bita pun
    diunduh, sekali lagi selagi mengalir supaya server yang tidak melaporkan
    panjangnya — atau melaporkannya keliru — tetap tidak bisa memenuhi disk.
    """
    host = endpoint.replace("http://", "").replace("https://", "").rstrip("/")
    jalur = "/" + bucket.strip("/") + "/" + urllib.parse.quote(
        kunci_objek.lstrip("/"), safe="/")

    t = datetime.datetime.now(datetime.timezone.utc)
    amz, tgl = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")

    permintaan = urllib.request.Request(
        f"{'https' if ssl else 'http'}://{host}{jalur}", method="GET",
        headers={
            "Host": host,
            "x-amz-date": amz,
            "x-amz-content-sha256": KOSONG,
            "Authorization": _wewenang("GET", host, jalur, akses, rahasia,
                                       amz, tgl),
        })

    jumlah = 0
    with urllib.request.urlopen(permintaan, timeout=120) as balasan:
        panjang = balasan.headers.get("Content-Length")
        if batas_bita and panjang and int(panjang) > batas_bita:
            raise ValueError(
                f"Berkas {int(panjang) / 2**20:.0f} MB melewati batas "
                f"{batas_bita / 2**20:.0f} MB. Ditolak sebelum diunduh.")
        with open(tujuan, "wb") as keluar:
            while potongan := balasan.read(POTONG):
                jumlah += len(potongan)
                if batas_bita and jumlah > batas_bita:
                    raise ValueError(
                        f"Berkas melewati batas {batas_bita / 2**20:.0f} MB "
                        f"selagi diunduh (server tidak melaporkan panjangnya "
                        f"dengan benar). Unduhan dihentikan.")
                keluar.write(potongan)
    return jumlah
