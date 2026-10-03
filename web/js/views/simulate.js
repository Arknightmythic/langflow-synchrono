import { append, badge, clear, fmtNum, fmtPct, h } from "../dom.js";
import { DATE_MATCH, ELEMENT_LABELS, GRADE_LETTERS, elementLabel } from "../fields.js";
import { classify, jaroWinkler, score, simulateGrading } from "../analysis.js";
import { button, card, gradeLink, notice, pageHead, selectInput } from "../ui.js";

const GRADING_ELEMENTS = ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"];

function outcomeBadge(value) {
  if (value === "AUTO") return badge("good", "AUTO", "check");
  if (value === "REVIEW") return badge("warning", "REVIEW", "half");
  return badge("neutral", "UNMATCH", "minus");
}

function explain(m, missing, value) {
  const result = classify(m, missing, value);
  if (result === "AUTO") {
    return `AUTO: kosong ${missing}${m.autoMissingMax == null ? "" : ` ≤ ${m.autoMissingMax}`} dan skor ≥ ${fmtNum(m.autoScoreMin)}.`;
  }
  if (result === "REVIEW") {
    return `REVIEW: bukan AUTO, kosong ${missing}${m.reviewMissingCount == null ? "" : ` = ${m.reviewMissingCount}`} dan ${fmtNum(m.reviewScoreMin)} ≤ skor < ${fmtNum(m.reviewScoreMax)}.`;
  }
  let notAuto;
  if (m.autoScoreMin == null) notAuto = "autoScoreMin kosong";
  else if (m.autoMissingMax != null && missing > m.autoMissingMax) notAuto = `kosong ${missing} > autoMissingMax ${m.autoMissingMax}`;
  else notAuto = `skor < autoScoreMin ${fmtNum(m.autoScoreMin)}`;
  let notReview;
  if (m.reviewMissingCount != null && missing !== m.reviewMissingCount) notReview = `REVIEW butuh tepat ${m.reviewMissingCount} elemen kosong`;
  else if (value < m.reviewScoreMin) notReview = `skor < reviewScoreMin ${fmtNum(m.reviewScoreMin)}`;
  else notReview = `skor ≥ reviewScoreMax ${fmtNum(m.reviewScoreMax)}`;
  return `UNMATCH: tidak AUTO (${notAuto}) dan tidak REVIEW (${notReview}).`;
}

