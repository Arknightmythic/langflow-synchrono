// Browser-side mirrors of the service rules (lib/_config.py, lib/_grading.py,
// lib/_shared.py). They give instant feedback while editing; the dry run on
// the service stays the authority.

import { GRADE_LETTERS } from "./fields.js";

const ELEMENTS = ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"];
const SUM_TOLERANCE = 1e-6;

export function combinations(items, size) {
  const out = [];
  const pick = (start, acc) => {
    if (acc.length === size) {
      out.push(acc.slice());
      return;
    }
    for (let i = start; i < items.length; i += 1) {
      acc.push(items[i]);
      pick(i + 1, acc);
      acc.pop();
    }
  };
  pick(0, []);
  return out;
}

// Same fold as the SQL: fractions added left to right in weight order, then x100.
export function bestScore(weights, missing) {
  let total = 0;
  for (const [element, percent] of Object.entries(weights || {})) {
    if (!percent) continue;
    const similarity = missing.includes(element) ? 0 : 1;
    total = total + similarity * (Number(percent) / 100);
  }
  return total * 100;
}

export function score(weights, similarities) {
  let total = 0;
  for (const [element, percent] of Object.entries(weights || {})) {
    if (!percent) continue;
    total = total + Number(similarities[element] || 0) * (Number(percent) / 100);
  }
  return total * 100;
}

export function canAuto(m, missingCount, value) {
  return m.autoScoreMin != null && (m.autoMissingMax == null || missingCount <= m.autoMissingMax)
    && value >= m.autoScoreMin;
}

export function canReview(m, missingCount, value) {
  return (m.reviewMissingCount == null || missingCount === m.reviewMissingCount)
    && m.reviewScoreMin != null && m.reviewScoreMax != null
    && m.reviewScoreMin <= value && value < m.reviewScoreMax;
}

export function classify(m, missingCount, value) {
  if (canAuto(m, missingCount, value)) return "AUTO";
  if (canReview(m, missingCount, value)) return "REVIEW";
  return "UNMATCH";
}

