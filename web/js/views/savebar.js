import { badge, clear, clone, h, same } from "../dom.js";
import { busy, button, confirmDialog, payloadDialog, resultPanel, textInput, toast } from "../ui.js";

// Shared dry-run / save flow for the grade and global editors.
export function createSaveFlow({ ctx, path, send, resultKey, savedText, onUpdate }) {
  const resultSlot = h("div");
  const barSlot = h("div");
  const editorName = textInput({ value: ctx.editor(), label: "Nama penyunting", placeholder: "nama Anda",
    onChange: (v) => ctx.setEditor(v) });
  let panel = null;
  let dry = null;
  let dryPatch = null;
  let currentPatch = {};
  let bar = null;
  let countEl;
  let invalidEl;
  let checkBtn;
  let saveBtn;

  const body = (patch, dryRun) => ({ ...patch, updatedBy: ctx.editor() || null, dryRun });

  function showResult(result, saved) {
    clear(resultSlot);
    panel = resultPanel(result, {
      saved,
      onDismiss: () => {
        clear(resultSlot);
        panel = null;
        dry = null;
        dryPatch = null;
        onUpdate();
      },
    });
    resultSlot.append(panel);
    resultSlot.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function check() {
    const patch = clone(currentPatch);
    try {
      const result = await send(body(patch, true));
      dry = result;
      dryPatch = patch;
      showResult(result, false);
      onUpdate();
    } catch (error) {
      toast("critical", error.message, 8000);
    }
  }

  async function save() {
    const patch = clone(currentPatch);
    if (!ctx.editor()) {
      toast("warning", "Isi nama penyunting dulu supaya riwayat perubahan jelas.");
      editorName.focus();
      return;
    }
    try {
      const loaded = ctx.state.rules.configVersion;
      const fresh = await ctx.api.rules();
      if (fresh.configVersion !== loaded) {
        const go = await confirmDialog("Konfigurasi sudah berubah",
          `Versi yang berlaku sekarang ${fresh.configVersion}, sedangkan halaman ini dimuat dari ${loaded}. `
          + "Yang dikirim hanya field yang Anda ubah, tetapi bila orang lain mengubah field yang sama, "
          + "nilainya akan tertimpa. Tetap simpan?", "Tetap simpan", "Batal");
        if (!go) return;
      }
      const result = await send(body(patch, false));
      if (result.httpStatus === 409 || result.problems?.length) {
        showResult(result, true);
        return;
      }
      ctx.state.results[resultKey] = result;
      ctx.setDirty(false);
      toast("good", `${savedText} · versi ${result.configVersion || "tidak berubah"}`);
      await ctx.reload({ quiet: true });
      ctx.rerender();
    } catch (error) {
      toast("critical", error.message, 8000);
    }
  }

  function buildBar() {
    countEl = h("span", { class: "count" });
    invalidEl = badge("critical", "ada isian yang tidak sah");
    checkBtn = button("Periksa (dry run)");
    saveBtn = button("Simpan", { kind: "primary" });
    checkBtn.addEventListener("click", () => busy(checkBtn, check));
    saveBtn.addEventListener("click", () => busy(saveBtn, save));
    const preview = button("Lihat muatan", { iconName: "info", onClick: () => payloadDialog("PATCH", path, body(currentPatch, true)) });
    const discard = button("Batalkan", {
      kind: "ghost",
      onClick: async () => {
        if (await confirmDialog("Batalkan perubahan?", "Draf dikembalikan ke konfigurasi yang berlaku.")) {
          ctx.setDirty(false);
          ctx.rerender();
        }
      },
    });
    bar = h("div", { class: "savebar" }, h("div", { class: "savebar-inner" },
      countEl, invalidEl, h("span", { class: "grow" }),
      h("label", {}, "Penyunting", editorName),
      preview, discard, checkBtn, saveBtn));
  }

  // Called after every draft change; the bar is updated in place, not rebuilt.
  function update(patch, count, invalid) {
    currentPatch = patch;
    if (panel) panel.setStale(dry != null && !same(dryPatch, patch));
    if (!count) {
      if (bar) bar.remove();
      return;
    }
    if (!bar) buildBar();
    if (!bar.isConnected) barSlot.append(bar);
    countEl.textContent = `${count} perubahan`;
    invalidEl.hidden = !invalid;
    for (const btn of [checkBtn, saveBtn]) {
      if (!btn.classList.contains("is-busy")) btn.disabled = invalid;
    }
  }

  const last = ctx.state.results[resultKey];
  if (last) {
    delete ctx.state.results[resultKey];
    showResult(last, true);
  }

  return {
    resultSlot,
    barSlot,
    update,
    dryFor: (patch) => (dry && same(dryPatch, patch) ? dry : null),
  };
}
