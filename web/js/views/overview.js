import { badge, fmtDate, fmtNum, fmtPct, h } from "../dom.js";
import { currentTarget } from "../api.js";
import { DATE_MATCH, ELEMENT_LABELS, NAME_CLEANING, NIK_COLUMN, elementLabel } from "../fields.js";
import { bandProblems } from "../analysis.js";
import { bandStrip, button, card, copyBlock, gradeLink, notice, pageHead, sourceBadge, weightCell } from "../ui.js";

const CRITERIA_ELEMENTS = ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"];
const SHORT = { nik: "NIK", nama: "Nama", tempat_lahir: "Tempat lahir", tanggal_lahir: "Tgl lahir", jenis_kelamin: "Jenis kel.", nama_ibu: "Nama ibu" };

export function autoText(m) {
  if (m.autoScoreMin == null) return "tidak pernah (autoScoreMin kosong)";
  const missing = m.autoMissingMax == null ? "kosong berapa pun" : `kosong ≤ ${m.autoMissingMax}`;
  return `${missing} dan skor ≥ ${fmtNum(m.autoScoreMin)}`;
}

export function reviewText(m) {
  if (m.reviewScoreMin == null || m.reviewScoreMax == null) return "tidak pernah";
  const missing = m.reviewMissingCount == null ? "kosong berapa pun" : `kosong tepat ${m.reviewMissingCount}`;
  return `${missing} dan ${fmtNum(m.reviewScoreMin)} ≤ skor < ${fmtNum(m.reviewScoreMax)}`;
}

function cleaningText(nc) {
  if (!nc) return "—";
  const on = Object.keys(NAME_CLEANING).filter((k) => nc[k]).map((k) => NAME_CLEANING[k].label.toLowerCase());
  return on.length ? on.join(", ") : "mati";
}

function collectWarnings(rules) {
  const items = [];
  for (const grade of rules.grades) {
    for (const text of grade.matching?.analysis?.warnings || []) items.push({ grade, text });
  }
  for (const text of rules.global?.analysis?.warnings || []) items.push({ grade: null, text });
  return items;
}

function versionCard(ctx) {
  const rules = ctx.state.rules;
  const target = currentTarget();
  const last = h("dd", { class: "muted", text: "memuat…" });
  ctx.api.history({ limit: 1 }).then((data) => {
    const item = data?.items?.[0];
    last.className = "";
    last.textContent = item
      ? `${fmtDate(item.at)} oleh ${item.by || "(tanpa nama)"} — ${item.scope === "global" ? "global" : `grade ${item.gradeLetter}`}, ${item.changes.length} field`
      : "belum ada perubahan lewat API";
  }).catch(() => { last.textContent = "riwayat tidak bisa dibaca"; });
  return card("Konfigurasi berlaku", {
    actions: [button("Riwayat", { iconName: "clock", onClick: () => ctx.navigate("#/riwayat") })],
  }, h("dl", { class: "kv" },
    h("dt", { text: "Service" }), h("dd", {}, target.name, " ", h("span", { class: "muted", text: target.url })),
    h("dt", { text: "configVersion" }), h("dd", {}, h("code", { text: rules.configVersion })),
    h("dt", { text: "Dimuat" }), h("dd", { text: ctx.state.loadedAt?.toLocaleString("id-ID") || "—" }),
    h("dt", { text: "Perubahan terakhir" }), last));
}

function warningsCard(rules) {
  const items = collectWarnings(rules);
  const body = items.length
    ? items.map(({ grade, text }) => notice("warning", grade ? `Grade ${grade.gradeLetter}` : "Global",
      h("div", {}, h("div", { text }), h("a", { href: grade ? `#/grade/${grade.gradeId}` : "#/global", text: "Buka pengaturan" }))))
    : [notice("good", null, "Tidak ada peringatan dari analisis service.")];
  return card("Peringatan", { sub: "Konfigurasi sah, tetapi akibatnya mungkin tidak disangka. Tidak menolak penyimpanan." }, body);
}

