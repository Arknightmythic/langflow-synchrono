const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "on") {
      for (const [event, handler] of Object.entries(value)) el.addEventListener(event, handler);
    } else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style") Object.assign(el.style, value);
    else if (key === "value") el.value = value;
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

const ICONS = {
  check: ["M20 6 9 17l-5-5"],
  x: ["M18 6 6 18", "M6 6l12 12"],
  alert: ["M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
  info: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 16v-4", "M12 8h.01"],
  stop: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M15 9l-6 6", "M9 9l6 6"],
  half: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 2v20"],
  minus: ["M5 12h14"],
  plus: ["M12 5v14", "M5 12h14"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6"],
  refresh: ["M21 12a9 9 0 1 1-2.6-6.4L21 8", "M21 3v5h-5"],
  key: ["M15.5 7.5a4.5 4.5 0 1 1-4.3 5.8L3 21.5V18h3v-3h3l2.2-2.2", "M16.5 7.5h.01"],
  sun: ["M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z", "M12 1v2", "M12 21v2", "M4.2 4.2l1.4 1.4",
    "M18.4 18.4l1.4 1.4", "M1 12h2", "M21 12h2", "M4.2 19.8l1.4-1.4", "M18.4 5.6l1.4-1.4"],
  moon: ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"],
  back: ["M19 12H5", "M12 19l-7-7 7-7"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  clock: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 6v6l4 2"],
  undo: ["M3 7v6h6", "M21 17a9 9 0 0 0-15-6.7L3 13"],
};

export function icon(name, label) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  for (const d of ICONS[name] || []) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

const STATUS_ICON = { good: "check", warning: "alert", critical: "stop", serious: "alert", info: "info", neutral: "minus" };

export function badge(kind, text, iconName) {
  return h("span", { class: `badge badge--${kind}` }, icon(iconName || STATUS_ICON[kind] || "info"), text);
}

export const OUTCOME = {
  AUTO: { kind: "good", icon: "check", label: "AUTO" },
  REVIEW: { kind: "warning", icon: "half", label: "REVIEW" },
  UNMATCH: { kind: "neutral", icon: "minus", label: "UNMATCH" },
};

export function outcomeBadge(outcome) {
  const o = OUTCOME[outcome];
  return badge(o.kind, o.label, o.icon);
}

export function code(text) {
  return h("code", { text });
}

export function fmtNum(value) {
  if (value == null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return String(num).replace(".", ",");
}

export function round6(value) {
  return Math.round(value * 1e6) / 1e6;
}

export function toPercent(fraction) {
  return fraction == null ? null : round6(Number(fraction) * 100);
}

export function fromPercent(percent) {
  return Number((Number(percent) / 100).toPrecision(12));
}

export function fmtPct(fraction) {
  return fraction == null ? "—" : `${fmtNum(toPercent(fraction))}%`;
}

export function parseApiDate(text) {
  if (!text) return null;
  let s = String(text).trim().replace(" ", "T");
  s = s.replace(/(\.\d{3})\d+/, "$1").replace(/([+-]\d{2})$/, "$1:00");
  const date = new Date(s);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function fmtDate(text) {
  const date = parseApiDate(text);
  if (!date) return text || "—";
  return date.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

export function fmtJson(value) {
  return JSON.stringify(value);
}

export function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

export function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

let tipEl = null;

function tooltip() {
  if (!tipEl) {
    tipEl = h("div", { class: "tooltip", role: "tooltip", hidden: true });
    document.body.append(tipEl);
  }
  return tipEl;
}

export function attachTip(el, textFn) {
  const show = (event) => {
    const tip = tooltip();
    tip.textContent = typeof textFn === "function" ? textFn() : textFn;
    tip.hidden = false;
    const rect = el.getBoundingClientRect();
    const x = event && event.clientX != null ? event.clientX : rect.left + rect.width / 2;
    const top = rect.top - tip.offsetHeight - 8;
    tip.style.left = `${Math.max(8, Math.min(window.innerWidth - tip.offsetWidth - 8, x - tip.offsetWidth / 2))}px`;
    tip.style.top = `${top < 8 ? rect.bottom + 8 : top}px`;
  };
  const hide = () => { tooltip().hidden = true; };
  el.addEventListener("pointerenter", show);
  el.addEventListener("pointermove", show);
  el.addEventListener("pointerleave", hide);
  el.addEventListener("focus", () => show());
  el.addEventListener("blur", hide);
  if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
  return el;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = h("textarea", { class: "visually-hidden" });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

export function storage(kind) {
  try {
    const store = kind === "session" ? window.sessionStorage : window.localStorage;
    const probe = "__synchrono_probe__";
    store.setItem(probe, "1");
    store.removeItem(probe);
    return store;
  } catch {
    return null;
  }
}

export function readStore(kind, key, fallback = null) {
  try {
    const value = storage(kind)?.getItem(key);
    return value == null ? fallback : value;
  } catch {
    return fallback;
  }
}

export function writeStore(kind, key, value) {
  try {
    const store = storage(kind);
    if (!store) return;
    if (value == null || value === "") store.removeItem(key);
    else store.setItem(key, value);
  } catch {
    /* storage unavailable: the value simply is not remembered */
  }
}