// One incoming row against its best candidate, scored with matching section `m`.
export function scoreCalculator(m, label, memory = { sim: {}, empty: {} }) {
  const weights = m.weights || {};
  const elements = [...Object.keys(weights), ...(m.missingElements || []).filter((e) => !(e in weights))];
  const sim = memory.sim;
  const empty = memory.empty;
  for (const e of elements) {
    if (!(e in sim)) sim[e] = 1;
    if (!(e in empty)) empty[e] = false;
  }
  const result = h("div", { class: "calc-result", "aria-live": "polite" });
  const contrib = {};
  const sliders = {};

  function recompute() {
    const effective = Object.fromEntries(elements.map((e) => [e, empty[e] ? 0 : sim[e]]));
    const value = score(weights, effective);
    const missing = (m.missingElements || []).filter((e) => empty[e]).length;
    for (const e of elements) {
      contrib[e].textContent = weights[e] ? fmtNum(Math.round(effective[e] * weights[e] * 1e4) / 1e4) : "—";
    }
    clear(result);
    result.append(
      h("div", { class: "stat" }, h("span", { class: "label", text: "Skor" }), h("span", { class: "big", text: fmtNum(value) })),
      h("div", { class: "stat" }, h("span", { class: "label", text: "Elemen kosong" }), h("span", { class: "big", text: String(missing) })),
      h("div", { class: "stat" }, h("span", { class: "label", text: "Hasil" }), outcomeBadge(classify(m, missing, value))),
      h("div", { class: "small", text: explain(m, missing, value) }));
  }

  const rows = h("div", {},
    h("div", { class: "calc-row calc-head" }, h("span", { text: "Elemen" }), h("span", { class: "num", text: "Bobot" }),
      h("span", { text: "Kosong di berkas" }), h("span", { class: "calc-sim", text: "Kemiripan dengan master (0–1)" }),
      h("span", { class: "num calc-contrib", text: "Sumbangan" })));
  for (const e of elements) {
    const counted = (m.missingElements || []).includes(e);
    const box = h("input", { type: "checkbox", checked: empty[e], "aria-label": `${elementLabel(e)} kosong` });
    let control;
    if (e === "tanggal_lahir" && m.dateMatch === "exact") {
      if (sim[e] !== 0 && sim[e] !== 1) sim[e] = sim[e] >= 1 ? 1 : 0;
      control = selectInput({ options: [["1", "sama persis (1)"], ["0", "beda (0)"]], value: String(sim[e]), label: "Tanggal lahir",
        onChange: (v) => { sim[e] = Number(v); recompute(); } });
      control.disabled = empty[e];
      sliders[e] = { set: (v) => { control.value = v >= 1 ? "1" : "0"; sim[e] = v >= 1 ? 1 : 0; } };
    } else {
      const range = h("input", { type: "range", min: "0", max: "1", step: "0.001", value: String(sim[e]), disabled: empty[e], "aria-label": `Kemiripan ${elementLabel(e)}` });
      const num = h("input", { type: "number", min: "0", max: "1", step: "0.001", value: String(sim[e]), disabled: empty[e], "aria-label": `Kemiripan ${elementLabel(e)} (angka)` });
      num.style.width = "90px";
      range.addEventListener("input", () => { sim[e] = Number(range.value); num.value = range.value; recompute(); });
      num.addEventListener("input", () => {
        const v = Number(num.value);
        if (Number.isFinite(v) && v >= 0 && v <= 1) { sim[e] = v; range.value = String(v); recompute(); }
      });
      control = h("span", { class: "input-group" }, range, num);
      sliders[e] = { set: (v) => { sim[e] = v; range.value = String(v); num.value = String(v); } };
    }
    box.addEventListener("change", () => {
      empty[e] = box.checked;
      control.querySelectorAll?.("input, select").forEach((el) => { el.disabled = box.checked; });
      if (control.tagName === "SELECT") control.disabled = box.checked;
      recompute();
    });
    contrib[e] = h("span", { class: "num calc-contrib" });
    rows.append(h("div", { class: "calc-row" },
      h("span", {}, elementLabel(e), counted ? h("div", { class: "muted small", text: "dihitung kosong" }) : null),
      h("span", { class: "num", text: weights[e] ? `${fmtNum(weights[e])}%` : "—" }),
      h("label", { class: "check" }, box, "kosong"),
      h("span", { class: "calc-sim" }, control), contrib[e]));
  }

  // Jaro-Winkler on texts that are already normalised (lower case, trimmed).
  const left = h("input", { type: "text", placeholder: "teks berkas", "aria-label": "Teks berkas" });
  const right = h("input", { type: "text", placeholder: "teks master", "aria-label": "Teks master" });
  const out = h("strong", { text: "—" });
  const target = selectInput({ options: elements.filter((e) => !(e === "tanggal_lahir" && m.dateMatch === "exact")).map((e) => [e, elementLabel(e)]),
    value: elements[0], label: "Pakai untuk elemen", onChange: () => {} });
  let last = null;
  const run = () => {
    last = left.value || right.value ? jaroWinkler(left.value, right.value) : null;
    out.textContent = last == null ? "—" : fmtNum(Math.round(last * 1e6) / 1e6);
  };
  left.addEventListener("input", run);
  right.addEventListener("input", run);
  const use = button("Pakai", { small: true, onClick: () => {
    if (last == null || !target.value) return;
    sliders[target.value].set(Math.round(last * 1000) / 1000);
    recompute();
  } });

  recompute();
  return card(`Coba satu baris${label ? ` · ${label}` : ""}`, {
    sub: "Skor kandidat terbaik dari kemiripan per elemen. CONFLICT (kandidat seri) tidak bisa disimulasikan di sini.",
  }, rows, result,
  h("div", { class: "stack", style: { marginTop: "14px" } },
    h("h3", { text: "Hitung kemiripan Jaro-Winkler" }),
    h("p", { class: "muted small", text: "Sama dengan jaro_winkler_similarity DuckDB. Masukkan teks yang sudah dinormalisasi (huruf kecil, tanpa spasi ganda; tanggal sebagai yyyy-mm-dd). Wilayah = rata-rata kemiripan provinsi/kabupaten/kecamatan/kelurahan yang terisi." }),
    h("div", { class: "jw" }, left, right, h("span", {}, "→ ", out), target, use)));
}

