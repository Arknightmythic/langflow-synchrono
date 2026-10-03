import { api } from "../api.js";
import { append, badge, clear, fmtDate, fmtNum, fmtPct, h, icon, same } from "../dom.js";
import {
  DATE_MATCH, ELEMENT_LABELS, GRADE_LETTERS, NAME_CLEANING, NIK_COLUMN, elementLabel,
} from "../fields.js";
import { analyseMatching, bandProblems, criteriaProblems, matchingProblems } from "../analysis.js";
import {
  bandStrip, button, card, field, meter, meterAxis, meterLegend, nextId, notice, nullableNumber, numberInput,
  pageHead, percentInput, selectInput, switchInput, textArea, textInput, valueText,
} from "../ui.js";
import { createSaveFlow } from "./savebar.js";
import { autoText, reviewText } from "./overview.js";
import { scoreCalculator } from "./simulate.js";

const CRITERIA_ELEMENTS = ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"];
const THRESHOLD_KEYS = ["autoMissingMax", "autoScoreMin", "reviewMissingCount", "reviewScoreMin", "reviewScoreMax"];
const SCORE_KEYS = ["min", "max", "severityLabel", "canProceed", "criteriaDescription"];

function draftFrom(grade) {
  const m = grade.matching || {};
  return {
    criteria: grade.criteria ? {
      order: grade.criteria.order,
      active: grade.criteria.active,
      nikColumn: grade.criteria.nikColumn,
      minCompleteness: { ...grade.criteria.minCompleteness },
      minNikTrusted: grade.criteria.minNikTrusted,
    } : null,
    score: grade.score ? Object.fromEntries(SCORE_KEYS.map((k) => [k, grade.score[k]])) : null,
    matching: grade.matching ? {
      ...Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, m[k]])),
      weights: m.weights ? Object.entries(m.weights).map(([e, p]) => [e, p]) : null,
      missingElements: m.missingElements ? [...m.missingElements] : null,
      nameCleaning: m.nameCleaning ? { ...m.nameCleaning } : null,
      dateMatch: m.dateMatch,
    } : null,
    reset: { weights: false, missingElements: false, nameCleaning: false, dateMatch: false },
  };
}

function buildPatch(grade, draft) {
  const patch = {};
  if (draft.criteria) {
    const c = {};
    for (const key of ["order", "active", "nikColumn", "minNikTrusted"]) {
      if (!same(draft.criteria[key], grade.criteria[key])) c[key] = draft.criteria[key];
    }
    const mc = {};
    for (const e of CRITERIA_ELEMENTS) {
      if (!same(draft.criteria.minCompleteness[e], grade.criteria.minCompleteness[e])) mc[e] = draft.criteria.minCompleteness[e];
    }
    if (Object.keys(mc).length) c.minCompleteness = mc;
    if (Object.keys(c).length) patch.criteria = c;
  }
  if (draft.score) {
    const s = {};
    for (const key of SCORE_KEYS) if (!same(draft.score[key], grade.score[key])) s[key] = draft.score[key];
    if (Object.keys(s).length) patch.score = s;
  }
  if (draft.matching) {
    const m = {};
    const orig = grade.matching;
    for (const key of THRESHOLD_KEYS) if (!same(draft.matching[key], orig[key])) m[key] = draft.matching[key];
    if (draft.reset.weights) m.weights = null;
    else if (draft.matching.weights && !same(draft.matching.weights, Object.entries(orig.weights || {}))) {
      m.weights = Object.fromEntries(draft.matching.weights);
    }
    if (draft.reset.missingElements) m.missingElements = null;
    else if (draft.matching.missingElements && !same(draft.matching.missingElements, orig.missingElements)) {
      m.missingElements = draft.matching.missingElements;
    }
    if (draft.reset.nameCleaning) m.nameCleaning = null;
    else if (draft.matching.nameCleaning) {
      const nc = {};
      for (const key of Object.keys(NAME_CLEANING)) {
        if (draft.matching.nameCleaning[key] !== orig.nameCleaning?.[key]) nc[key] = draft.matching.nameCleaning[key];
      }
      if (Object.keys(nc).length) m.nameCleaning = nc;
    }
    if (draft.reset.dateMatch) m.dateMatch = null;
    else if (draft.matching.dateMatch && draft.matching.dateMatch !== orig.dateMatch) m.dateMatch = draft.matching.dateMatch;
    if (Object.keys(m).length) patch.matching = m;
  }
  return patch;
}