function step(title, text, keys) {
  return h("div", { class: "flow-step" }, h("h3", { text: title }), h("p", { text }),
    keys?.length ? h("div", { class: "chips" }, keys.map(([label, href]) => (href
      ? h("a", { class: "chip chip--key", href, text: label })
      : h("span", { class: "chip chip--key", text: label })))) : null);
}

function flowCard(rules) {
  const sw = rules.global.grading.scoreWeights;
  const m = rules.global.matching;
  return card("Alur penilaian", { sub: "Di mana setiap pengaturan dipakai" }, h("div", { class: "flow" },
    h("div", { class: "flow-row" }, h("div", { class: "flow-title", text: "Grading" }), h("div", { class: "flow-steps" },
      step("1 · Kriteria A–D", "Dievaluasi menurut urutan (order). Berkas masuk grade pertama yang semua syaratnya terpenuhi: kolom NIK, kelengkapan minimal per elemen, NIK tepercaya minimal. Grade nonaktif dilewati.",
        [["criteria.*", "#/grade/1"]]),
      step("2 · Kombinasi E", "Bila tidak ada A–D yang terpenuhi: berkas yang memuat SEMUA kolom salah satu kombinasi menjadi grade E (cukup kolomnya ada).",
        [["gradeECombinations", "#/global"]]),
      step("3 · Grade F", "Selain itu grade F. canProceed = false: berkas tidak bisa dicocokkan sampai kolomnya dipetakan.",
        [["score.canProceed", "#/grade/6"]]),
      step("4 · Skor mutu", `pita min + (max − min) × mutu. Mutu = ${fmtNum(sw.kelengkapan)} × kelengkapan rata-rata + ${fmtNum(sw.nik_tepercaya)} × porsi NIK tepercaya (tanpa kolom NIK: mutu = kelengkapan). Dibulatkan.`,
        [["score.min/max", null], ["scoreWeights", "#/global"]]))),
    h("div", { class: "flow-row" }, h("div", { class: "flow-title", text: "Matching" }), h("div", { class: "flow-steps" },
      step("Pass 1 · NIK + nama persis", `NIK tepercaya sama dan nama sama persis → AUTO skor 100. Dibatalkan bila atribut bertentangan; nama ibu bertentangan bila kemiripannya < ${fmtNum(m.contradictionJw)}.`,
        [["contradictionJw", "#/global"], ["nameCleaning", null]]),
      step("Pass 2 · nama + tgl lahir + ibu", "Ketiganya sama persis → AUTO; cocok dengan lebih dari satu NIK → CONFLICT; NIK berkas milik orang lain → REVIEW.",
        [["nameCleaning", null]]),
      step("Pass 3 · blocking + skor", "Kandidat dari kueri blocking grade berkas. Skor = Σ kemiripan elemen × bobot. Tanggal lahir dinilai menurut dateMatch.",
        [["weights", null], ["dateMatch", null], ["blocking (baca)", null]]),
      step("Klasifikasi", `AUTO / REVIEW menurut ambang grade (lihat tabel di bawah); selain itu UNMATCH. Kandidat yang selisih skornya ≤ ${fmtNum(m.conflictEpsilon)} dari teratas dianggap seri → CONFLICT.`,
        [["auto*/review*", null], ["missingElements", null], ["conflictEpsilon", "#/global"]])))));
}