function gradingSimulator(rules) {
  const present = Object.fromEntries([...GRADING_ELEMENTS, "wilayah"].map((e) => [e, true]));
  const rate = Object.fromEntries(GRADING_ELEMENTS.map((e) => [e, 1]));
  const input = { present, rate, trusted: 1 };
  const output = h("div", { "aria-live": "polite" });
  const controls = {};

  const PRESETS = [
    ["Lengkap ber-NIK", { present: { nik: 1, nama: 1, tempat_lahir: 1, tanggal_lahir: 1, jenis_kelamin: 1, nama_ibu: 1, wilayah: 1 }, rate: 1, trusted: 1 }],
    ["Ber-NIK, sebagian kosong", { present: { nik: 1, nama: 1, tempat_lahir: 1, tanggal_lahir: 1, jenis_kelamin: 1, nama_ibu: 1, wilayah: 0 }, rate: 0.8, trusted: 0.75 }],
    ["Tanpa NIK, lengkap", { present: { nik: 0, nama: 1, tempat_lahir: 1, tanggal_lahir: 1, jenis_kelamin: 1, nama_ibu: 1, wilayah: 1 }, rate: 1, trusted: 0 }],
    ["Nama + tgl lahir + jenis kelamin", { present: { nik: 0, nama: 1, tempat_lahir: 0, tanggal_lahir: 1, jenis_kelamin: 1, nama_ibu: 0, wilayah: 0 }, rate: 1, trusted: 0 }],
    ["Kolom tak dikenal", { present: { nik: 0, nama: 0, tempat_lahir: 0, tanggal_lahir: 0, jenis_kelamin: 0, nama_ibu: 0, wilayah: 0 }, rate: 0, trusted: 0 }],
  ];

  function recompute() {
    const r = simulateGrading(rules, input);
    clear(output);
    const steps = r.steps.map((s) => {
      const letter = GRADE_LETTERS[s.gradeId];
      if (!s.failures.length) return h("li", {}, h("strong", { text: `${letter} (urutan ${s.order}): terpenuhi` }));
      return h("li", {}, `${letter} (urutan ${s.order}): tidak — `, s.failures.map((f) => (typeof f === "string" ? f
        : `${f.element === "nik_tepercaya" ? "NIK tepercaya" : ELEMENT_LABELS[f.element]} ${fmtPct(f.have)} < ${fmtPct(f.need)}`)).join("; "));
    });
    if (r.gradeId === 5) steps.push(h("li", {}, h("strong", { text: `E: kombinasi ${r.combo.map(elementLabel).join(" + ")} ada` })));
    if (r.gradeId === 6) steps.push(h("li", {}, h("strong", { text: "F: tidak ada kriteria maupun kombinasi E yang terpenuhi" })));
    const band = r.band;
    append(output, [
      h("div", { class: "calc-result" },
        h("div", { class: "stat" }, h("span", { class: "label", text: "Grade" }), gradeLink(r.gradeId, band?.severityLabel)),
        h("div", { class: "stat" }, h("span", { class: "label", text: "Skor mutu" }), h("span", { class: "big", text: r.score == null ? "—" : String(r.score) })),
        h("div", { class: "stat" }, h("span", { class: "label", text: "Lanjut matching" }),
          band?.canProceed ? badge("good", "ya") : badge("critical", "tidak"))),
      h("h3", { style: { margin: "12px 0 6px" }, text: "Jejak evaluasi" }),
      h("ol", {}, steps),
      band ? h("p", { class: "small muted", style: { marginTop: "8px" },
        text: `Mutu = ${input.present.nik ? `${fmtNum(r.weights.kelengkapan)} × kelengkapan ${fmtPct(r.completeness)} + ${fmtNum(r.weights.nik_tepercaya)} × NIK tepercaya ${fmtPct(r.trusted)}` : `kelengkapan ${fmtPct(r.completeness)} (tanpa kolom NIK)`} = ${fmtNum(Math.round(r.quality * 1e6) / 1e6)}. Skor = ${band.min} + (${band.max} − ${band.min}) × mutu = ${fmtNum(Math.round(r.rawScore * 1e6) / 1e6)} → dibulatkan ${r.score}.` }) : null]);
  }

  const table = h("table", { class: "table-tight" },
    h("thead", {}, h("tr", {}, h("th", { text: "Elemen" }), h("th", { text: "Kolom ada" }), h("th", { text: "Baris terisi" }))));
  const tbody = h("tbody");
  for (const e of [...GRADING_ELEMENTS, "wilayah"]) {
    const box = h("input", { type: "checkbox", checked: present[e], "aria-label": `Kolom ${elementLabel(e)} ada` });
    let pct = null;
    if (e !== "wilayah") {
      pct = h("input", { type: "number", min: "0", max: "100", step: "any", value: "100", "aria-label": `Persen ${elementLabel(e)} terisi` });
      pct.addEventListener("input", () => { const v = Number(pct.value); if (Number.isFinite(v)) { rate[e] = Math.max(0, Math.min(100, v)) / 100; recompute(); } });
    }
    box.addEventListener("change", () => { present[e] = box.checked; if (pct) pct.disabled = !box.checked; recompute(); });
    controls[e] = { box, pct };
    tbody.append(h("tr", {}, h("td", { text: e === "wilayah" ? "Wilayah (salah satu kolom provinsi/kab/kec/kel)" : elementLabel(e) }),
      h("td", {}, box), h("td", {}, pct ? h("span", { class: "input-group" }, pct, h("span", { class: "suffix", text: "%" })) : h("span", { class: "muted", text: "—" }))));
  }
  const trusted = h("input", { type: "number", min: "0", max: "100", step: "any", value: "100", "aria-label": "Persen NIK tepercaya" });
  trusted.addEventListener("input", () => { const v = Number(trusted.value); if (Number.isFinite(v)) { input.trusted = Math.max(0, Math.min(100, v)) / 100; recompute(); } });
  tbody.append(h("tr", {}, h("td", { text: "NIK tepercaya (dari semua baris)" }), h("td", {}),
    h("td", {}, h("span", { class: "input-group" }, trusted, h("span", { class: "suffix", text: "%" })))));
  table.append(tbody);

  const presets = h("div", { class: "chips" }, PRESETS.map(([name, p]) => button(name, { small: true, onClick: () => {
    for (const e of [...GRADING_ELEMENTS, "wilayah"]) {
      present[e] = Boolean(p.present[e]);
      controls[e].box.checked = present[e];
      if (controls[e].pct) {
        rate[e] = p.rate;
        controls[e].pct.value = String(p.rate * 100);
        controls[e].pct.disabled = !present[e];
      }
    }
    input.trusted = p.trusted;
    trusted.value = String(p.trusted * 100);
    recompute();
  } })));

  recompute();
  return card("Simulasi grading", { sub: "Grade dan skor mutu sebuah berkas menurut kriteria yang berlaku." },
    h("p", { class: "muted small", text: "Contoh cepat:" }), presets,
    h("div", { class: "grid-2", style: { marginTop: "12px" } },
      h("div", { class: "table-wrap" }, table), output));
}