// Python's `_angka`: two decimals when exact, otherwise %.16g.
export function angka(x) {
  if (x == null) return "-";
  const num = Number(x);
  const rounded = Number(num.toFixed(2));
  if (num !== rounded) {
    let s = num.toPrecision(16);
    if (!s.includes("e") && s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s;
  }
  return String(rounded);
}

function pyList(items) {
  return `[${items.map((item) => `'${item}'`).join(", ")}]`;
}

export function analyseMatching(m) {
  const weights = m.weights || {};
  const missing = [...(m.missingElements || [])];
  const amax = m.autoMissingMax;
  const amin = m.autoScoreMin;
  const rcnt = m.reviewMissingCount;
  const rmin = m.reviewScoreMin;
  const rmax = m.reviewScoreMax;

  const perCount = [];
  const best = [];
  for (let n = 0; n <= missing.length; n += 1) {
    const patterns = combinations(missing, n).map((set) => ({ value: bestScore(weights, set), set }));
    perCount.push(patterns);
    best.push(Math.max(...patterns.map((p) => p.value)));
  }

  const warnings = [];
  if (amin != null && best[0] < amin) {
    warnings.push(`AUTO tidak mungkin tercapai lewat skor: skor tertinggi ${angka(best[0])} `
      + `di bawah autoScoreMin ${angka(amin)}.`);
  }
  if (rcnt != null) {
    if (rcnt > missing.length) {
      const list = missing.length ? ` (${missing.join(", ")})` : "";
      warnings.push(`REVIEW tidak pernah terjadi: butuh tepat ${rcnt} elemen kosong, `
        + `padahal elemen yang dihitung kosong hanya ${missing.length}${list}.`);
    } else if (rmin != null && best[rcnt] < rmin) {
      warnings.push(`REVIEW tidak mungkin tercapai: dengan ${rcnt} elemen kosong, skor `
        + `tertinggi ${angka(best[rcnt])} di bawah reviewScoreMin ${angka(rmin)}.`);
    }
  }

  const floor = [amin, rmin].filter((x) => x != null);
  if (floor.length) {
    const lowest = Math.min(...floor);
    for (let n = 0; n <= missing.length; n += 1) {
      const dropped = perCount[n].filter((p) => p.value >= lowest
        && !canAuto(m, n, p.value) && !canReview(m, n, p.value));
      if (!dropped.length) continue;
      const autoReason = amax != null && n > amax
        ? `tidak bisa AUTO (kosong ${n} > autoMissingMax ${amax})`
        : `tidak bisa AUTO (skor di bawah autoScoreMin ${angka(amin)})`;
      const reviewReason = rcnt != null && n !== rcnt
        ? `REVIEW hanya untuk tepat ${rcnt} elemen kosong`
        : `skornya di luar pita REVIEW ${angka(rmin)}–<${angka(rmax)}`;
      let patterns = dropped.slice(0, 4).map((p) => `${p.set.join(" + ")} kosong -> ${angka(p.value)}`).join("; ");
      if (dropped.length > 4) patterns += `; dan ${dropped.length - 4} pola lain`;
      warnings.push(`Baris dengan ${n} elemen kosong yang selebihnya cocok sempurna `
        + `jatuh ke UNMATCH: ${autoReason}, dan ${reviewReason}. (${patterns})`);
    }
  }

  // Rows with n missing elements can score anywhere in [0, best[n]].
  const rows = best.map((value, n) => ({
    missingCount: n,
    best: value,
    patterns: perCount[n],
    autoPossible: canAuto(m, n, value),
    reviewPossible: (rcnt == null || n === rcnt) && rmin != null && rmax != null && rmin < rmax && value >= rmin,
    bestOutcome: classify(m, n, value),
  }));
  return { maxScoreByMissingCount: Object.fromEntries(best.map((v, n) => [String(n), v])), warnings, rows };
}

export function analyseGlobal(values) {
  const warnings = [];
  for (const combo of values.gradeECombinations || []) {
    if (!Array.isArray(combo)) continue;
    if (!combo.includes("nama") || !combo.includes("tanggal_lahir")) {
      warnings.push(`Kombinasi grade E ${pyList(combo)}: blocking grade E mensyaratkan nama `
        + "DAN tanggal lahir (3 huruf awal nama, hari & bulan lahir). Berkas yang hanya "
        + "memenuhi kombinasi ini tidak akan mendapat kandidat di Pass 3.");
    }
  }
  const eps = values.conflictEpsilon;
  if (typeof eps === "number" && eps > 5) {
    warnings.push(`conflictEpsilon ${angka(eps)} poin cukup lebar: kandidat yang skornya `
      + "berbeda sebesar itu pun dianggap seri, sehingga CONFLICT akan banyak.");
  }
  return warnings;
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function matchingProblems(gradeId, m, available) {
  const g = GRADE_LETTERS[gradeId] || gradeId;
  const problems = [];
  for (const key of ["autoScoreMin", "reviewScoreMin", "reviewScoreMax"]) {
    const v = m[key];
    if (!isNumber(v) || v < 0 || v > 100) problems.push(`Grade ${g}: ${key} harus angka 0–100.`);
  }
  for (const key of ["autoMissingMax", "reviewMissingCount"]) {
    const v = m[key];
    if (v != null && (!Number.isInteger(v) || v < 0)) {
      problems.push(`Grade ${g}: ${key} harus bilangan bulat ≥ 0 atau tanpa syarat.`);
    }
  }
  if (isNumber(m.reviewScoreMin) && isNumber(m.reviewScoreMax) && m.reviewScoreMin > m.reviewScoreMax) {
    problems.push(`Grade ${g}: reviewScoreMin (${angka(m.reviewScoreMin)}) melebihi reviewScoreMax (${angka(m.reviewScoreMax)}).`);
  }
  if (gradeId > 5) return problems;

  if (m.weights !== null) {
    const entries = Object.entries(m.weights || {});
    if (!entries.length) problems.push(`Grade ${g}: weights harus berisi minimal satu elemen.`);
    for (const [element, percent] of entries) {
      if (!isNumber(percent) || percent < 0 || percent > 100) {
        problems.push(`Grade ${g}: bobot ${element} harus angka 0–100 (persen).`);
      } else if (available && !available.weights.includes(element)) {
        problems.push(`Grade ${g}: elemen '${element}' tidak dikeluarkan kueri blocking grade ini.`);
      }
    }
    if (entries.length && entries.every(([, p]) => isNumber(p))) {
      const sum = entries.reduce((acc, [, p]) => acc + p, 0);
      if (Math.abs(sum - 100) > SUM_TOLERANCE) problems.push(`Grade ${g}: jumlah bobot ${angka(sum)}, harus 100.`);
    }
  }
  if (m.missingElements !== null && available) {
    for (const element of m.missingElements || []) {
      if (!available.missingElements.includes(element)) {
        problems.push(`Grade ${g}: elemen kosong '${element}' tidak dikeluarkan kueri blocking grade ini.`);
      }
    }
  }
  return problems;
}

const MIN_KEYS = [...ELEMENTS.map((e) => ["minCompleteness", e]), ["minNikTrusted", null]];

function minValue(criteria, [key, element]) {
  const value = element ? criteria.minCompleteness?.[element] : criteria[key];
  return value == null ? 0 : value;
}

export function criteriaProblems(grades) {
  const problems = [];
  const rows = grades.filter((grade) => grade.criteria).map((grade) => ({ id: grade.gradeId, ...grade.criteria }));
  const orders = rows.map((row) => row.order);
  if (new Set(orders).size !== orders.length) {
    problems.push(`Nilai urutan berulang: ${[...orders].sort((a, b) => a - b).join(", ")}. Urutan evaluasi jadi tidak tentu.`);
  }
  for (const row of rows) {
    const g = GRADE_LETTERS[row.id];
    for (const [key, element] of MIN_KEYS) {
      const value = element ? row.minCompleteness?.[element] : row[key];
      if (value != null && (value < 0 || value > 1)) {
        problems.push(`Grade ${g}: ${element || key} harus antara 0% dan 100%.`);
      }
    }
    if (row.nikColumn === "terlarang") {
      if (row.minCompleteness?.nik != null) {
        problems.push(`Grade ${g}: kolom NIK terlarang, tapi kelengkapan NIK minimal diisi.`);
      }
      if (row.minNikTrusted != null) {
        problems.push(`Grade ${g}: kolom NIK terlarang, tapi NIK tepercaya minimal diisi; grade ini tak akan pernah cocok.`);
      }
    }
  }
  const sorted = [...rows].sort((a, b) => a.order - b.order);
  sorted.forEach((later, index) => {
    for (const earlier of sorted.slice(0, index)) {
      if (later.nikColumn !== earlier.nikColumn) continue;
      if (MIN_KEYS.every((k) => minValue(later, k) >= minValue(earlier, k))) {
        problems.push(`Grade ${GRADE_LETTERS[later.id]} tidak akan pernah tercapai: seluruh ambangnya sama ketat `
          + `atau lebih ketat dari grade ${GRADE_LETTERS[earlier.id]} yang dievaluasi lebih dulu.`);
        break;
      }
    }
  });
  return problems;
}

export function bandProblems(grades) {
  const problems = [];
  const bands = grades.filter((grade) => grade.score).map((grade) => ({
    letter: grade.gradeLetter, min: grade.score.min, max: grade.score.max,
  }));
  for (const band of bands) {
    if (band.min > band.max) problems.push(`Grade ${band.letter}: skor minimal (${band.min}) melebihi maksimal (${band.max}).`);
    if (band.min < 0 || band.max > 100) problems.push(`Grade ${band.letter}: pita skor di luar 0–100.`);
  }
  const sorted = [...bands].sort((a, b) => a.min - b.min);
  for (let i = 1; i < sorted.length; i += 1) {
    const a = sorted[i - 1];
    const b = sorted[i];
    if (b.min <= a.max) {
      problems.push(`Pita skor tumpang tindih: grade ${a.letter} (${a.min}–${a.max}) dan ${b.letter} (${b.min}–${b.max}).`);
    }
  }
  return problems;
}

export function pyRound(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

// input: { present: {element: bool, wilayah: bool}, rate: {element: 0..1}, trusted: 0..1 }
export function simulateGrading(rules, input) {
  const present = input.present;
  const rate = Object.fromEntries(ELEMENTS.map((e) => [e, present[e] ? Number(input.rate[e] || 0) : 0]));
  const trusted = present.nik ? Number(input.trusted || 0) : 0;
  const steps = [];
  let gradeId = null;

  const criteria = rules.grades.filter((g) => g.criteria && g.criteria.active)
    .sort((a, b) => a.criteria.order - b.criteria.order);
  for (const grade of criteria) {
    const c = grade.criteria;
    const failures = [];
    if (c.nikColumn === "wajib" && !present.nik) failures.push("kolom NIK wajib ada");
    if (c.nikColumn === "terlarang" && present.nik) failures.push("kolom NIK harus tidak ada");
    for (const element of ELEMENTS) {
      const min = c.minCompleteness?.[element];
      if (min != null && rate[element] < min) failures.push({ element, have: rate[element], need: min });
    }
    if (c.minNikTrusted != null && trusted < c.minNikTrusted) {
      failures.push({ element: "nik_tepercaya", have: trusted, need: c.minNikTrusted });
    }
    steps.push({ gradeId: grade.gradeId, order: c.order, failures });
    if (!failures.length) {
      gradeId = grade.gradeId;
      break;
    }
  }

  let combo = null;
  if (gradeId == null) {
    const combos = rules.global?.grading?.gradeECombinations || [];
    combo = combos.find((set) => set.length && set.every((e) => (e === "wilayah" ? present.wilayah : present[e]))) || null;
    gradeId = combo ? 5 : 6;
  }

  const grade = rules.grades.find((g) => g.gradeId === gradeId);
  const band = grade?.score;
  const presentElements = ELEMENTS.filter((e) => present[e]);
  const completeness = presentElements.length
    ? presentElements.reduce((acc, e) => acc + rate[e], 0) / presentElements.length : 0;
  const weights = rules.global?.grading?.scoreWeights || { kelengkapan: 0.6, nik_tepercaya: 0.4 };
  const quality = present.nik ? weights.kelengkapan * completeness + weights.nik_tepercaya * trusted : completeness;
  const raw = band ? band.min + (band.max - band.min) * quality : null;
  return {
    gradeId, steps, combo, completeness, quality, trusted, weights,
    band, rawScore: raw, score: raw == null ? null : pyRound(raw),
  };
}

const encoder = new TextEncoder();

function jaro(p, t) {
  const pLen = p.length;
  const tLen = t.length;
  if (pLen === 0 || tLen === 0) return 0;
  if (pLen === 1 && tLen === 1) return p[0] === t[0] ? 1 : 0;
  let pEnd = pLen;
  let tEnd = tLen;
  let bound;
  if (tLen > pLen) {
    bound = Math.trunc(tLen / 2) - 1;
    if (tLen > pLen + bound) tEnd = pLen + bound;
  } else {
    bound = Math.trunc(pLen / 2) - 1;
    if (pLen > tLen + bound) pEnd = tLen + bound;
  }
  let start = 0;
  const limit = Math.min(pEnd, tEnd);
  while (start < limit && p[start] === t[start]) start += 1;
  let common = start;
  let transpositions = 0;
  const pView = pEnd - start;
  const tView = tEnd - start;
  if (pView > 0 && tView > 0) {
    const pFlag = new Array(pView).fill(false);
    const tFlag = new Array(tView).fill(false);
    let flagged = 0;
    for (let j = 0; j < tView; j += 1) {
      const low = Math.max(0, j - bound);
      const high = Math.min(pView - 1, j + bound);
      const c = t[start + j];
      for (let i = low; i <= high; i += 1) {
        if (!pFlag[i] && p[start + i] === c) {
          pFlag[i] = true;
          tFlag[j] = true;
          flagged += 1;
          break;
        }
      }
    }
    common += flagged;
    if (common === 0) return 0;
    let k = 0;
    for (let j = 0; j < tView; j += 1) {
      if (!tFlag[j]) continue;
      while (!pFlag[k]) k += 1;
      if (p[start + k] !== t[start + j]) transpositions += 1;
      k += 1;
    }
  }
  transpositions = Math.trunc(transpositions / 2);
  let sim = 0;
  sim += common / pLen;
  sim += common / tLen;
  sim += (common - transpositions) / common;
  return sim / 3;
}

// DuckDB jaro_winkler_similarity: bytes, prefix weight 0.1, no cutoff.
export function jaroWinkler(a, b) {
  const p = encoder.encode(a);
  const t = encoder.encode(b);
  const maxPrefix = Math.min(p.length, t.length, 4);
  let prefix = 0;
  while (prefix < maxPrefix && t[prefix] === p[prefix]) prefix += 1;
  let sim = jaro(p, t);
  if (sim > 0.7) sim += prefix * 0.1 * (1 - sim);
  return sim;
}
