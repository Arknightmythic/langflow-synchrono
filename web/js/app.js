import { api, ApiError, currentTarget, keyRemembered, saveKey, setTarget, storedKey } from "./api.js";
import { clear, h, icon, readStore, writeStore } from "./dom.js";
import { GRADE_IDS, GRADE_LETTERS } from "./fields.js";
import { button, card, confirmDialog, notice, openDialog, toast } from "./ui.js";
import { renderOverview } from "./views/overview.js";
import { renderGrade } from "./views/grade.js";
import { renderGlobal } from "./views/global.js";
import { renderHistory } from "./views/history.js";
import { renderSimulate } from "./views/simulate.js";
import { renderGuide } from "./views/guide.js";

const state = {
  uiConfig: null,
  rules: null,
  error: null,
  loadedAt: null,
  dirty: false,
  lastHash: window.location.hash,
  results: {},
};

const main = document.getElementById("main");
const nav = document.getElementById("nav");
const statusEl = document.getElementById("status");
let cleanup = null;

const ROUTES = {
  ringkasan: renderOverview,
  grade: renderGrade,
  global: renderGlobal,
  riwayat: renderHistory,
  simulasi: renderSimulate,
  panduan: renderGuide,
};

const ctx = {
  state,
  api,
  reload: (opts) => loadRules(opts),
  navigate: (hash) => { window.location.hash = hash; },
  setDirty: (flag) => { state.dirty = Boolean(flag); },
  editor: () => readStore("local", "synchrono-ui:editor", ""),
  setEditor: (name) => writeStore("local", "synchrono-ui:editor", name.trim()),
  rerender: () => route(),
};

function setStatus(kind, text) {
  statusEl.className = `status status--${kind}`;
  clear(statusEl);
  statusEl.append(h("span", { class: "dot" }), h("span", { class: "text", text }));
  statusEl.title = text;
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme) root.dataset.theme = theme;
  else delete root.dataset.theme;
  const dark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const btn = document.getElementById("theme-button");
  clear(btn);
  btn.append(icon(dark ? "sun" : "moon"));
  btn.title = dark ? "Tema terang" : "Tema gelap";
  btn.setAttribute("aria-label", btn.title);
}

function setupTheme() {
  applyTheme(readStore("local", "synchrono-ui:theme"));
  document.getElementById("theme-button").addEventListener("click", () => {
    const current = document.documentElement.dataset.theme
      || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    writeStore("local", "synchrono-ui:theme", next);
    applyTheme(next);
  });
}

function renderNav() {
  clear(nav);
  const hash = window.location.hash || "#/ringkasan";
  const link = (href, label, extra) => {
    const current = hash === href || (href !== "#/ringkasan" && hash.startsWith(`${href}/`));
    return h("li", {}, h("a", { href, "aria-current": current ? "page" : null }, label, extra || null));
  };
  nav.append(link("#/ringkasan", "Ringkasan"));
  nav.append(h("li", { class: "nav-group", text: "Grade" }));
  for (const id of GRADE_IDS) {
    const grade = state.rules?.grades.find((g) => g.gradeId === id);
    const warnings = grade?.matching?.analysis?.warnings?.length || 0;
    const meta = h("span", { class: "nav-meta" });
    if (warnings) meta.append(icon("alert", `${warnings} peringatan`), String(warnings));
    nav.append(link(`#/grade/${id}`, [h("span", { class: "nav-letter", text: GRADE_LETTERS[id] }),
      grade?.score?.severityLabel || `Grade ${GRADE_LETTERS[id]}`], meta));
  }
  nav.append(h("li", { class: "nav-group", text: "Lainnya" }));
  nav.append(link("#/global", "Global"));
  nav.append(link("#/riwayat", "Riwayat & versi"));
  nav.append(link("#/simulasi", "Simulasi"));
  nav.append(link("#/panduan", "Panduan API"));
}

async function loadRules({ quiet } = {}) {
  if (!quiet) setStatus("busy", "Memuat konfigurasi…");
  try {
    state.rules = await api.rules();
    state.error = null;
    state.loadedAt = new Date();
    setStatus("good", `Terhubung · versi ${state.rules.configVersion}`);
  } catch (error) {
    state.error = error;
    if (!quiet) state.rules = null;
    setStatus("critical", error instanceof ApiError && (error.status === 401 || error.status === 403)
      ? "API key diperlukan" : "Tidak terhubung");
    if (quiet) throw error;
  }
  renderNav();
  return state.rules;
}

