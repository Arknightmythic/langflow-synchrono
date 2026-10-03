import { append, attachTip, badge, clear, copyText, fmtNum, fromPercent, h, icon, prettyJson, toPercent } from "./dom.js";
import { currentTarget } from "./api.js";
import { GRADE_LETTERS } from "./fields.js";

export function pageHead(title, lede, ...actions) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", { text: title }), lede ? h("p", { class: "lede", text: lede }) : null),
    actions.length ? h("div", { class: "actions" }, actions) : null);
}

export function card(title, { sub, actions, id, className } = {}, ...body) {
  return h("section", { class: `card ${className || ""}`.trim(), id },
    title ? h("header", {}, h("h2", { text: title }), sub ? h("span", { class: "sub", text: sub }) : null,
      actions ? h("div", { class: "actions" }, actions) : null) : null,
    body);
}

const NOTICE_ICON = { critical: "stop", warning: "alert", good: "check", info: "info" };

export function notice(kind, title, content) {
  const body = h("div", { class: "notice-body" }, title ? h("strong", { text: title }) : null);
  if (Array.isArray(content)) body.append(h("ul", {}, content.map((item) => h("li", {}, item))));
  else if (content != null) body.append(content instanceof Node ? content : h("div", { text: content }));
  return h("div", { class: `notice notice--${kind}`, role: kind === "critical" ? "alert" : null },
    icon(NOTICE_ICON[kind] || "info"), body);
}

export function toast(kind, text, timeout = 5000) {
  const box = document.getElementById("toasts");
  const el = notice(kind, null, text);
  el.classList.add("toast");
  box.append(el);
  setTimeout(() => el.remove(), timeout);
}

export function button(label, { kind, iconName, onClick, title, small, type = "button", disabled } = {}) {
  const cls = ["btn", kind ? `btn--${kind}` : "", small ? "btn--small" : "", !label ? "btn--icon" : ""].join(" ").trim();
  return h("button", { class: cls, type, title, "aria-label": !label ? title : null, disabled, on: { click: onClick } },
    iconName ? icon(iconName) : null, label);
}

export async function busy(btn, task) {
  btn.disabled = true;
  btn.classList.add("is-busy");
  try {
    return await task();
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-busy");
  }
}

const dialogEl = () => document.getElementById("dialog");

export function openDialog({ title, body, actions = [], wide }) {
  const dialog = dialogEl();
  clear(dialog);
  dialog.style.width = wide ? "min(1100px, calc(100vw - 32px))" : "";
  const close = () => dialog.open && dialog.close();
  append(dialog, [
    h("div", { class: "dialog-head" }, h("h2", { id: "dialog-title", text: title }),
      button(null, { iconName: "x", title: "Tutup", onClick: close })),
    h("div", { class: "dialog-body" }, body),
    actions.length ? h("div", { class: "dialog-actions" }, actions) : null]);
  dialog.showModal();
  return { close, dialog };
}

export function confirmDialog(title, text, okLabel = "Lanjutkan", cancelLabel = "Batal") {
  return new Promise((resolve) => {
    let answer = false;
    const ok = button(okLabel, { kind: "primary", onClick: () => { answer = true; handle.close(); } });
    const cancel = button(cancelLabel, { onClick: () => handle.close() });
    const handle = openDialog({ title, body: h("p", { text }), actions: [cancel, ok] });
    handle.dialog.addEventListener("close", () => resolve(answer), { once: true });
    ok.focus();
  });
}

export function field({ label, path, control, hint, forId }) {
  const change = h("div", { class: "field-change" });
  const problems = h("div", { class: "field-problems" });
  const el = h("div", { class: "field" },
    h("div", { class: "field-label" },
      forId ? h("label", { for: forId, text: label }) : h("span", { class: "label", text: label }),
      path ? h("code", { text: path }) : null),
    h("div", { class: "field-body" }, control, hint ? h("div", { class: "field-hint" }, hint) : null, change, problems));
  return {
    el,
    setChanged(changed, before) {
      el.classList.toggle("is-changed", Boolean(changed));
      change.textContent = changed ? `Diubah — sebelumnya ${before}` : "";
    },
    setProblems(list) {
      clear(problems);
      for (const text of list || []) problems.append(h("div", { class: "field-problem" }, icon("stop"), h("span", { text })));
    },
  };
}

let uid = 0;
export const nextId = (prefix) => `${prefix}-${++uid}`;

