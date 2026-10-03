import { currentTarget } from "../api.js";
import { clear, h } from "../dom.js";
import { FIELD_DOCS } from "../fields.js";
import { busy, button, card, copyBlock, curlCommand, notice, pageHead } from "../ui.js";

const ENDPOINTS = [
  ["GET", "/api/v1/config/rules", "Semua grade A–F, global, dan configVersion.", "200 · 401"],
  ["GET", "/api/v1/config/rules/{gradeId}", "Satu grade (1 = A … 6 = F), bentuk sama dengan di atas.", "200 · 401 · 404 · 422"],
  ["PATCH", "/api/v1/config/rules/{gradeId}", "Ubah sebagian satu grade: criteria, score, matching.", "200 · 400 · 401 · 409 · 422"],
  ["PATCH", "/api/v1/config/global", "Ubah nilai global: grading.*, matching.*.", "200 · 400 · 401 · 409 · 422"],
  ["GET", "/api/v1/config/history?gradeId=&limit=", "Riwayat perubahan, terbaru dulu. limit 1–500 (bawaan 50).", "200 · 401 · 422"],
  ["GET", "/api/v1/config/versions/{configVersion}", "Isi konfigurasi lengkap satu versi (12 heksa).", "200 · 401 · 404 · 422"],
];

const STATUS = [
  ["200", "Berhasil. Pada PATCH: applied = true (atau dryRun = true). Baca changed, warnings, configVersion."],
  ["400", "Bentuk muatan salah: field tak dikenal, tipe keliru, mengubah blocking, kriteria untuk E/F. Badan: {\"detail\": \"…\"} — tampilkan apa adanya."],
  ["401", "x-api-key tidak ada atau salah."],
  ["404", "Grade atau versi tidak ada."],
  ["409", "Ditolak validasi isi. Badan sama dengan 200 tetapi applied = false, after = null, dan problems berisi daftar alasan. Tidak ada yang ditulis."],
  ["422", "Validasi FastAPI: gradeId di luar 1–6, configVersion bukan 12 heksa, atau tipe di tingkat atas salah (mis. dryRun bukan boolean). Badan: {\"detail\": [{loc, msg, type}]}."],
];

const RULES = [
  "PATCH bersifat sebagian: hanya field yang disebut yang berubah.",
  "matching.weights dan matching.missingElements DIGANTI UTUH (bobot harus berjumlah 100).",
  "minCompleteness dan nameCleaning digabung per kunci; global.grading.scoreWeights digabung per kunci; gradeECombinations diganti utuh.",
  "null = kembali ke bawaan untuk weights, missingElements, nameCleaning, dateMatch, dan semua kunci global (kembali ke env/bawaan).",
  "null pada ambang punya arti lain: autoMissingMax / reviewMissingCount null = tanpa syarat; minCompleteness.* / minNikTrusted null = tidak diperiksa.",
  "autoScoreMin, reviewScoreMin, reviewScoreMax wajib angka 0–100; persen bobot 0–100; kelengkapan & NIK tepercaya berupa pecahan 0–1.",
  "Urutan bobot dikirim dan dipertahankan (objek JSON berurutan, atau [[elemen, persen], …]); urutan ikut menentukan digit terakhir skor.",
  "Grade E dan F tidak punya criteria (criteria = null, note menjelaskan); grade F tidak punya weights/missingElements/nameCleaning/dateMatch.",
  "matching.blocking, analysis, availableElements, updatedAt, updatedBy hanya dibaca — mengirimnya = 400.",
  "dryRun: true menjalankan seluruh validasi dan simulasi tanpa menulis; after berisi hasil simulasi, changed berisi perubahan yang akan terjadi.",
  "Konfigurasi dibaca sekali di awal setiap job; perubahan tidak mengganggu job yang sedang berjalan.",
];

