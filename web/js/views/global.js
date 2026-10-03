import { api } from "../api.js";
import { clear, clone, fmtDate, fmtNum, h, icon, same } from "../dom.js";
import { elementLabel } from "../fields.js";
import { analyseGlobal } from "../analysis.js";
import { button, card, field, nextId, notice, numberInput, pageHead, sourceBadge } from "../ui.js";
import { createSaveFlow } from "./savebar.js";

const COMBO_ELEMENTS = ["nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu", "wilayah"];
const KEYS = {
  scoreWeights: ["grading", "grading.scoreWeights"],
  gradeECombinations: ["grading", "grading.gradeECombinations"],
  conflictEpsilon: ["matching", "matching.conflictEpsilon"],
  contradictionJw: ["matching", "matching.contradictionJw"],
};
const ENV_NAMES = {
  "matching.conflictEpsilon": "MATCHING_CONFLICT_EPSILON",
  "matching.contradictionJw": "MATCHING_KONTRA_JW",
};

function buildPatch(g, draft) {
  const patch = {};
  const put = (section, key, value) => { (patch[section] ||= {})[key] = value; };
  if (draft.reset.scoreWeights) put("grading", "scoreWeights", null);
  else {
    const changed = Object.fromEntries(Object.entries(draft.scoreWeights)
      .filter(([k, v]) => !same(v, g.grading.scoreWeights[k])));
    if (Object.keys(changed).length) put("grading", "scoreWeights", changed);
  }
  if (draft.reset.gradeECombinations) put("grading", "gradeECombinations", null);
  else if (!same(draft.gradeECombinations, g.grading.gradeECombinations)) put("grading", "gradeECombinations", draft.gradeECombinations);
  for (const key of ["conflictEpsilon", "contradictionJw"]) {
    if (draft.reset[key]) put("matching", key, null);
    else if (!same(draft[key], g.matching[key])) put("matching", key, draft[key]);
  }
  return patch;
}

function problemsOf(draft) {
  const problems = { scoreWeights: [], gradeECombinations: [], conflictEpsilon: [], contradictionJw: [] };
  if (!draft.reset.scoreWeights) {
    const values = Object.values(draft.scoreWeights);
    if (!values.every((v) => Number.isFinite(v) && v >= 0 && v <= 1)) problems.scoreWeights.push("Setiap bobot harus angka 0–1.");
    else if (Math.abs(values.reduce((a, b) => a + b, 0) - 1) > 1e-6) {
      problems.scoreWeights.push(`Jumlah bobot ${fmtNum(Math.round(values.reduce((a, b) => a + b, 0) * 1e9) / 1e9)}, harus 1.`);
    }
  }
  if (!draft.reset.gradeECombinations) {
    if (!draft.gradeECombinations.length) problems.gradeECombinations.push("Minimal satu kombinasi.");
    draft.gradeECombinations.forEach((combo, i) => {
      if (!combo.length) problems.gradeECombinations.push(`Kombinasi ${i + 1} kosong.`);
    });
  }
  if (!draft.reset.conflictEpsilon && !(Number.isFinite(draft.conflictEpsilon) && draft.conflictEpsilon >= 0 && draft.conflictEpsilon <= 100)) {
    problems.conflictEpsilon.push("Harus angka 0–100 (poin skor).");
  }
  if (!draft.reset.contradictionJw && !(Number.isFinite(draft.contradictionJw) && draft.contradictionJw >= 0 && draft.contradictionJw <= 1)) {
    problems.contradictionJw.push("Harus angka 0–1.");
  }
  return problems;
}