export function numberInput({ value, min, max, step = "any", id, onChange, label, width, integer }) {
  const input = h("input", { type: "number", id, min, max, step: integer ? 1 : step, value: value ?? "", "aria-label": label });
  if (width) input.style.width = width;
  input.addEventListener("input", () => {
    const raw = input.value.trim();
    let num = raw === "" ? NaN : Number(raw);
    if (integer && !Number.isInteger(num)) num = NaN;
    input.classList.toggle("is-invalid", raw !== "" && !Number.isFinite(num));
    onChange(raw === "" ? null : num, raw);
  });
  return input;
}

// A number that may also be null ("tanpa syarat", "tidak diperiksa").
export function nullableNumber({ value, nullLabel, id, min, max, step, onChange, suffix, label, toDisplay, fromDisplay, integer }) {
  const show = toDisplay || ((v) => v);
  const parse = fromDisplay || ((v) => v);
  let lastNumber = value != null ? value : null;
  const input = numberInput({
    value: value != null ? show(value) : "", min, max, step, id, label, integer,
    onChange: (num) => {
      if (num == null || !Number.isFinite(num)) {
        onChange(Number.NaN);
        return;
      }
      lastNumber = parse(num);
      onChange(lastNumber);
    },
  });
  input.disabled = value == null;
  const box = h("input", { type: "checkbox", checked: value == null });
  box.addEventListener("change", () => {
    input.disabled = box.checked;
    if (box.checked) onChange(null);
    else {
      if (lastNumber == null) {
        lastNumber = parse(Number(min ?? 0));
      }
      input.value = String(show(lastNumber));
      onChange(lastNumber);
      input.focus();
    }
  });
  return h("div", { class: "input-group" }, input, suffix ? h("span", { class: "suffix", text: suffix }) : null,
    h("label", { class: "check" }, box, nullLabel));
}

export function percentInput({ fraction, nullable, nullLabel, id, onChange, label }) {
  const opts = {
    value: fraction, id, label, min: 0, max: 100, step: "any", suffix: "%",
    toDisplay: (f) => toPercent(f), fromDisplay: (p) => fromPercent(p),
  };
  if (nullable) return nullableNumber({ ...opts, nullLabel, onChange });
  const input = numberInput({ ...opts, value: toPercent(fraction), onChange: (p) => onChange(p == null ? Number.NaN : fromPercent(p)) });
  return h("div", { class: "input-group" }, input, h("span", { class: "suffix", text: "%" }));
}

export function switchInput({ checked, label, id, onChange, disabled }) {
  const input = h("input", { type: "checkbox", class: "switch", id, checked, disabled, role: "switch" });
  input.addEventListener("change", () => onChange(input.checked));
  return h("label", { class: `check ${disabled ? "is-disabled" : ""}` }, input, label);
}