function countFields(patch) {
  let n = 0;
  for (const part of Object.values(patch)) {
    for (const [key, value] of Object.entries(part)) {
      n += key === "minCompleteness" || (key === "nameCleaning" && value) ? Object.keys(value).length : 1;
    }
  }
  return n;
}

function hasInvalidNumber(value) {
  if (typeof value === "number") return !Number.isFinite(value);
  if (Array.isArray(value)) return value.some(hasInvalidNumber);
  if (value && typeof value === "object") return Object.values(value).some(hasInvalidNumber);
  return false;
}

// Matching section as the service will see it after the patch (null = default,
// known only once a dry run returned `after`).
function effectiveMatching(grade, draft, dryAfter) {
  const m = { ...grade.matching, ...Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, draft.matching[k]])) };
  const fromDry = dryAfter?.matching;
  m.weights = draft.reset.weights ? fromDry?.weights ?? null : Object.fromEntries(draft.matching.weights || []);
  m.missingElements = draft.reset.missingElements ? fromDry?.missingElements ?? null : draft.matching.missingElements;
  m.nameCleaning = draft.reset.nameCleaning ? fromDry?.nameCleaning ?? null : draft.matching.nameCleaning;
  m.dateMatch = draft.reset.dateMatch ? "similarity" : draft.matching.dateMatch;
  return m;
}

