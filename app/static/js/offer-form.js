/* Offer-form client logic: unit-aware stepper, add-line, live recalc, inline-new. */
(function () {
  "use strict";

  function sectionsEl() { return document.getElementById("sections"); }
  function formEl() { return document.getElementById("offer-form"); }
  function compsData() {
    const el = document.getElementById("comps-data");
    try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; }
  }

  // Unit-aware step: db -> 1, g/ml -> configurable (window.cpUnitStep).
  window.cpStep = function (btn, dir) {
    const line = btn.closest(".cp-line");
    const input = line.querySelector("input[name=amount]");
    const unit = (line.querySelector(".cp-unit")?.textContent || "").trim();
    const step = (window.cpMassUnits || []).includes(unit) ? (window.cpUnitStep || 10) : 1;
    // Tolerate a decimal comma in the current value before stepping.
    const cur = parseFloat(String(input.value || "0").replace(",", ".")) || 0;
    input.value = Math.max(0, cur + dir * step);
    window.cpRecalc();
  };

  // Recalc: POST current form line arrays to /offers/recalc, swap #sections.
  // Guard against overlapping recalcs: if one is in flight, coalesce into a
  // single follow-up run after it settles (otherwise concurrent #sections swaps
  // can duplicate lines).
  let _recalcBusy = false;
  let _recalcQueued = false;
  window.cpRecalc = function () {
    const s = sectionsEl();
    const f = formEl();
    if (!s || !f || !window.htmx) return;
    if (_recalcBusy) { _recalcQueued = true; return; }
    // Serialize the form ourselves — htmx.ajax() does not gather form fields from
    // `source` reliably for programmatic POSTs. Build a values object incl. the
    // parallel component_id[]/amount[] arrays.
    const values = {};
    new FormData(f).forEach((val, key) => {
      if (key in values) {
        if (!Array.isArray(values[key])) values[key] = [values[key]];
        values[key].push(val);
      } else {
        values[key] = val;
      }
    });
    _recalcBusy = true;
    htmx.ajax("POST", "/offers/recalc", { target: "#sections", swap: "innerHTML", values: values })
      .finally(() => {
        _recalcBusy = false;
        if (_recalcQueued) { _recalcQueued = false; window.cpRecalc(); }
      });
  };
  // Debounce keystrokes so we recalc only after the chef pauses typing, not on
  // every character (fewer swaps, and less chance of a swap landing mid-word on
  // a slow box).
  const RECALC_DELAY_MS = 700;
  let _t = null;
  window.cpDebouncedRecalc = function () {
    clearTimeout(_t);
    _t = setTimeout(window.cpRecalc, RECALC_DELAY_MS);
  };

  // --- Keep the caret in the amount field across the recalc swap -------------
  // The recalc replaces #sections wholesale, which would otherwise blow away
  // focus + caret in the input the chef is typing in. Capture them right before
  // the swap and restore them right after, keyed by the input's position among
  // the amount fields (stable during a plain amount edit). If the chef kept
  // typing while the request was in flight, keep THEIR newer value and trigger
  // a follow-up recalc, rather than snapping back to the submitted value.
  let _caret = null;
  function amountInputs() {
    return Array.prototype.slice.call(document.querySelectorAll("#sections .cp-amount"));
  }
  document.body.addEventListener("htmx:beforeSwap", function (e) {
    if (!e.detail || !e.detail.target || e.detail.target.id !== "sections") return;
    const a = document.activeElement;
    if (!a || !a.classList || !a.classList.contains("cp-amount")) { _caret = null; return; }
    let sel = null;
    try { sel = [a.selectionStart, a.selectionEnd]; } catch (_) { sel = null; }  // number inputs throw
    _caret = { index: amountInputs().indexOf(a), value: a.value, sel: sel };
  });
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (!e.detail || !e.detail.target || e.detail.target.id !== "sections") return;
    const c = _caret;
    _caret = null;
    if (!c || c.index < 0) return;
    const el = amountInputs()[c.index];
    if (!el) return;
    // Compare NUMERICALLY: the server trims trailing zeros (2.50 -> 2.5), so a
    // pure string compare would think the value changed on every recalc and
    // loop forever restoring the user's value. Only restore when the number the
    // user has actually differs (they typed more during a slow request).
    const un = parseFloat(String(c.value).replace(",", "."));
    const sn = parseFloat(String(el.value).replace(",", "."));
    const sameNumber = Number.isFinite(un) && Number.isFinite(sn) && un === sn;
    if (!sameNumber && el.value !== c.value) {
      el.value = c.value;
      window.cpDebouncedRecalc();
    }
    el.focus();
    if (c.sel) { try { el.setSelectionRange(c.sel[0], c.sel[1]); } catch (_) {} }
  });

  // Add a blank line to a group's .cp-lines container (builds <select> from comps-data).
  window.cpAddLine = function (groupId) {
    const lines = document.querySelector('.cp-group[data-group-id="' + groupId + '"] .cp-lines');
    if (!lines) return;
    const comps = (compsData()[groupId] || []);
    const line = document.createElement("div");
    line.className = "cp-line";
    line.dataset.group = groupId;
    let opts = '<option value="">—</option><option value="__new__">[Új összetevő…]</option>';
    comps.forEach(c => { opts += '<option value="' + c.id + '" data-unit="' + c.unit + '">' + escapeHtml(c.name) + "</option>"; });
    line.innerHTML =
      '<select name="component_id" required onchange="cpLineChanged(this)">' + opts + "</select>" +
      '<input class="cp-amount" type="text" inputmode="decimal" name="amount" value="1" onchange="cpRecalc()" onkeyup="cpDebouncedRecalc()">' +
      '<span class="cp-unit"></span>' +
      '<span class="cp-stepper"><button type="button" onclick="cpStep(this,1)">▲</button><button type="button" onclick="cpStep(this,-1)">▼</button></span>' +
      '<span class="cp-line-cost"></span>' +
      '<button type="button" class="outline secondary" onclick="this.closest(\'.cp-line\').remove(); cpRecalc();"><i data-lucide="trash-2"></i></button>';
    lines.appendChild(line);
    window.lucide && lucide.createIcons();
  };

  // Component <select> changed: update unit label; handle the [Új…] sentinel.
  window.cpLineChanged = function (sel) {
    if (sel.value === "__new__") {
      const groupId = sel.closest(".cp-group")?.dataset.groupId;
      sel.value = "";
      window.cpNewComponent(groupId, sel);
      return;
    }
    const opt = sel.selectedOptions[0];
    const unit = opt ? (opt.dataset.unit || "") : "";
    const line = sel.closest(".cp-line");
    const unitEl = line.querySelector(".cp-unit");
    if (unitEl) unitEl.textContent = unit;
    window.cpRecalc();
  };

  // Inline new-component: open the quick-create dialog; on save, add option + select it.
  window.cpNewComponent = function (groupId, sel) {
    const dlg = document.getElementById("cp-modal");
    htmx.ajax("GET", "/components/quick-new?group_id=" + (groupId || ""), { target: "#cp-modal", swap: "innerHTML" })
      .then(() => { dlg.showModal(); dlg._targetSelect = sel; });
  };

  // Called by the quick-create dialog on success (see components/_quick_new.html).
  window.cpComponentCreated = function (id, name, unit, groupId) {
    document.getElementById("cp-modal").close();
    // Add to comps-data so future add-lines include it.
    const data = compsData();
    (data[groupId] = data[groupId] || []).push({ id: id, name: name, unit: unit });
    const el = document.getElementById("comps-data");
    if (el) el.textContent = JSON.stringify(data);
    // Add option to every select in that group and select it on the target line.
    document.querySelectorAll('.cp-group[data-group-id="' + groupId + '"] select[name=component_id]').forEach(s => {
      const o = document.createElement("option");
      o.value = id; o.textContent = name; o.dataset.unit = unit;
      s.appendChild(o);
    });
    // Select on the most recently touched line (last empty select in the group).
    const target = document.querySelector('.cp-group[data-group-id="' + groupId + '"] select[name=component_id][value=""], .cp-group[data-group-id="' + groupId + '"] select[name=component_id]');
    // Prefer an empty one:
    const selects = [...document.querySelectorAll('.cp-group[data-group-id="' + groupId + '"] select[name=component_id]')];
    const empty = selects.find(s => !s.value) || selects[selects.length - 1];
    if (empty) { empty.value = id; cpLineChanged(empty); }
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
})();
