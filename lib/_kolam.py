"""
Kolam koneksi DuckDB untuk node Langflow.

KENAPA INI ADA

`buka_koneksi()` bukan operasi murah. Tiap panggilan meng-INSTALL dan me-LOAD
dua extension, membuat SECRET S3, lalu meng-ATTACH PostgreSQL. Diukur di dalam
container pada 21 September: **327,6 ms**, tujuh kali berturut, panggilan
pertama dibuang.

Empat node API — status, dispatch, baca aturan, ubah aturan — membayar ongkos
itu SETIAP PANGGILAN, padahal pekerjaan yang sesungguhnya mereka lakukan hanya
sekitar 100 ms. Pembedahan satu panggilan `grading-status` yang memakan 507 ms
terurai begini:

    masuk-keluar HTTP                 15,7 ms
    buka koneksi + ATTACH            327,6 ms   <- ini
    membaca barisnya                 102,9 ms
    merakit graf & membungkus         60,8 ms

Jadi dua pertiga waktunya bukan Langflow, bukan basis data, dan bukan logika
grading. Itu ongkos membuka pintu yang sama berulang-ulang.

KENAPA INI DULU DIKIRA TIDAK MUNGKIN

Catatan di kolam milik service pembanding menyatakan bahwa di Langflow
"tidak ada tempat untuk menyimpannya, karena node hidup hanya selama satu
eksekusi". Bagian pertamanya keliru: yang hidup sesaat adalah OBJEK NODE-nya,
sedangkan MODUL Python-nya tetap terimpor selama proses Langflow hidup. Kolam
di level modul karena itu bertahan antar eksekusi node, persis seperti di
service.

YANG DIKUMPULKAN, DAN YANG SENGAJA TIDAK

Hanya node yang pendek dan tidak menyimpan keadaan apa pun di koneksinya:

    dikumpulkan       api1_dispatch, api2_status, api1_get_rules,
                      api2_update_rule
    TIDAK             n1_open_session (matching), buka() (grading G1),
                      _worker (thread latar)

Ketiga yang terakhir memegang koneksinya lama dan MENINGGALKAN VIEW di dalamnya
(`incoming_df`, `master_df`, `joined_df`). Mengembalikan koneksi semacam itu ke
kolam berarti peminjam berikutnya mewarisi view milik pekerjaan orang lain —
dan karena namanya sama, ia tidak akan mendapat galat, melainkan JAWABAN YANG
SALAH. Lagi pula di sana 328 ms itu teramortisasi oleh ratusan milidetik kerja
nyata, jadi yang dibeli sedikit sementara yang dipertaruhkan banyak.

SATU ATURAN YANG TIDAK BOLEH DILANGGAR

Koneksi DuckDB TIDAK aman dipakai dua thread sekaligus. Antrean di sini
menjamin satu koneksi hanya dipegang satu peminjam pada satu waktu. Itu bukan
kehati-hatian berlebihan: Langflow menjalankan flow di beberapa worker, dan
node dispatch bahkan melepas thread latar sendiri.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from contextlib import contextmanager

from _shared import buka_koneksi

# Setara `DUCKDB_MODE` milik service, dengan nilai yang sama persis, supaya
# satu env var mengatur keduanya dengan arti yang sama:
#
#     kolam         koneksi dipakai ulang                <- bawaan
#     per_request   buka-tutup tiap panggilan            <- perilaku lama
#
# `per_request` sengaja dipertahankan: ia jalan mundur ke perilaku sebelum
# perubahan ini tanpa menyentuh kode, dan ia yang dipakai kalau ongkos kolam
# perlu diukur ulang berdampingan.
MODE = os.getenv("DUCKDB_MODE", "kolam").strip().lower()

UKURAN = int(os.getenv("DUCKDB_POOL_SIZE", "8"))

# Batas tunggu saat semua koneksi sedang dipakai. Lebih baik gagal jujur
# daripada menggantung sampai klien timeout sendiri tanpa penjelasan.
TUNGGU = float(os.getenv("DUCKDB_POOL_TIMEOUT", "30"))

# Koneksi yang lebih tua dari ini dibuang saat dikembalikan, bukan dipakai lagi.
#
# Kolam service tidak punya ini karena servicenya dinyalakan ulang saat deploy.
# Langflow di server hidup berhari-hari, dan ATTACH ke PostgreSQL yang sudah
# lama menganggur bisa mati diam-diam — bukan saat diperiksa, melainkan saat
# dipakai, yaitu di tengah permintaan pengguna. Mendaur ulang koneksi secara
# berkala jauh lebih murah daripada memburu galat yang muncul sekali sehari.
#
# 0 = tidak pernah didaur ulang.
UMUR_MAKS = float(os.getenv("DUCKDB_POOL_MAX_UMUR", "1800"))


class KolamPenuh(RuntimeError):
    """Semua koneksi terpakai dan batas tunggu terlampaui."""


class Kolam:
    def __init__(self, ukuran: int = UKURAN) -> None:
        self.ukuran = ukuran
        # LIFO, bukan FIFO: koneksi yang baru saja dipakai paling hangat
        # cache-nya, dan yang menganggur lama tidak dipaksa ikut berputar.
        self._siap: queue.LifoQueue = queue.LifoQueue()
        self._kunci = threading.Lock()
        self._dibuat = 0
        # Umur tiap koneksi, dikunci id()-nya. Disimpan terpisah supaya objek
        # koneksi DuckDB tidak perlu ditempeli atribut apa pun.
        self._lahir: dict[int, float] = {}

    def _boleh_buat(self) -> bool:
        with self._kunci:
            if self._dibuat < self.ukuran:
                self._dibuat += 1
                return True
        return False

    def _batalkan_hitungan(self) -> None:
        with self._kunci:
            self._dibuat -= 1

    def _uzur(self, con) -> bool:
        if not UMUR_MAKS:
            return False
        lahir = self._lahir.get(id(con))
        return lahir is not None and (time.monotonic() - lahir) > UMUR_MAKS

    @contextmanager
    def pinjam(self):
        """
        Pinjam satu koneksi, kembalikan otomatis saat blok selesai.

        Koneksi yang blok-nya berakhir dengan pengecualian TIDAK dikembalikan.
        Transaksi yang tergantung atau ATTACH yang putus akan menular ke
        permintaan berikutnya dan menghasilkan galat yang sama sekali tidak ada
        hubungannya dengan permintaan itu — jenis bug yang paling sulit
        dilacak. Lebih murah membuang koneksinya.
        """
        con = None
        baru = False
        while True:
            try:
                con = self._siap.get_nowait()
            except queue.Empty:
                break
            if self._uzur(con):
                self._buang(con)   # daur ulang; lalu coba ambil yang lain
                con = None
                continue
            break

        if con is None:
            if self._boleh_buat():
                baru = True
            else:
                try:
                    con = self._siap.get(timeout=TUNGGU)
                except queue.Empty as e:
                    raise KolamPenuh(
                        f"{self.ukuran} koneksi DuckDB semuanya terpakai lebih "
                        f"dari {TUNGGU:.0f} detik."
                    ) from e

        if baru:
            try:
                con = buka_koneksi()
            except Exception:
                self._batalkan_hitungan()
                raise
            self._lahir[id(con)] = time.monotonic()
            # Membuka koneksi memakan ~330 ms. Kalau baris ini muncul di tiap
            # permintaan dan bukan sesekali, kolamnya tidak dipakai ulang.
            print(f"[kolam] koneksi DuckDB baru dibuka "
                  f"({self._dibuat}/{self.ukuran})", flush=True)

        try:
            yield con
        except Exception:
            self._buang(con)
            raise
        else:
            self._siap.put(con)

    def _buang(self, con) -> None:
        self._batalkan_hitungan()
        self._lahir.pop(id(con), None)
        try:
            con.close()
        except Exception:  # noqa: BLE001 — sudah mau dibuang, apa pun galatnya
            pass

    def panaskan(self, jumlah: int = 1) -> None:
        """
        Buka beberapa koneksi sebelum panggilan pertama datang.

        TIDAK dipanggil otomatis saat modul diimpor. Langflow mengimpor berkas
        komponen saat memindai sidebar, jauh sebelum ada yang memanggil flow,
        dan menyambung ke PostgreSQL di tengah pemindaian itu membuat kegagalan
        sambungan muncul sebagai node yang hilang dari sidebar — persis jenis
        kegagalan diam yang paling mahal di sistem ini.

        Akibatnya panggilan pertama sesudah Langflow menyala tetap menanggung
        sendiri 328 ms itu. Sesudahnya tidak lagi.
        """
        simpan = []
        for _ in range(min(jumlah, self.ukuran)):
            if not self._boleh_buat():
                break
            try:
                con = buka_koneksi()
            except Exception:
                self._batalkan_hitungan()
                raise
            self._lahir[id(con)] = time.monotonic()
            simpan.append(con)
        for con in simpan:
            self._siap.put(con)

    def tutup(self) -> None:
        while True:
            try:
                self._siap.get_nowait().close()
            except queue.Empty:
                return
            except Exception:  # noqa: BLE001
                pass

    @property
    def menganggur(self) -> int:
        return self._siap.qsize()


class _PerPermintaan:
    """Perilaku lama: buka baru, tutup lagi, tiap panggilan."""

    ukuran = 0

    @contextmanager
    def pinjam(self):
        con = buka_koneksi()
        try:
            yield con
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    def panaskan(self, jumlah: int = 1) -> None:
        buka_koneksi().close()

    def tutup(self) -> None:
        pass

    @property
    def menganggur(self) -> int:
        return 0


kolam = Kolam() if MODE == "kolam" else _PerPermintaan()


def pinjam():
    return kolam.pinjam()