export async function renderGrade(ctx, root, param) {
  const gradeId = Number(param) || 1;
  const rules = ctx.state.rules;
  const grade = rules.grades.find((g) => g.gradeId === gradeId);
  if (!grade) {
    root.append(notice("critical", `Grade ${param} tidak ada di konfigurasi.`));
    return null;
  }
  const letter = GRADE_LETTERS[gradeId];
  const matchable = gradeId <= 5 && Boolean(grade.matching?.weights);
  const draft = draftFrom(grade);
  const markers = [];
  const flow = createSaveFlow({
    ctx,
    path: `/api/v1/config/rules/${gradeId}`,
    resultKey: `grade-${gradeId}`,
    savedText: `Grade ${letter} tersimpan`,
    send: (body) => api.patchGrade(gradeId, body),
    onUpdate: () => {
      // A dry run reveals the grade defaults behind a pending reset.
      if (matchable && draft.reset.weights) renderWeights();
      if (matchable && draft.reset.missingElements) renderMissing();
      update();
    },
  });
  const resultSlot = flow.resultSlot;
  const currentDry = () => flow.dryFor(buildPatch(grade, draft));

  function track(row, read, original, format = valueText) {
    markers.push(() => {
      const value = read();
      row.setChanged(!same(value, original), format(original));
    });
    return row.el;
  }

  // ── header ────────────────────────────────────────────────────────────
  const updated = [grade.criteria?.updatedAt && `kriteria ${fmtDate(grade.criteria.updatedAt)} (${grade.criteria.updatedBy || "—"})`,
    grade.matching?.updatedAt && `matching ${fmtDate(grade.matching.updatedAt)} (${grade.matching.updatedBy || "—"})`]
    .filter(Boolean).join(" · ");
  append(root, [
    h("a", { class: "back", href: "#/ringkasan" }, icon("back"), "Ringkasan"),
    pageHead(`Grade ${letter} · ${grade.score?.severityLabel || ""}`,
      grade.score?.criteriaDescription || "",
      grade.score?.canProceed ? badge("good", "boleh lanjut ke matching") : badge("critical", "tidak dicocokkan")),
    updated ? h("p", { class: "muted small", text: `Terakhir diubah: ${updated}` }) : null,
    resultSlot,
  ]);

  // ── criteria ──────────────────────────────────────────────────────────
  const criteriaProblemsSlot = h("div");
  if (draft.criteria) {
    const c = draft.criteria;
    const o = grade.criteria;
    const fields = h("div", { class: "fields" });
    const orderId = nextId("order");
    fields.append(track(field({ label: "Urutan evaluasi", path: "criteria.order", forId: orderId,
      control: numberInput({ value: c.order, id: orderId, integer: true, min: 1, onChange: (v) => { c.order = v == null ? Number.NaN : v; update(); } }),
      hint: "Berkas masuk grade pertama (urutan terkecil) yang semua syaratnya terpenuhi." }), () => c.order, o.order));
    fields.append(track(field({ label: "Aktif", path: "criteria.active",
      control: switchInput({ checked: c.active, label: "Grade ini dievaluasi", onChange: (v) => { c.active = v; update(); } }),
      hint: "Nonaktif = dilewati tanpa menghapus angkanya." }), () => c.active, o.active));
    const nikId = nextId("nik");
    let trustedRow;
    fields.append(track(field({ label: "Kolom NIK", path: "criteria.nikColumn", forId: nikId,
      control: selectInput({ id: nikId, value: c.nikColumn, options: Object.entries(NIK_COLUMN),
        onChange: (v) => { c.nikColumn = v; update(); } }),
      hint: "wajib = berkas harus punya kolom NIK (A/B) · terlarang = tidak boleh punya (C/D) · abaikan = tidak diperiksa." }),
    () => c.nikColumn, o.nikColumn, (v) => NIK_COLUMN[v] || v));

    for (const e of CRITERIA_ELEMENTS) {
      const id = nextId(`min-${e}`);
      fields.append(track(field({ label: `Kelengkapan ${ELEMENT_LABELS[e]} minimal`, path: `criteria.minCompleteness.${e}`, forId: id,
        control: percentInput({ fraction: c.minCompleteness[e], nullable: true, nullLabel: "tidak diperiksa", id,
          onChange: (v) => { c.minCompleteness[e] = v; update(); } }) }),
      () => c.minCompleteness[e], o.minCompleteness[e], fmtPctOrOff));
    }
    const trustedId = nextId("trusted");
    trustedRow = field({ label: "NIK tepercaya minimal", path: "criteria.minNikTrusted", forId: trustedId,
      control: percentInput({ fraction: c.minNikTrusted, nullable: true, nullLabel: "tidak diperiksa", id: trustedId,
        onChange: (v) => { c.minNikTrusted = v; update(); } }),
      hint: "Porsi baris dengan NIK tepercaya (16 digit sah, bukan notasi ilmiah Excel, tidak ganda). Harus \"tidak diperiksa\" bila kolom NIK terlarang." });
    markers.push(() => trustedRow.setChanged(!same(c.minNikTrusted, o.minNikTrusted), fmtPctOrOff(o.minNikTrusted)));
    fields.append(trustedRow.el);
    root.append(card("Kriteria grading", { sub: "Ambang kelengkapan dihitung dari porsi baris yang terisi. Nilai di atas 0% sekaligus mensyaratkan kolomnya ada." },
      fields, criteriaProblemsSlot));
  } else {
    root.append(card("Kriteria grading", {}, notice("info", null, h("div", {}, h("div", { text: grade.note || "Grade ini tidak punya kriteria yang bisa disetel." }),
      gradeId === 5 ? h("a", { href: "#/global", text: "Atur kombinasi grade E di halaman Global" }) : null))));
  }

  // ── score band ────────────────────────────────────────────────────────
  const bandSlot = h("div");
  if (draft.score) {
    const s = draft.score;
    const o = grade.score;
    const fields = h("div", { class: "fields" });
    const minId = nextId("smin");
    const maxId = nextId("smax");
    fields.append(track(field({ label: "Skor mutu minimal", path: "score.min", forId: minId,
      control: numberInput({ value: s.min, id: minId, integer: true, min: 0, max: 100, onChange: (v) => { s.min = v == null ? Number.NaN : v; update(); } }) }),
    () => s.min, o.min));
    fields.append(track(field({ label: "Skor mutu maksimal", path: "score.max", forId: maxId,
      control: numberInput({ value: s.max, id: maxId, integer: true, min: 0, max: 100, onChange: (v) => { s.max = v == null ? Number.NaN : v; update(); } }),
      hint: "Pita antar-grade tidak boleh tumpang tindih." }), () => s.max, o.max));
    const labelId = nextId("label");
    fields.append(track(field({ label: "Label", path: "score.severityLabel", forId: labelId,
      control: textInput({ value: s.severityLabel, id: labelId, onChange: (v) => { s.severityLabel = v; update(); } }) }),
    () => s.severityLabel, o.severityLabel));
    fields.append(track(field({ label: "Boleh lanjut ke matching", path: "score.canProceed",
      control: switchInput({ checked: s.canProceed, label: "Berkas grade ini boleh disinkronkan", onChange: (v) => { s.canProceed = v; update(); } }) }),
    () => s.canProceed, o.canProceed));
    const descId = nextId("desc");
    fields.append(track(field({ label: "Deskripsi", path: "score.criteriaDescription", forId: descId,
      control: textArea({ value: s.criteriaDescription, id: descId, onChange: (v) => { s.criteriaDescription = v; update(); } }) }),
    () => s.criteriaDescription, o.criteriaDescription));
    root.append(card("Pita skor mutu", { sub: "Skor mutu = min + (max − min) × mutu berkas di dalam grade-nya." }, fields, bandSlot));
  }

  // ── thresholds ────────────────────────────────────────────────────────
  const sentence = h("div", { class: "rule-sentence" });
  if (draft.matching) {
    const m = draft.matching;
    const o = grade.matching;
    const fields = h("div", { class: "fields" });
    const ids = Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, nextId(k)]));
    fields.append(track(field({ label: "AUTO: maksimal elemen kosong", path: "matching.autoMissingMax", forId: ids.autoMissingMax,
      control: nullableNumber({ value: m.autoMissingMax, id: ids.autoMissingMax, integer: true, min: 0, nullLabel: "tanpa syarat",
        onChange: (v) => { m.autoMissingMax = v; update(); } }),
      hint: "AUTO hanya bila jumlah elemen kosong tidak melebihi angka ini." }), () => m.autoMissingMax, o.autoMissingMax, nullOrNum("tanpa syarat")));
    fields.append(track(field({ label: "AUTO: skor minimal", path: "matching.autoScoreMin", forId: ids.autoScoreMin,
      control: numberInput({ value: m.autoScoreMin, id: ids.autoScoreMin, min: 0, max: 100, onChange: (v) => { m.autoScoreMin = v == null ? Number.NaN : v; update(); } }),
      hint: "Wajib angka 0–100. Pecahan seperti 80,001 dipakai untuk \"di atas 80\"." }), () => m.autoScoreMin, o.autoScoreMin, nullOrNum("—")));
    fields.append(track(field({ label: "REVIEW: jumlah elemen kosong", path: "matching.reviewMissingCount", forId: ids.reviewMissingCount,
      control: nullableNumber({ value: m.reviewMissingCount, id: ids.reviewMissingCount, integer: true, min: 0, nullLabel: "tanpa syarat",
        onChange: (v) => { m.reviewMissingCount = v; update(); } }),
      hint: "REVIEW hanya bila jumlah elemen kosong TEPAT angka ini. Tanpa syarat = berapa pun." }),
    () => m.reviewMissingCount, o.reviewMissingCount, nullOrNum("tanpa syarat")));
    fields.append(track(field({ label: "REVIEW: skor minimal", path: "matching.reviewScoreMin", forId: ids.reviewScoreMin,
      control: numberInput({ value: m.reviewScoreMin, id: ids.reviewScoreMin, min: 0, max: 100, onChange: (v) => { m.reviewScoreMin = v == null ? Number.NaN : v; update(); } }) }),
    () => m.reviewScoreMin, o.reviewScoreMin, nullOrNum("—")));
    fields.append(track(field({ label: "REVIEW: skor di bawah", path: "matching.reviewScoreMax", forId: ids.reviewScoreMax,
      control: numberInput({ value: m.reviewScoreMax, id: ids.reviewScoreMax, min: 0, max: 100, onChange: (v) => { m.reviewScoreMax = v == null ? Number.NaN : v; update(); } }),
      hint: "Batas atas tidak termasuk: REVIEW bila min ≤ skor < max." }), () => m.reviewScoreMax, o.reviewScoreMax, nullOrNum("—")));
    root.append(card("Ambang klasifikasi", {
      sub: gradeId === 6 ? "Grade F tidak dicocokkan; ambang ini tersimpan tetapi tidak dipakai." : "Berlaku untuk baris yang sampai di Pass 3.",
    }, sentence, fields));
  }

  // ── similarity score ─────────────────────────────────────────────────
  const weightsSlot = h("div");
  const missingSlot = h("div");
  const cleaningSlot = h("div");
  const dateSlot = h("div", { class: "radio-list" });
  const weightsRow = field({ label: "Bobot per elemen", path: "matching.weights", control: weightsSlot,
    hint: "Diganti utuh saat disimpan; jumlahnya harus tepat 100. Urutan ikut dikirim dan memengaruhi digit terakhir skor." });
  const missingRow = field({ label: "Elemen yang dihitung kosong", path: "matching.missingElements", control: missingSlot,
    hint: "Jumlah elemen tercentang yang kosong di baris berkas = \"jumlah kosong\" pada aturan AUTO/REVIEW. Diganti utuh." });
  const cleaningRow = field({ label: "Pembersihan nama", path: "matching.nameCleaning", control: cleaningSlot,
    hint: "Untuk nama dan nama ibu, di berkas dan master, di semua pass (termasuk blocking 3 huruf awal nama). Digabung per sakelar." });
  const dateRow = field({ label: "Cara menilai tanggal lahir", path: "matching.dateMatch", control: dateSlot });
  const available = grade.matching?.availableElements || { weights: rules.matchingElements, missingElements: rules.matchingElements };

  function renderWeights() {
    clear(weightsSlot);
    if (draft.reset.weights) {
      const known = currentDry()?.after?.matching?.weights;
      weightsSlot.append(notice("info", "Akan dikembalikan ke bobot bawaan grade (weights: null).",
        known ? `Bawaan: ${Object.entries(known).map(([e, p]) => `${elementLabel(e)} ${fmtNum(p)}%`).join(" · ")}`
          : "Nilai bawaan terlihat setelah Periksa."),
      button("Urungkan", { iconName: "undo", small: true, onClick: () => { draft.reset.weights = false; renderWeights(); update(); } }));
      return;
    }
    const list = draft.matching.weights;
    const rows = h("div", { class: "weights" });
    list.forEach(([element, percent], index) => {
      const bar = h("span");
      bar.style.width = `${Math.max(0, Math.min(100, Number(percent) || 0))}%`;
      const notAvailable = !available.weights.includes(element);
      const input = numberInput({ value: percent, min: 0, max: 100, label: `Bobot ${elementLabel(element)}`,
        onChange: (v) => { list[index][1] = v == null ? Number.NaN : v; bar.style.width = `${Math.max(0, Math.min(100, v || 0))}%`; update(); } });
      rows.append(h("div", { class: "weight-row" },
        h("span", { class: "order" },
          button(null, { iconName: "up", title: "Naikkan", small: true, disabled: index === 0,
            onClick: () => { [list[index - 1], list[index]] = [list[index], list[index - 1]]; renderWeights(); update(); } }),
          button(null, { iconName: "down", title: "Turunkan", small: true, disabled: index === list.length - 1,
            onClick: () => { [list[index + 1], list[index]] = [list[index], list[index + 1]]; renderWeights(); update(); } })),
        h("span", {}, elementLabel(element), notAvailable ? h("div", { class: "field-problem" }, icon("stop"), "tidak dikeluarkan kueri blocking") : null),
        h("span", { class: "input-group" }, input, h("span", { class: "suffix", text: "%" })),
        h("div", { class: "wbar" }, bar),
        button(null, { iconName: "trash", title: `Hapus ${elementLabel(element)}`, small: true,
          onClick: () => { list.splice(index, 1); renderWeights(); update(); } })));
    });
    const sumEl = h("span", { class: "weight-sum-value" });
    const unused = available.weights.filter((e) => !list.some(([x]) => x === e));
    const adder = unused.length ? selectInput({ options: [["", "Tambah elemen…"], ...unused.map((e) => [e, elementLabel(e)])], value: "",
      label: "Tambah elemen bobot", onChange: (v) => { if (v) { list.push([v, 0]); renderWeights(); update(); } } }) : null;
    weightsSlot.append(rows, h("div", { class: "weight-sum" }, sumEl, adder,
      button("Kembalikan ke bawaan", { iconName: "undo", small: true, onClick: () => { draft.reset.weights = true; renderWeights(); update(); } })));
  }

  function renderMissing() {
    clear(missingSlot);
    if (draft.reset.missingElements) {
      const known = currentDry()?.after?.matching?.missingElements;
      missingSlot.append(notice("info", "Akan dikembalikan ke daftar bawaan grade (missingElements: null).",
        known ? `Bawaan: ${known.length ? known.map(elementLabel).join(", ") : "(kosong)"}` : "Nilai bawaan terlihat setelah Periksa."),
      button("Urungkan", { iconName: "undo", small: true, onClick: () => { draft.reset.missingElements = false; renderMissing(); update(); } }));
      return;
    }
    const list = draft.matching.missingElements;
    const grid = h("div", { class: "element-grid" });
    for (const e of rules.matchingElements) {
      const disabled = !available.missingElements.includes(e) && !list.includes(e);
      const box = h("input", { type: "checkbox", checked: list.includes(e), disabled });
      box.addEventListener("change", () => {
        if (box.checked) list.push(e);
        else list.splice(list.indexOf(e), 1);
        // Keep the saved order so re-ticking an element is not reported as a change.
        const rank = (x) => {
          const saved = grade.matching.missingElements.indexOf(x);
          return saved >= 0 ? saved : 100 + rules.matchingElements.indexOf(x);
        };
        list.sort((a, b) => rank(a) - rank(b));
        update();
      });
      grid.append(h("label", { class: `check ${disabled ? "is-disabled" : ""}` }, box, elementLabel(e)));
    }
    missingSlot.append(grid, h("div", { class: "add-row" },
      button("Kembalikan ke bawaan", { iconName: "undo", small: true, onClick: () => { draft.reset.missingElements = true; renderMissing(); update(); } })));
  }

  function renderCleaning() {
    clear(cleaningSlot);
    if (draft.reset.nameCleaning) {
      cleaningSlot.append(notice("info", "Akan dikembalikan ke bawaan: semua sakelar mati (nameCleaning: null)."),
        button("Urungkan", { iconName: "undo", small: true, onClick: () => { draft.reset.nameCleaning = false; renderCleaning(); update(); } }));
      return;
    }
    const nc = draft.matching.nameCleaning;
    for (const [key, info] of Object.entries(NAME_CLEANING)) {
      cleaningSlot.append(h("div", { class: "toggle-item" }, switchInput({ checked: Boolean(nc[key]), label: `${info.label} (${key})`,
        onChange: (v) => { nc[key] = v; update(); } }), h("div", { class: "field-hint", text: info.example })));
    }
    cleaningSlot.append(h("div", { class: "add-row" },
      button("Kembalikan ke bawaan", { iconName: "undo", small: true, onClick: () => { draft.reset.nameCleaning = true; renderCleaning(); update(); } })));
  }

  function renderDate() {
    clear(dateSlot);
    const name = nextId("date");
    for (const [value, text] of Object.entries(DATE_MATCH)) {
      const radio = h("input", { type: "radio", name, value, checked: !draft.reset.dateMatch && draft.matching.dateMatch === value });
      radio.addEventListener("change", () => { draft.matching.dateMatch = value; draft.reset.dateMatch = false; renderDate(); update(); });
      dateSlot.append(h("label", { class: "check" }, radio, h("span", {}, h("code", { text: value }), " ", text)));
    }
    if (draft.reset.dateMatch) dateSlot.append(notice("info", "Akan dikembalikan ke bawaan (dateMatch: null → similarity)."));
    else if (grade.matching.dateMatch !== "similarity" || draft.matching.dateMatch !== "similarity") {
      dateSlot.append(h("div", {}, button("Kembalikan ke bawaan", { iconName: "undo", small: true,
        onClick: () => { draft.reset.dateMatch = true; renderDate(); update(); } })));
    }
  }

  const analysisSlot = h("div");
  const calcSlot = h("div");
  const calcMemory = { sim: {}, empty: {} };
  if (matchable) {
    renderWeights();
    renderMissing();
    renderCleaning();
    renderDate();
    const o = grade.matching;
    markers.push(() => weightsRow.setChanged(draft.reset.weights || !same(draft.matching.weights, Object.entries(o.weights)),
      Object.entries(o.weights).map(([e, p]) => `${elementLabel(e)} ${fmtNum(p)}%`).join(" · ")));
    markers.push(() => missingRow.setChanged(draft.reset.missingElements || !same(draft.matching.missingElements, o.missingElements),
      o.missingElements.length ? o.missingElements.map(elementLabel).join(", ") : "(tidak ada)"));
    markers.push(() => cleaningRow.setChanged(draft.reset.nameCleaning || !same(draft.matching.nameCleaning, o.nameCleaning),
      Object.entries(o.nameCleaning).map(([k, v]) => `${k} ${v ? "nyala" : "mati"}`).join(" · ")));
    markers.push(() => dateRow.setChanged(draft.reset.dateMatch || draft.matching.dateMatch !== o.dateMatch, o.dateMatch));
    root.append(card("Skor kemiripan (Pass 3)", { sub: "Skor = Σ kemiripan elemen (0–1) × bobot. Elemen kosong bernilai 0." },
      h("div", { class: "fields" }, weightsRow.el, missingRow.el, cleaningRow.el, dateRow.el)));
    root.append(card("Analisis: apa akibat aturan ini", {
      sub: "Dihitung di browser dengan rumus yang sama dengan service (lib/_config.py analisis_matching), dari draf saat ini.",
    }, analysisSlot));
    root.append(calcSlot);
    const b = grade.matching.blocking || {};
    root.append(card("Kueri blocking", { sub: "Hanya dibaca. Diubah lewat basis data/migrasi (tabel matching_queries), bukan API." },
      b.note ? h("p", { class: "muted small", text: b.note }) : null,
      b.spec ? h("div", { class: "stack" }, h("h3", { text: "Ringkasan kandidat (versi StarRocks)" }),
        h("pre", { text: JSON.stringify(b.spec, null, 2) })) : null,
      b.query ? h("details", {}, h("summary", { text: "Tampilkan SQL" }), h("pre", { text: b.query })) : h("p", { class: "muted", text: "Tidak ada kueri." }),
      h("p", { class: "muted small", text: `Elemen yang bisa diberi bobot dengan kueri ini: ${(grade.matching.availableElements?.weights || []).map(elementLabel).join(", ") || "tidak diketahui"}.` })));
  }

  // ── derived views ─────────────────────────────────────────────────────
  function simulatedGrades() {
    return rules.grades.map((g) => {
      if (g.gradeId !== gradeId) return g;
      return { ...g, criteria: draft.criteria ? { ...g.criteria, ...draft.criteria } : g.criteria,
        score: draft.score ? { ...g.score, ...draft.score } : g.score };
    });
  }

  function renderAnalysis(m) {
    clear(analysisSlot);
    if (!m.weights || !m.missingElements) {
      analysisSlot.append(notice("info", null, "Jalankan Periksa untuk melihat analisis dengan nilai bawaan grade."));
      return;
    }
    const a = analyseMatching(m);
    const head = h("tr", {}, h("th", { class: "num", text: "Elemen kosong" }), h("th", { class: "num", text: "Skor tertinggi" }),
      h("th", {}, "Rentang skor yang mungkin", meterAxis()), h("th", { text: "AUTO" }), h("th", { text: "REVIEW" }),
      h("th", { text: "Baris terbaik jadi" }));
    const body = a.rows.map((row) => h("tr", {},
      h("td", { class: "num", text: String(row.missingCount) }),
      h("td", { class: "num", text: fmtNum(row.best) }),
      h("td", {}, meter(m, row.missingCount, row.best),
        row.patterns.length > 1 ? h("details", { class: "small" }, h("summary", { text: `${row.patterns.length} pola` }),
          h("ul", {}, row.patterns.map((p) => h("li", { text: `${p.set.map(elementLabel).join(" + ") || "tidak ada"} kosong → ${fmtNum(p.value)}` })))) : null),
      h("td", {}, row.autoPossible ? badge("good", "bisa") : badge("neutral", "tidak")),
      h("td", {}, row.reviewPossible ? badge("warning", "bisa", "half") : badge("neutral", "tidak")),
      h("td", {}, outcome(row.bestOutcome))));
    analysisSlot.append(
      h("div", { class: "rule-sentence" }, h("p", {}, `Elemen yang dihitung kosong: ${m.missingElements.length ? m.missingElements.map(elementLabel).join(", ") : "tidak ada (jumlah kosong selalu 0)"}.`)),
      h("div", { class: "table-wrap", style: { marginTop: "12px" } }, h("table", {}, h("thead", {}, head), h("tbody", {}, body))),
      meterLegend(),
      a.warnings.length
        ? h("div", { class: "stack", style: { marginTop: "12px" } }, notice("warning", "Peringatan (sama dengan yang akan dikirim service)", a.warnings))
        : h("div", { class: "stack", style: { marginTop: "12px" } }, notice("good", null, "Tidak ada peringatan.")));
  }

  function update() {
    const patch = buildPatch(grade, draft);
    const count = countFields(patch);
    ctx.setDirty(count > 0);
    for (const mark of markers) mark();

    const grades = simulatedGrades();
    if (draft.criteria) {
      clear(criteriaProblemsSlot);
      const problems = criteriaProblems(grades);
      if (problems.length) criteriaProblemsSlot.append(notice("critical", "Akan ditolak service", problems));
    }
    if (draft.score) {
      clear(bandSlot);
      bandSlot.append(h("h3", { style: { margin: "14px 0 4px" }, text: "Pita semua grade (dengan draf ini)" }),
        bandStrip(grades, gradeId, bandProblems(grades)));
    }
    let m = null;
    if (draft.matching) {
      m = effectiveMatching(grade, draft, flow.dryFor(patch)?.after ?? null);
      clear(sentence);
      sentence.append(h("p", {}, h("strong", { text: "AUTO " }), `bila ${autoText(m)}.`),
        h("p", {}, h("strong", { text: "REVIEW " }), `bila ${reviewText(m)}.`),
        h("p", {}, h("strong", { text: "UNMATCH " }), "selain itu. Kandidat seri → CONFLICT."));
      const problems = matchingProblems(gradeId, {
        ...m,
        weights: draft.reset.weights ? null : m.weights,
        missingElements: draft.reset.missingElements ? null : m.missingElements,
      }, grade.matching.availableElements);
      const weightIssues = problems.filter((p) => /bobot|weights|elemen '/.test(p));
      const missingIssues = problems.filter((p) => p.includes("elemen kosong"));
      const otherIssues = problems.filter((p) => !weightIssues.includes(p) && !missingIssues.includes(p));
      if (otherIssues.length) sentence.append(notice("critical", "Akan ditolak service", otherIssues));
      if (matchable) {
        weightsRow.setProblems(weightIssues);
        missingRow.setProblems(missingIssues);
      }
    }
    if (matchable) {
      const sumEl = weightsSlot.querySelector(".weight-sum-value");
      if (sumEl && draft.matching.weights) {
        const sum = draft.matching.weights.reduce((acc, [, p]) => acc + (Number.isFinite(p) ? p : 0), 0);
        clear(sumEl);
        sumEl.append(Math.abs(sum - 100) <= 1e-6 ? badge("good", `Jumlah ${fmtNum(sum)}%`) : badge("critical", `Jumlah ${fmtNum(Math.round(sum * 1e6) / 1e6)}% — harus 100`));
      }
      renderAnalysis(m);
      clear(calcSlot);
      if (m.weights && m.missingElements) calcSlot.append(scoreCalculator(m, `Grade ${letter} (draf)`, calcMemory));
    }
    flow.update(patch, count, hasInvalidNumber(patch));
  }

  root.append(flow.barSlot);
  update();
  return null;
}

function outcome(value) {
  if (value === "AUTO") return badge("good", "AUTO", "check");
  if (value === "REVIEW") return badge("warning", "REVIEW", "half");
  return badge("neutral", "UNMATCH", "minus");
}

function fmtPctOrOff(value) {
  return value == null ? "tidak diperiksa" : fmtPct(value);
}

function nullOrNum(nullText) {
  return (value) => (value == null ? nullText : fmtNum(value));
}