function errorPage() {
  const error = state.error;
  const target = currentTarget();
  const needsKey = error instanceof ApiError && (error.status === 401 || error.status === 403);
  return h("div", { class: "page" },
    card("Konfigurasi tidak bisa dimuat", { sub: target ? `${target.name} · ${target.url}` : "" },
      notice("critical", null, error?.message || "Kesalahan tidak dikenal."),
      h("div", { class: "chips", style: { marginTop: "12px" } },
        needsKey ? button("Isi API key", { kind: "primary", iconName: "key", onClick: keyDialog }) : null,
        button("Coba lagi", { iconName: "refresh", onClick: async () => { await loadRules(); route(); } }))));
}

async function route() {
  if (cleanup) {
    try { cleanup(); } catch { /* ignore */ }
    cleanup = null;
  }
  renderNav();
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [name, param] = hash.split("/");
  const view = ROUTES[name] || renderOverview;
  clear(main);
  if (!state.rules && view !== renderGuide) {
    main.append(state.error ? errorPage() : h("div", { class: "loading", text: "Memuat…" }));
    return;
  }
  const page = h("div", { class: "page" });
  main.append(page);
  try {
    cleanup = (await view(ctx, page, param)) || null;
  } catch (error) {
    console.error(error);
    page.append(notice("critical", "Halaman gagal ditampilkan", String(error?.message || error)));
  }
}

function keyDialog() {
  const target = currentTarget();
  const input = h("input", { type: "password", id: "api-key-input", value: storedKey(), autocomplete: "off" });
  input.style.width = "100%";
  const remember = h("input", { type: "checkbox", checked: keyRemembered() });
  const save = button("Simpan", {
    kind: "primary",
    onClick: async () => {
      saveKey(input.value.trim(), remember.checked);
      handle.close();
      await loadRules();
      route();
    },
  });
  const forget = button("Hapus", {
    onClick: async () => {
      saveKey("", false);
      handle.close();
      await loadRules();
      route();
    },
  });
  const handle = openDialog({
    title: `API key · ${target?.name || ""}`,
    body: h("div", { class: "stack" },
      target?.keyInjected
        ? notice("info", "Server UI sudah mengisi API key untuk target ini.",
          "Kosongkan untuk memakai kunci dari server; isi bila ingin memakai kunci lain.")
        : h("p", { text: "Dikirim sebagai header x-api-key ke service, lewat proxy UI ini." }),
      h("label", { for: "api-key-input", class: "label", text: "API key" }), input,
      h("label", { class: "check" }, remember, "Ingat di browser ini (localStorage). Tanpa centang: hanya selama tab terbuka.")),
    actions: [forget, save],
  });
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") save.click(); });
  input.focus();
}

async function selectTarget(id) {
  const target = state.uiConfig.targets.find((t) => t.id === id) || state.uiConfig.targets[0];
  setTarget(target);
  writeStore("local", "synchrono-ui:target", target.id);
  document.getElementById("target-select").value = target.id;
  state.rules = null;
  state.results = {};
  await loadRules();
  await route();
}

async function boot() {
  setupTheme();
  document.getElementById("reload-button").append(icon("refresh"));
  try {
    const response = await fetch("ui-config.json", { cache: "no-store" });
    state.uiConfig = response.ok ? await response.json() : null;
  } catch {
    state.uiConfig = null;
  }
  if (!state.uiConfig?.targets?.length) {
    state.uiConfig = { auth: false, targets: [{ id: "0", name: "service", url: window.location.origin, base: "", keyInjected: false }] };
  }
  const select = document.getElementById("target-select");
  for (const t of state.uiConfig.targets) select.append(h("option", { value: t.id, text: `${t.name}` }));
  select.hidden = state.uiConfig.targets.length < 2;
  select.addEventListener("change", async () => {
    if (state.dirty && !(await confirmDialog("Ganti service?", "Perubahan yang belum disimpan akan hilang."))) {
      select.value = currentTarget().id;
      return;
    }
    state.dirty = false;
    selectTarget(select.value);
  });
  document.getElementById("key-button").addEventListener("click", keyDialog);
  document.getElementById("reload-button").addEventListener("click", async () => {
    if (state.dirty && !(await confirmDialog("Muat ulang?", "Perubahan yang belum disimpan akan hilang."))) return;
    state.dirty = false;
    await loadRules();
    route();
    if (state.rules) toast("good", "Konfigurasi dimuat ulang.", 2500);
  });

  window.addEventListener("hashchange", () => {
    if (state.dirty && window.location.hash !== state.lastHash) {
      if (!window.confirm("Ada perubahan yang belum disimpan. Tinggalkan halaman ini?")) {
        window.history.replaceState(null, "", state.lastHash || "#/ringkasan");
        renderNav();
        return;
      }
    }
    state.dirty = false;
    state.lastHash = window.location.hash;
    route();
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  await selectTarget(readStore("local", "synchrono-ui:target") || state.uiConfig.targets[0].id);
}

boot();