export async function renderGlobal(ctx, root) {
  const g = ctx.state.rules.global;
  const meta = g.meta || {};
  const draft = {
    scoreWeights: { ...g.grading.scoreWeights },
    gradeECombinations: clone(g.grading.gradeECombinations),
    conflictEpsilon: g.matching.conflictEpsilon,
    contradictionJw: g.matching.contradictionJw,
    reset: { scoreWeights: false, gradeECombinations: false, conflictEpsilon: false, contradictionJw: false },
  };
  const rows = {};
  const slots = {};
  const flow = createSaveFlow({
    ctx,
    path: "/api/v1/config/global",
    resultKey: "global",
    savedText: "Nilai global tersimpan",
    send: (body) => api.patchGlobal(body),
    onUpdate: () => update(),
  });

  function resetControl(key) {
    const path = KEYS[key][1];
    const source = meta[path]?.source;
    if (draft.reset[key]) {
      return h("div", { class: "stack" }, notice("info", `Akan dikembalikan ke ${ENV_NAMES[path] ? "env/bawaan" : "bawaan"} (${path}: null).`),
        button("Urungkan", { iconName: "undo", small: true, onClick: () => { draft.reset[key] = false; renderAll(); update(); } }));
    }
    if (source !== "config") return document.createDocumentFragment();
    return h("div", {}, button("Hapus setelan (kembali ke env/bawaan)", { iconName: "undo", small: true,
      onClick: () => { draft.reset[key] = true; renderAll(); update(); } }));
  }

  function sourceLine(key) {
    const path = KEYS[key][1];
    const m = meta[path] || {};
    return h("div", { class: "chips" }, sourceBadge(m.source),
      m.updatedAt ? h("span", { class: "muted small", text: `${fmtDate(m.updatedAt)} · ${m.updatedBy || "—"}` }) : null,
      m.source === "env" && ENV_NAMES[path] ? h("span", { class: "muted small", text: `env ${ENV_NAMES[path]} di service` }) : null);
  }

  function renderScoreWeights() {
    const slot = clear(slots.scoreWeights);
    slot.append(sourceLine("scoreWeights"));
    if (!draft.reset.scoreWeights) {
      const bar = h("div", { class: "wbar" });
      const fill = h("span");
      bar.append(fill);
      const paint = () => { fill.style.width = `${Math.max(0, Math.min(1, draft.scoreWeights.kelengkapan || 0)) * 100}%`; };
      for (const [key, label] of [["kelengkapan", "Kelengkapan rata-rata"], ["nik_tepercaya", "Porsi NIK tepercaya"]]) {
        const id = nextId(key);
        slot.append(h("div", { class: "pair" }, h("label", { for: id, text: label }),
          numberInput({ value: draft.scoreWeights[key], id, min: 0, max: 1, step: 0.05,
            onChange: (v) => { draft.scoreWeights[key] = v == null ? Number.NaN : v; paint(); update(); } })));
      }
      paint();
      slot.append(h("div", { class: "small muted" }, "Porsi kelengkapan dalam mutu:"), bar);
    }
    slot.append(resetControl("scoreWeights"));
  }

  function renderCombos() {
    const slot = clear(slots.gradeECombinations);
    slot.append(sourceLine("gradeECombinations"));
    if (!draft.reset.gradeECombinations) {
      const list = draft.gradeECombinations;
      list.forEach((combo, index) => {
        const boxes = COMBO_ELEMENTS.map((e) => {
          const box = h("input", { type: "checkbox", checked: combo.includes(e) });
          box.addEventListener("change", () => {
            if (box.checked) combo.push(e);
            else combo.splice(combo.indexOf(e), 1);
            combo.sort((a, b) => COMBO_ELEMENTS.indexOf(a) - COMBO_ELEMENTS.indexOf(b));
            update();
          });
          return h("label", { class: "check" }, box, elementLabel(e));
        });
        slot.append(h("div", { class: "combo" }, h("span", { class: "combo-index", text: `${index + 1}.` }), boxes,
          button(null, { iconName: "trash", title: `Hapus kombinasi ${index + 1}`, small: true,
            onClick: () => { list.splice(index, 1); renderCombos(); update(); } })));
      });
      slot.append(h("div", { class: "add-row" }, button("Tambah kombinasi", { iconName: "plus", small: true,
        onClick: () => { list.push(["nama", "tanggal_lahir"]); renderCombos(); update(); } })));
    }
    slot.append(resetControl("gradeECombinations"));
  }

  function renderNumber(key, opts) {
    const slot = clear(slots[key]);
    slot.append(sourceLine(key));
    if (!draft.reset[key]) {
      const id = nextId(key);
      slot.append(h("div", { class: "input-group" }, numberInput({ value: draft[key], id, ...opts, label: opts.label,
        onChange: (v) => { draft[key] = v == null ? Number.NaN : v; update(); } }), opts.suffix ? h("span", { class: "suffix", text: opts.suffix }) : null));
    }
    slot.append(resetControl(key));
  }

  function renderAll() {
    renderScoreWeights();
    renderCombos();
    renderNumber("conflictEpsilon", { min: 0, max: 100, step: 0.1, suffix: "poin skor", label: "Selisih skor yang dianggap seri" });
    renderNumber("contradictionJw", { min: 0, max: 1, step: 0.01, label: "Ambang nama ibu bertentangan" });
  }

  for (const key of Object.keys(KEYS)) slots[key] = h("div", { class: "stack" });
  rows.scoreWeights = field({ label: "Bobot skor mutu grading", path: "global.grading.scoreWeights", control: slots.scoreWeights,
    hint: "Mutu = kelengkapan × bobot + NIK tepercaya × bobot (hanya bila berkas punya kolom NIK). Jumlah harus 1. Digabung per kunci." });
  rows.gradeECombinations = field({ label: "Kombinasi kolom grade E", path: "global.grading.gradeECombinations", control: slots.gradeECombinations,
    hint: "Berkas yang tidak lolos A–D tetapi memuat SEMUA kolom salah satu kombinasi menjadi grade E. Diganti utuh. Blocking grade E butuh nama dan tanggal lahir." });
  rows.conflictEpsilon = field({ label: "Selisih skor yang dianggap seri", path: "global.matching.conflictEpsilon", control: slots.conflictEpsilon,
    hint: "Kandidat ber-NIK berbeda yang skornya berselisih ≤ nilai ini dari kandidat teratas dianggap seri → CONFLICT. 0 = hanya seri persis." });
  rows.contradictionJw = field({ label: "Ambang nama ibu bertentangan", path: "global.matching.contradictionJw", control: slots.contradictionJw,
    hint: "Pass 1: bila kemiripan nama ibu berkas vs master di bawah nilai ini, kecocokan NIK dibatalkan dan baris turun ke pass berikutnya." });

  const warningsSlot = h("div");

  function update() {
    const patch = buildPatch(g, draft);
    const count = Object.values(patch).reduce((n, part) => n + Object.keys(part).length, 0);
    ctx.setDirty(count > 0);
    const problems = problemsOf(draft);
    for (const key of Object.keys(KEYS)) {
      const original = key === "scoreWeights" || key === "gradeECombinations" ? g.grading[key] : g.matching[key];
      const current = draft.reset[key] ? null : draft[key];
      const fmt = key === "gradeECombinations" ? original.map((c) => c.join(" + ")).join(" · ") : JSON.stringify(original);
      rows[key].setChanged(draft.reset[key] || !same(current, original), fmt);
      rows[key].setProblems(problems[key]);
    }
    clear(warningsSlot);
    const values = {
      gradeECombinations: draft.reset.gradeECombinations ? g.grading.gradeECombinations : draft.gradeECombinations,
      conflictEpsilon: draft.reset.conflictEpsilon ? g.matching.conflictEpsilon : draft.conflictEpsilon,
    };
    const warnings = analyseGlobal(values);
    warningsSlot.append(warnings.length ? notice("warning", "Peringatan (sama dengan analisis service)", warnings)
      : notice("good", null, "Tidak ada peringatan."));
    const invalid = Object.values(problems).some((list) => list.length);
    flow.update(patch, count, invalid);
  }

  renderAll();
  root.append(
    h("a", { class: "back", href: "#/ringkasan" }, icon("back"), "Ringkasan"),
    pageHead("Nilai global", "Berlaku untuk semua grade. Nilai yang belum pernah disimpan lewat API memakai env service atau nilai bawaan."),
    flow.resultSlot,
    card("Grading", {}, h("div", { class: "fields" }, rows.scoreWeights.el, rows.gradeECombinations.el)),
    card("Matching", {}, h("div", { class: "fields" }, rows.conflictEpsilon.el, rows.contradictionJw.el)),
    card("Analisis", {}, warningsSlot),
    flow.barSlot);
  update();
  return null;
}