const FLOW = [
  "Muat GET /rules. Tampilkan keenam grade; pakai criteriaEditable dan note untuk menjelaskan kenapa E/F tidak punya kriteria.",
  "Isi pilihan elemen bobot / elemen kosong dari matching.availableElements (bila null, pakai matchingElements).",
  "Tampilkan matching.analysis.maxScoreByMissingCount dan analysis.warnings di samping ambang — itulah akibat aturan yang sedang berlaku.",
  "Saat pengguna mengubah: bangun muatan HANYA dari field yang berubah (lihat aturan di atas).",
  "Kirim PATCH dengan dryRun: true. 409 → tampilkan problems. 200 → tampilkan warnings dan changed (from → to).",
  "Kirim lagi dengan dryRun: false dan updatedBy = nama pengguna. Simpan configVersion dari balasan.",
  "Muat ulang GET /rules dan riwayat. Peringatan tidak menolak penyimpanan, tetapi sebaiknya diminta konfirmasi.",
];

const EXAMPLES = [
  ["Ubah bobot grade C (diganti utuh, jumlah 100)", "PATCH", "/api/v1/config/rules/3",
    { matching: { weights: { nama: 60, tanggal_lahir: 25, tempat_lahir: 15 } }, updatedBy: "nama-operator", dryRun: true }],
  ["Tanggal lahir harus sama persis di grade C", "PATCH", "/api/v1/config/rules/3",
    { matching: { dateMatch: "exact" }, updatedBy: "nama-operator", dryRun: true }],
  ["Nyalakan pembersihan gelar di grade B", "PATCH", "/api/v1/config/rules/2",
    { matching: { nameCleaning: { titles: true } }, updatedBy: "nama-operator", dryRun: true }],
  ["Longgarkan kelengkapan tempat lahir grade B", "PATCH", "/api/v1/config/rules/2",
    { criteria: { minCompleteness: { tempat_lahir: 0.65 } }, updatedBy: "nama-operator", dryRun: true }],
  ["Kembalikan bobot & elemen kosong grade C ke bawaan", "PATCH", "/api/v1/config/rules/3",
    { matching: { weights: null, missingElements: null, nameCleaning: null }, updatedBy: "nama-operator", dryRun: true }],
  ["Ubah bobot skor mutu (global, digabung per kunci)", "PATCH", "/api/v1/config/global",
    { grading: { scoreWeights: { kelengkapan: 0.5, nik_tepercaya: 0.5 } }, updatedBy: "nama-operator", dryRun: true }],
];

// Line breaks are allowed after dots without adding characters to copied text.
function codePath(path) {
  return h("code", {}, path.split(".").flatMap((part, i) => (i ? [h("wbr"), `.${part}`] : [part])));
}

function table(head, rows, className = "") {
  return h("div", { class: "table-wrap" }, h("table", { class: `table-tight ${className}`.trim() },
    h("thead", {}, h("tr", {}, head.map((t) => h("th", { text: t })))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) => h("td", {}, c)))))));
}

