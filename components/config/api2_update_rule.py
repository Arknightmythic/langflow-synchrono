"""
NODE API-4 — Update Grading Rule   (tombol simpan di menu "Rule")

{gradeId, criteria?, score?, matching?}  ->  hasil + daftar perubahan
{global: {grading?, matching?}}          ->  hasil + daftar perubahan (nilai global)

PERUBAHAN SEBAGIAN. Hanya field yang disebut yang berubah; sisanya dibiarkan.
UI tidak perlu mengirim ulang seluruh konfigurasi hanya untuk menggeser satu
ambang, dan dua orang yang menyunting bagian berbeda tidak saling menimpa.
Pengecualiannya `matching.weights` dan `matching.missingElements`, yang
diganti utuh (bobot harus berjumlah 100).

NILAI GLOBAL — bobot skor mutu grading, kombinasi grade E, selisih seri, dan
ambang nama ibu bertentangan — lewat kunci `global`, tanpa `gradeId`. Bentuk
dan perilakunya sama dengan PATCH /api/v1/config/global.

DIVALIDASI SEBELUM DITULIS. Perubahan digabungkan dulu ke salinan konfigurasi,
seluruhnya diperiksa, baru disimpan — sehingga konfigurasi yang merusak tidak
pernah sempat masuk ke basis data. Yang paling penting dijaga adalah grade yang
menjadi TIDAK PERNAH TERCAPAI: kalau ambang B dibuat sama ketat dengan A, semua
berkas tertangkap di A lebih dulu dan B mati tanpa pesan galat apa pun.

`dryRun: true` menjalankan seluruh validasi tanpa menulis — untuk tombol
"periksa" di UI sebelum benar-benar menyimpan.
"""

import json

from _config import perbarui, perbarui_global
from _kolam import pinjam
from _shared import BoolInput, Component, Message, MessageTextInput, Output


class GradingRuleUpdate(Component):
    display_name = "API 4. Update Grading Rule"
    description = "Ubah ambang grading/matching satu grade, dengan validasi."
    icon = "save"
    name = "GradingRuleUpdate"

    inputs = [
        MessageTextInput(
            name="payload", display_name="Payload JSON", required=True,
            info=('{"gradeId":2,"criteria":{"minCompleteness":{"tempat_lahir":0.75}},'
                  '"score":{"min":72},"updatedBy":"reno"}'),
        ),
        BoolInput(
            name="dry_run", display_name="Dry Run", value=False,
            info="Validasi saja, tidak menulis apa pun.",
        ),
    ]
    outputs = [Output(display_name="Result", name="result", method="simpan")]

    def simpan(self) -> Message:
        teks = (self.payload or "").strip()
        if not teks:
            raise ValueError("payload kosong")
        try:
            muatan = json.loads(teks)
        except json.JSONDecodeError as e:
            raise ValueError(f"payload bukan JSON yang sah: {e}") from e
        if not isinstance(muatan, dict):
            raise ValueError("payload harus objek JSON")

        # dryRun boleh datang dari muatan maupun dari kolom di kanvas.
        kering = bool(muatan.get("dryRun", False)) or bool(self.dry_run)
        oleh = muatan.get("updatedBy") or muatan.get("updated_by")
        gid = muatan.get("gradeId", muatan.get("grade_id"))

        if "global" in muatan:
            if gid is not None:
                raise ValueError("Pilih salah satu: `gradeId` (aturan satu grade) "
                                 "atau `global` (nilai global), bukan keduanya.")
            isi = muatan["global"]
            if not isinstance(isi, dict):
                raise ValueError("`global` harus objek {grading?, matching?}.")
            label = "global"
            with pinjam() as con:
                hasil = perbarui_global(
                    con, {k: isi.get(k) for k in ("grading", "matching")},
                    oleh=oleh, dry_run=kering)
        else:
            if gid is None:
                raise ValueError("Field `gradeId` wajib diisi (atau `global` untuk "
                                 "nilai global).")
            try:
                gid = int(gid)
            except (TypeError, ValueError) as e:
                raise ValueError(f"gradeId harus angka, dapat: {gid!r}") from e
            label = f"grade {gid}"
            with pinjam() as con:
                hasil = perbarui(
                    con, gid,
                    {k: muatan.get(k) for k in ("criteria", "score", "matching")},
                    oleh=oleh, dry_run=kering,
                )

        if hasil["problems"]:
            print(f"[API4] {label} DITOLAK: {len(hasil['problems'])} masalah")
            for p in hasil["problems"]:
                print(f"   - {p}")
        else:
            aksi = "divalidasi" if kering else "disimpan"
            print(f"[API4] {label} {aksi}, "
                  f"{len(hasil.get('changed') or [])} field berubah")

        return Message(text=json.dumps(hasil, ensure_ascii=False, default=str))