function gradingCard(rules) {
  const head = h("tr", {}, h("th", { text: "Grade" }), h("th", { class: "num", text: "Urutan" }), h("th", { text: "Aktif" }),
    h("th", { text: "Kolom NIK" }), CRITERIA_ELEMENTS.map((e) => h("th", { class: "num", title: ELEMENT_LABELS[e], text: SHORT[e] })),
    h("th", { class: "num", text: "NIK tepercaya" }), h("th", { class: "num", text: "Pita skor" }), h("th", { text: "Label" }),
    h("th", { title: "Boleh lanjut ke matching", text: "Lanjut" }));
  const rows = rules.grades.map((grade) => {
    const c = grade.criteria;
    const s = grade.score || {};
    const tail = [
      h("td", { class: "num", text: `${s.min}–${s.max}` }),
      h("td", { text: s.severityLabel || "—" }),
      h("td", {}, s.canProceed ? badge("good", "ya") : badge("critical", "tidak")),
    ];
    const tr = h("tr", { class: "is-link", on: { click: () => { window.location.hash = `#/grade/${grade.gradeId}`; } } },
      h("td", {}, gradeLink(grade.gradeId)));
    if (c) {
      tr.append(h("td", { class: "num", text: String(c.order) }),
        h("td", {}, c.active ? badge("good", "aktif") : badge("neutral", "nonaktif")),
        h("td", { title: NIK_COLUMN[c.nikColumn] || c.nikColumn, text: c.nikColumn }),
        ...CRITERIA_ELEMENTS.map((e) => h("td", { class: `num ${c.minCompleteness[e] == null ? "is-off" : ""}`, text: c.minCompleteness[e] == null ? "—" : `≥ ${fmtPct(c.minCompleteness[e])}` })),
        h("td", { class: `num ${c.minNikTrusted == null ? "is-off" : ""}`, text: c.minNikTrusted == null ? "—" : `≥ ${fmtPct(c.minNikTrusted)}` }));
    } else {
      const text = grade.gradeId === 5
        ? [h("div", { text: "Tidak lolos A–D, tetapi memuat semua kolom salah satu kombinasi (global):" }),
          h("div", { class: "chips", style: { marginTop: "4px" } }, rules.global.grading.gradeECombinations
            .map((set) => h("span", { class: "chip", text: set.map(elementLabel).join(" + ") })))]
        : ["Tidak satu pun kriteria di atas terpenuhi."];
      tr.append(h("td", { colspan: "10", class: "cell-span" }, ...text));
    }
    tr.append(...tail);
    return tr;
  });
  return card("Grading: kriteria & pita skor", { sub: "— = tidak diperiksa. Persentase = porsi baris yang terisi." },
    h("div", { class: "table-wrap" }, h("table", { class: "table-tight" }, h("thead", {}, head), h("tbody", {}, rows))),
    h("h3", { style: { margin: "16px 0 4px" }, text: "Pita skor mutu 0–100" }),
    bandStrip(rules.grades, null, bandProblems(rules.grades)));
}

function matchingCard(rules) {
  const rows = rules.grades.map((grade) => {
    const m = grade.matching;
    if (!m) return null;
    const warn = m.analysis?.warnings?.length || 0;
    const tr = h("tr", { class: "is-link", on: { click: () => { window.location.hash = `#/grade/${grade.gradeId}`; } } },
      h("td", {}, gradeLink(grade.gradeId)));
    if (grade.gradeId === 6) {
      tr.append(h("td", { colspan: "5", class: "cell-span", text: `Tidak dicocokkan: berkas grade F ditolak sebelum matching. Ambang tersimpan (AUTO ${autoText(m)}; REVIEW ${reviewText(m)}) tetapi tidak dipakai.` }));
      return tr;
    }
    tr.append(h("td", { text: autoText(m) }), h("td", { text: reviewText(m) }),
      h("td", { text: m.dateMatch === "exact" ? "sama persis" : "kemiripan" }),
      h("td", { text: cleaningText(m.nameCleaning) }),
      h("td", {}, warn ? badge("warning", `${warn} peringatan`) : badge("good", "tidak ada")));
    return tr;
  });
  return card("Matching: ambang klasifikasi Pass 3", { sub: "Dicek berurutan: AUTO dulu, lalu REVIEW, selain itu UNMATCH." },
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Grade" }), h("th", { text: "AUTO bila" }), h("th", { text: "REVIEW bila" }),
        h("th", { text: "Tanggal lahir" }), h("th", { text: "Pembersihan nama" }), h("th", { text: "Peringatan" }))),
      h("tbody", {}, rows))),
    h("p", { class: "card-foot", text: `Tanggal lahir: "kemiripan" = ${DATE_MATCH.similarity}; "sama persis" = ${DATE_MATCH.exact}.` }));
}