export function selectInput({ options, value, id, onChange, label, disabled }) {
  const select = h("select", { id, "aria-label": label, disabled },
    options.map(([v, text]) => h("option", { value: v, text })));
  select.value = value ?? "";
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

export function textInput({ value, id, onChange, label, placeholder, width }) {
  const input = h("input", { type: "text", id, value: value ?? "", "aria-label": label, placeholder });
  if (width) input.style.width = width;
  input.addEventListener("input", () => onChange(input.value));
  return input;
}

export function textArea({ value, id, onChange, label, rows = 3 }) {
  const area = h("textarea", { id, rows: String(rows), "aria-label": label });
  area.value = value ?? "";
  area.addEventListener("input", () => onChange(area.value));
  return area;
}

export function valueText(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") return fmtNum(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function valueNode(value) {
  if (value === null || value === undefined) return h("span", { class: "value value--null", text: "null" });
  return h("span", { class: "value", text: typeof value === "string" ? `"${value}"` : JSON.stringify(value) });
}

export function changesTable(changes) {
  if (!changes || !changes.length) return h("p", { class: "muted", text: "Tidak ada field yang berubah." });
  return h("div", { class: "table-wrap" }, h("table", { class: "table-tight changes" },
    h("thead", {}, h("tr", {}, h("th", { text: "Bagian" }), h("th", { text: "Field" }), h("th", { text: "Dari" }), h("th", { text: "Ke" }))),
    h("tbody", {}, changes.map((c) => h("tr", {},
      h("td", { text: c.section }), h("td", {}, h("code", { text: c.field })),
      h("td", {}, valueNode(c.from)), h("td", {}, valueNode(c.to)))))));
}

export function resultPanel(result, { saved, stale, onDismiss } = {}) {
  const problems = result.problems || [];
  const warnings = result.warnings || [];
  let head;
  if (problems.length) {
    head = notice("critical", saved ? "Ditolak service (HTTP 409) — tidak ada yang disimpan" : "Periksa: ditolak (HTTP 409)", problems);
  } else if (saved) {
    head = notice("good", "Tersimpan", h("span", {}, "Versi konfigurasi baru: ", h("code", { text: result.configVersion || "(tidak berubah)" })));
  } else {
    head = notice("good", "Periksa: lolos validasi (dry run, belum disimpan)", "Kirim Simpan untuk menerapkan.");
  }
  const staleNote = h("p", { class: "field-problem", hidden: !stale }, icon("alert"),
    h("span", { text: "Draf sudah berubah sejak diperiksa. Hasil di bawah untuk draf sebelumnya — periksa lagi." }));
  const panel = card(saved ? "Hasil simpan" : "Hasil periksa", {
    id: "result", className: "result",
    actions: onDismiss ? [button(null, { iconName: "x", title: "Tutup hasil", onClick: onDismiss, small: true })] : null,
  },
  staleNote,
  head,
  warnings.length ? h("div", { class: "stack" }, notice("warning", "Peringatan dari service (tetap boleh disimpan)", warnings)) : null,
  problems.length ? null : h("div", { class: "stack" }, h("h3", { text: "Perubahan" }), changesTable(result.changed)));
  panel.setStale = (flag) => { staleNote.hidden = !flag; };
  return panel;
}

export function curlCommand(method, path, body) {
  const target = currentTarget();
  const url = `${target?.url || window.location.origin}${path}`;
  const lines = [`curl -X ${method} '${url}'`, "  -H 'x-api-key: $API_KEY'"];
  if (body !== undefined) {
    lines.push("  -H 'Content-Type: application/json'");
    lines.push(`  -d '${JSON.stringify(body).replace(/'/g, "'\\''")}'`);
  }
  return lines.join(" \\\n");
}

export function payloadDialog(method, path, body) {
  const json = prettyJson(body);
  const curl = curlCommand(method, path, body);
  openDialog({
    title: "Muatan yang akan dikirim",
    body: h("div", { class: "stack" },
      h("p", {}, h("code", { text: `${method} ${path}` })),
      copyBlock(json),
      h("h3", { text: "Sebagai curl" }),
      copyBlock(curl),
      h("p", { class: "muted small", text: "UI mengirim muatan yang sama persis, dengan dryRun: true saat Periksa dan dryRun: false saat Simpan." })),
  });
}

export function copyBlock(text) {
  const btn = button(null, { iconName: "copy", title: "Salin", small: true });
  btn.addEventListener("click", async () => {
    const ok = await copyText(text);
    toast(ok ? "good" : "warning", ok ? "Disalin." : "Gagal menyalin.", 2000);
  });
  return h("div", { class: "copy-line" }, h("pre", { text }), btn);
}

export function gradeLink(gradeId, label) {
  return h("a", { href: `#/grade/${gradeId}`, class: "grade-pill" },
    h("span", { class: "nav-letter", text: GRADE_LETTERS[gradeId] }), label || null);
}

export function sourceBadge(source) {
  if (source === "config") return badge("info", "disimpan lewat API", "check");
  if (source === "env") return badge("warning", "dari environment", "info");
  return badge("neutral", "bawaan", "minus");
}

export function bandStrip(grades, currentId, problems) {
  const overlaps = new Set();
  const bands = grades.filter((g) => g.score).map((g) => ({ id: g.gradeId, letter: g.gradeLetter, ...g.score }));
  for (const a of bands) {
    for (const b of bands) {
      if (a.id !== b.id && a.min <= b.max && b.min <= a.max) overlaps.add(a.id);
    }
  }
  const track = h("div", { class: "bands-track" });
  const strip = h("div", { class: "bands", role: "img", "aria-label": bands.map((b) => `${b.letter} ${b.min}–${b.max}`).join(", ") }, track);
  for (const band of bands) {
    const lo = Math.max(0, Math.min(100, band.min));
    const hi = Math.max(lo, Math.min(100, band.max));
    const width = Math.max(hi - lo + 1, 1);
    const cls = ["band", band.id === currentId ? "is-current" : "", overlaps.has(band.id) ? "is-overlap" : ""].join(" ");
    const el = h("div", { class: cls }, width >= 7 ? `${band.letter} ${band.min}–${band.max}` : band.letter);
    el.style.left = `${lo}%`;
    el.style.width = `${Math.min(width, 100 - lo)}%`;
    attachTip(el, `Grade ${band.letter} · skor ${band.min}–${band.max} · ${band.severityLabel || ""}${overlaps.has(band.id) ? "\nTumpang tindih dengan grade lain" : ""}`);
    strip.append(el);
  }
  const axis = h("div", { class: "bands-axis" }, [0, 25, 50, 75, 100].map((v) => {
    const s = h("span", { text: String(v) });
    s.style.left = `${v}%`;
    return s;
  }));
  strip.append(axis);
  const out = h("div", {}, strip);
  if (problems && problems.length) out.append(notice("critical", null, problems));
  return out;
}

export function weightCell(percent, missing, available) {
  if (available === false) return h("span", { class: "muted small", text: "tidak tersedia" });
  const cell = h("div", { class: "wcell" });
  if (percent) {
    const bar = h("span");
    bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    cell.append(h("span", { class: "wval", text: `${fmtNum(percent)}%` }), h("div", { class: "wbar" }, bar));
  } else {
    cell.append(h("span", { class: "muted", text: "—" }));
  }
  if (missing) cell.append(h("span", { class: "wmiss", text: "dihitung kosong" }));
  return cell;
}

function zone(cls, from, to) {
  const el = h("div", { class: `meter-zone ${cls}` });
  el.style.left = `${from}%`;
  el.style.width = `${Math.max(0, to - from)}%`;
  return el;
}

export function meter(m, missingCount, best) {
  const track = h("div", { class: "meter-track" });
  const wrap = h("div", { class: "meter" }, track);
  const autoAllowed = m.autoScoreMin != null && (m.autoMissingMax == null || missingCount <= m.autoMissingMax);
  const reviewAllowed = (m.reviewMissingCount == null || missingCount === m.reviewMissingCount)
    && m.reviewScoreMin != null && m.reviewScoreMax != null && m.reviewScoreMin < m.reviewScoreMax;
  if (reviewAllowed) track.append(zone("meter-zone--review", clampPct(m.reviewScoreMin), clampPct(m.reviewScoreMax)));
  if (autoAllowed) track.append(zone("meter-zone--auto", clampPct(m.autoScoreMin), 100));
  const bar = h("div", { class: "meter-bar" });
  bar.style.width = `${clampPct(best)}%`;
  wrap.append(bar);
  if (autoAllowed) {
    const tick = h("div", { class: "meter-tick" });
    tick.style.left = `${clampPct(m.autoScoreMin)}%`;
    wrap.append(tick);
  }
  attachTip(wrap, () => [
    `${missingCount} elemen kosong: skor tertinggi yang mungkin ${fmtNum(best)}`,
    autoAllowed ? `AUTO bila skor ≥ ${fmtNum(m.autoScoreMin)}` : "AUTO tidak diizinkan untuk jumlah kosong ini",
    reviewAllowed ? `REVIEW bila ${fmtNum(m.reviewScoreMin)} ≤ skor < ${fmtNum(m.reviewScoreMax)}` : "REVIEW tidak diizinkan untuk jumlah kosong ini",
  ].join("\n"));
  return wrap;
}

function clampPct(value) {
  return Math.max(0, Math.min(100, Number(value) || 0));
}

export function meterAxis() {
  return h("div", { class: "meter-axis", "aria-hidden": "true" }, [0, 25, 50, 75, 100].map((v) => {
    const s = h("span", { text: String(v) });
    s.style.left = `${v}%`;
    return s;
  }));
}

export function meterLegend() {
  return h("div", { class: "meter-legend" },
    h("span", {}, h("i", { class: "swatch swatch--bar" }), "skor tertinggi yang mungkin (baris yang selebihnya cocok sempurna)"),
    h("span", {}, h("i", { class: "swatch swatch--auto" }), "wilayah AUTO"),
    h("span", {}, h("i", { class: "swatch swatch--review" }), "wilayah REVIEW"),
    h("span", {}, h("i", { class: "swatch swatch--tick" }), "autoScoreMin"));
}