export function renderGuide(ctx, root) {
  const target = currentTarget();
  const base = target?.url || window.location.origin;
  const demoSlot = h("div");
  const demo = button("Jalankan contoh dry run", { iconName: "refresh" });
  demo.addEventListener("click", () => busy(demo, async () => {
    clear(demoSlot);
    try {
      const result = await ctx.api.patchGrade(3, { matching: { dateMatch: "exact" }, updatedBy: "contoh-panduan", dryRun: true });
      demoSlot.append(h("p", { class: "muted small", text: "PATCH /api/v1/config/rules/3 {\"matching\": {\"dateMatch\": \"exact\"}, \"dryRun\": true} — tidak ada yang ditulis:" }),
        copyBlock(JSON.stringify(result, null, 2)));
    } catch (error) {
      demoSlot.append(notice("critical", null, error.message));
    }
  }));

  root.append(
    pageHead("Panduan API konfigurasi", "Untuk tim portal yang membangun menu konfigurasinya sendiri. UI ini memakai API yang sama persis — buka \"Lihat muatan\" di halaman grade untuk melihat muatan nyata."),
    card("Dasar", {},
      h("dl", { class: "kv" },
        h("dt", { text: "Base URL" }), h("dd", {}, h("code", { text: `${base}/api/v1/config` })),
        h("dt", { text: "Autentikasi" }), h("dd", {}, "header ", h("code", { text: "x-api-key: <kunci>" }), " (atau query ?x-api-key=)"),
        h("dt", { text: "Format" }), h("dd", { text: "JSON. PATCH memakai Content-Type: application/json." }),
        h("dt", { text: "CORS" }), h("dd", { text: "Service tidak mengirim header CORS: panggil dari backend portal (atau lewat proxy seperti UI ini), bukan langsung dari browser." }),
        h("dt", { text: "Kontrak Langflow" }), h("dd", {}, "Isi yang sama juga tersedia lewat ", h("code", { text: "POST /api/v1/run/config-rules" }),
          " (tweak grade_id) dan ", h("code", { text: "POST /api/v1/run/config-rules-update" }),
          " (payload {\"gradeId\": 3, …} atau {\"global\": {…}}); di sana penolakan validasi dibalas 200 dengan applied = false."))),
    card("Endpoint", {}, table(["Metode", "Path", "Fungsi", "Status"],
      ENDPOINTS.map(([m, p, d, s]) => [h("code", { text: m }), h("code", { text: p }), d, h("span", { class: "nowrap", text: s })]))),
    card("Kode status", {}, table(["Status", "Arti & penanganan"], STATUS.map(([s, d]) => [h("strong", { text: s }), d]))),
    card("Bentuk muatan & balasan", {},
      h("h3", { text: "PATCH /rules/{gradeId}" }),
      copyBlock(JSON.stringify({ criteria: { minCompleteness: { tempat_lahir: 0.75 } }, score: { min: 72 },
        matching: { autoScoreMin: 86, weights: { nama: 80, tempat_lahir: 10, nama_ibu: 10 } }, updatedBy: "nama-operator", dryRun: true }, null, 2)),
      h("h3", { style: { marginTop: "12px" }, text: "PATCH /global" }),
      copyBlock(JSON.stringify({ grading: { scoreWeights: { kelengkapan: 0.5, nik_tepercaya: 0.5 } },
        matching: { conflictEpsilon: 0.5 }, updatedBy: "nama-operator", dryRun: true }, null, 2)),
      h("h3", { style: { marginTop: "12px" }, text: "Balasan PATCH (200 dan 409)" }),
      copyBlock(JSON.stringify({ applied: true, dryRun: false, problems: [], warnings: ["…"], before: "{grade/global sebelum}",
        after: "{grade/global sesudah; null bila 409}", changed: [{ section: "matching", field: "weights.nama", from: 80, to: 70 }],
        configVersion: "a1b2c3d4e5f6" }, null, 2)),
      h("div", { class: "add-row", style: { marginTop: "8px" } }, demo), demoSlot),
    card("Aturan mengubah", {}, h("ul", {}, RULES.map((r) => h("li", { text: r })))),
    card("Alur UI yang disarankan", {}, h("ol", {}, FLOW.map((r) => h("li", { text: r })))),
    card("Referensi field", { sub: "Bagian per grade: criteria (A–D), score (A–F), matching (A–F; bobot dkk. A–E)." },
      table(["Field", "Arti", "Tipe & rentang", "null berarti", "Grade", "Akibat"],
        FIELD_DOCS.map((f) => [codePath(f.path), f.label, `${f.type}; ${f.range}`, f.nullMeaning, f.grades, f.effect]), "ref")),
    card("Contoh", { sub: "Selalu dryRun: true dulu, baca warnings, baru kirim tanpa dryRun. $API_KEY diganti kunci Anda." },
      EXAMPLES.map(([title, method, path, body]) => h("div", { class: "stack", style: { marginBottom: "14px" } },
        h("h3", { text: title }), copyBlock(curlCommand(method, path, body))))),
    card("Membaca kode UI ini", {}, h("ul", {},
      h("li", {}, h("code", { text: "web/js/api.js" }), " — klien REST dan penanganan status."),
      h("li", {}, h("code", { text: "web/js/views/grade.js" }), " — membangun muatan PATCH hanya dari field yang berubah (buildPatch)."),
      h("li", {}, h("code", { text: "web/js/analysis.js" }), " — salinan rumus analisis & validasi service, untuk umpan balik seketika."),
      h("li", {}, h("code", { text: "web/js/fields.js" }), " — label dan penjelasan setiap field."))),
  );
  return null;
}