export function renderSimulate(ctx, root) {
  const rules = ctx.state.rules;
  const matchable = rules.grades.filter((g) => g.gradeId <= 5 && g.matching?.weights);
  const calcSlot = h("div");
  const choose = selectInput({ options: matchable.map((g) => [String(g.gradeId), `Grade ${g.gradeLetter} · ${g.score?.severityLabel || ""}`]),
    value: String(matchable[0]?.gradeId || 1), label: "Grade", onChange: (v) => show(Number(v)) });
  function show(id) {
    clear(calcSlot);
    const grade = rules.grades.find((g) => g.gradeId === id);
    calcSlot.append(scoreCalculator(grade.matching, `Grade ${grade.gradeLetter}`));
  }
  root.append(
    pageHead("Simulasi", "Coba akibat aturan yang berlaku tanpa mengubah apa pun. Untuk mencoba draf yang belum disimpan, pakai bagian \"Coba satu baris\" di halaman grade."),
    gradingSimulator(rules),
    card("Simulasi matching", { sub: `Tanggal lahir dinilai menurut dateMatch grade: ${Object.keys(DATE_MATCH).join(" / ")}.` },
      h("label", { class: "check" }, "Grade berkas ", choose)),
    calcSlot);
  if (!matchable.length) root.append(notice("info", null, "Tidak ada grade yang dicocokkan."));
  else show(matchable[0].gradeId);
  return null;
}