function weightsCard(rules) {
  const elements = rules.matchingElements;
  const rows = rules.grades.filter((g) => g.gradeId <= 5 && g.matching?.weights).map((grade) => {
    const m = grade.matching;
    const sum = Object.values(m.weights).reduce((a, b) => a + Number(b || 0), 0);
    const best = m.analysis?.maxScoreByMissingCount || {};
    return h("tr", { class: "is-link", on: { click: () => { window.location.hash = `#/grade/${grade.gradeId}`; } } },
      h("td", {}, gradeLink(grade.gradeId)),
      elements.map((e) => h("td", {}, weightCell(m.weights[e], (m.missingElements || []).includes(e),
        m.availableElements ? m.availableElements.weights.includes(e) || Boolean(m.weights[e]) : true))),
      h("td", { class: "num", text: `${fmtNum(sum)}%` }),
      h("td", { class: "small" }, Object.entries(best).map(([n, v]) => h("div", { class: "nowrap", text: `${n} kosong → ${fmtNum(Math.round(v * 1000) / 1000)}` }))));
  });
  return card("Matching: bobot skor & elemen kosong", {
    sub: "Skor Pass 3 = Σ kemiripan × bobot. Elemen bertanda \"dihitung kosong\" menentukan jumlah kosong untuk aturan AUTO/REVIEW.",
  }, h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", { text: "Grade" }), elements.map((e) => h("th", { text: elementLabel(e) })),
      h("th", { class: "num", text: "Jumlah" }), h("th", { text: "Skor tertinggi" }))),
    h("tbody", {}, rows))),
  h("p", { class: "card-foot", text: "Skor tertinggi = skor baris yang elemen kosongnya seperti tertulis dan selebihnya cocok sempurna. NIK tidak diberi bobot: dipakai untuk blocking grade A/B dan Pass 1." }));
}

function globalCard(rules) {
  const g = rules.global;
  const meta = g.meta || {};
  const row = (label, key, value) => h("tr", {},
    h("td", {}, h("div", { text: label }), h("code", { text: key })),
    h("td", {}, value),
    h("td", {}, sourceBadge(meta[key]?.source)),
    h("td", { class: "small muted", text: meta[key]?.updatedAt ? `${fmtDate(meta[key].updatedAt)} · ${meta[key].updatedBy || "—"}` : "—" }));
  return card("Global", { actions: [button("Ubah", { onClick: () => { window.location.hash = "#/global"; } })] },
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Pengaturan" }), h("th", { text: "Nilai" }), h("th", { text: "Sumber" }), h("th", { text: "Diubah" }))),
      h("tbody", {},
        row("Bobot skor mutu grading", "grading.scoreWeights", `kelengkapan ${fmtNum(g.grading.scoreWeights.kelengkapan)} · NIK tepercaya ${fmtNum(g.grading.scoreWeights.nik_tepercaya)}`),
        row("Kombinasi kolom grade E", "grading.gradeECombinations", h("div", {}, g.grading.gradeECombinations.map((set) => h("div", { text: set.map(elementLabel).join(" + ") })))),
        row("Selisih skor yang dianggap seri", "matching.conflictEpsilon", `${fmtNum(g.matching.conflictEpsilon)} poin`),
        row("Ambang nama ibu bertentangan (Pass 1)", "matching.contradictionJw", fmtNum(g.matching.contradictionJw))))));
}

export function renderOverview(ctx, root) {
  const rules = ctx.state.rules;
  root.append(
    pageHead("Ringkasan aturan", "Seluruh aturan grading A–F dan matching yang sedang berlaku. Klik baris grade untuk melihat detail dan mengubahnya."),
    h("div", { class: "grid-2" }, versionCard(ctx), warningsCard(rules)),
    h("div", { style: { height: "16px" } }),
    flowCard(rules),
    gradingCard(rules),
    matchingCard(rules),
    weightsCard(rules),
    globalCard(rules),
    card("Respons mentah", { sub: "GET /api/v1/config/rules — bentuk yang dibaca UI ini" },
      h("details", {}, h("summary", { text: "Tampilkan JSON" }), copyBlock(JSON.stringify(rules, null, 2)))),
  );
}
