import { ApiError } from "../api.js";
import { append, badge, clear, fmtDate, h } from "../dom.js";
import { GRADE_IDS, GRADE_LETTERS } from "../fields.js";
import { button, card, changesTable, notice, openDialog, pageHead, selectInput, valueNode } from "../ui.js";

const SECTION_LABEL = { criteria: "Kriteria", bands: "Pita skor", matching: "Matching", global: "Global" };

function flatten(snapshot) {
  const out = new Map();
  for (const [section, value] of Object.entries(snapshot || {})) {
    if (section === "global") {
      for (const [key, v] of Object.entries(value || {})) out.set(`global › ${key}`, v);
      continue;
    }
    for (const [gradeId, fields] of Object.entries(value || {})) {
      for (const [key, v] of Object.entries(fields || {})) {
        out.set(`Grade ${GRADE_LETTERS[gradeId] || gradeId} › ${SECTION_LABEL[section] || section} › ${key}`, v);
      }
    }
  }
  return out;
}

function shortValue(value) {
  if (typeof value === "string" && value.length > 160) {
    return h("details", {}, h("summary", { text: `${value.slice(0, 60).replace(/\s+/g, " ")}…` }), h("pre", { text: value }));
  }
  return valueNode(value);
}

async function openVersion(ctx, version) {
  const body = h("div", { class: "stack" }, h("p", { class: "muted", text: "Memuat…" }));
  openDialog({ title: `Versi konfigurasi ${version}`, body, wide: true });
  let data;
  try {
    data = await ctx.api.version(version);
  } catch (error) {
    clear(body).append(notice("critical", null, error.message));
    return;
  }
  const flat = flatten(data.config);
  const current = ctx.state.rules.configVersion;
  const diffSlot = h("div");
  append(clear(body), [
    h("dl", { class: "kv" }, h("dt", { text: "configVersion" }), h("dd", {}, h("code", { text: data.configVersion })),
      h("dt", { text: "Pertama dipakai" }), h("dd", { text: fmtDate(data.firstUsedAt) }),
      h("dt", { text: "Berlaku sekarang" }), h("dd", {}, h("code", { text: current }),
        version === current ? badge("good", "versi ini") : null)),
    version !== current ? card("Beda dengan versi yang berlaku", {}, diffSlot) : null,
    h("details", {}, h("summary", { text: `Seluruh isi (${flat.size} nilai)` }),
      h("div", { class: "table-wrap" }, h("table", { class: "table-tight" },
        h("thead", {}, h("tr", {}, h("th", { text: "Nilai" }), h("th", { text: "Isi" }))),
        h("tbody", {}, [...flat].map(([key, value]) => h("tr", {}, h("td", { class: "nowrap", text: key }), h("td", {}, shortValue(value))))))))]);
  if (version === current) return;
  diffSlot.append(h("p", { class: "muted", text: "Membandingkan…" }));
  try {
    const now = flatten((await ctx.api.version(current)).config);
    const keys = [...new Set([...flat.keys(), ...now.keys()])];
    const rows = keys.filter((k) => JSON.stringify(flat.get(k)) !== JSON.stringify(now.get(k)));
    clear(diffSlot).append(rows.length
      ? h("div", { class: "table-wrap" }, h("table", { class: "table-tight" },
        h("thead", {}, h("tr", {}, h("th", { text: "Nilai" }), h("th", { text: `Versi ${version}` }), h("th", { text: `Berlaku (${current})` }))),
        h("tbody", {}, rows.map((k) => h("tr", {}, h("td", { class: "nowrap", text: k }), h("td", {}, shortValue(flat.get(k))), h("td", {}, shortValue(now.get(k))))))))
      : h("p", { text: "Isinya sama." }));
  } catch (error) {
    clear(diffSlot).append(notice("info", null, error instanceof ApiError && error.status === 404
      ? "Versi yang berlaku belum tercatat (tercatat saat ada job atau perubahan berikutnya)."
      : error.message));
  }
}

export async function renderHistory(ctx, root) {
  let filter = "";
  let limit = 50;
  const list = h("div");
  const more = button("Muat lebih banyak", { onClick: () => { limit = Math.min(500, limit + 50); load(); } });
  const versionInput = h("input", { type: "text", placeholder: "mis. c5a7010b87af", "aria-label": "configVersion", maxlength: "12" });
  versionInput.style.width = "160px";

  async function load() {
    clear(list).append(h("div", { class: "loading", text: "Memuat riwayat…" }));
    let items;
    try {
      const gradeId = filter && filter !== "global" ? Number(filter) : null;
      items = (await ctx.api.history({ gradeId, limit })).items || [];
    } catch (error) {
      clear(list).append(notice("critical", null, error.message));
      return;
    }
    const total = items.length;
    if (filter === "global") items = items.filter((item) => item.scope === "global");
    clear(list);
    if (!items.length) list.append(h("div", { class: "empty", text: "Belum ada perubahan lewat API." }));
    for (const item of items) {
      const scope = item.scope === "global" ? badge("info", "Global", "info")
        : h("a", { href: `#/grade/${item.gradeId}`, class: "grade-pill" }, h("span", { class: "nav-letter", text: item.gradeLetter }), `Grade ${item.gradeLetter}`);
      list.append(h("article", { class: "history-item" },
        h("div", { class: "history-head" },
          h("strong", { text: fmtDate(item.at) }),
          h("span", { class: "muted", text: `oleh ${item.by || "(tanpa nama)"}` }),
          scope,
          h("span", { class: "grow" }),
          item.configVersion ? button(`versi ${item.configVersion}`, { small: true, title: "Lihat isi versi", onClick: () => openVersion(ctx, item.configVersion) }) : null),
        h("div", { style: { padding: "4px 12px 10px" } }, changesTable(item.changes))));
    }
    more.hidden = total < limit || limit >= 500;
    list.append(h("div", { class: "add-row" }, more));
  }

  const gradeSelect = selectInput({
    options: [["", "Semua"], ["global", "Global"], ...GRADE_IDS.map((id) => [String(id), `Grade ${GRADE_LETTERS[id]}`])],
    value: "", label: "Tampilkan", onChange: (v) => { filter = v; limit = 50; load(); },
  });
  root.append(
    pageHead("Riwayat & versi", "Setiap perubahan lewat API: siapa, kapan, dari berapa ke berapa, dan versi konfigurasi sesudahnya. Setiap job grading dan matching mencatat configVersion yang dipakainya."),
    h("div", { class: "filters" },
      h("label", {}, "Tampilkan", gradeSelect),
      button("Muat ulang", { iconName: "refresh", onClick: () => load() }),
      h("span", { class: "grow" }),
      h("label", {}, "Buka versi", versionInput),
      button("Buka", { onClick: () => {
        const v = versionInput.value.trim().toLowerCase();
        if (/^[0-9a-f]{12}$/.test(v)) openVersion(ctx, v);
        else versionInput.classList.add("is-invalid");
      } })),
    list);
  versionInput.addEventListener("input", () => versionInput.classList.remove("is-invalid"));
  await load();
  return null;
}
