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
    const cur = parseFloat(input.value || "0");
    input.value = Math.max(0, cur + dir * step);
    window.cpRecalc();
  };

  // Recalc: POST current form line arrays to /offers/recalc, swap #sections.
  window.cpRecalc = function () {
    const s = sectionsEl();
    if (!s || !window.htmx) return;
    htmx.ajax("POST", "/offers/recalc", { source: formEl(), target: "#sections", swap: "innerHTML" });
  };
  let _t = null;
  window.cpDebouncedRecalc = function () { clearTimeout(_t); _t = setTimeout(window.cpRecalc, 400); };

  // Add a blank line to a group (builds the <select> from comps-data).
  window.cpAddLine = function (groupId) {
    const group = document.querySelector('.cp-group[data-group-id="' + groupId + '"] .cp-group__body');
    if (!group) return;
    const addBtn = group.querySelector("button");
    const comps = (compsData()[groupId] || []);
    const line = document.createElement("div");
    line.className = "cp-line";
    line.dataset.group = groupId;
    let opts = '<option value="">—</option><option value="__new__">[Új összetevő…]</option>';
    comps.forEach(c => { opts += '<option value="' + c.id + '" data-unit="' + c.unit + '">' + escapeHtml(c.name) + "</option>"; });
    line.innerHTML =
      '<select name="component_id" required onchange="cpLineChanged(this)">' + opts + "</select>" +
      '<input class="cp-amount" type="number" step="0.001" min="0" name="amount" value="1" onchange="cpRecalc()" onkeyup="cpDebouncedRecalc()">' +
      '<span class="cp-unit"></span>' +
      '<span class="cp-stepper"><button type="button" onclick="cpStep(this,1)">▲</button><button type="button" onclick="cpStep(this,-1)">▼</button></span>' +
      '<span class="cp-line-cost"></span>' +
      '<button type="button" class="outline secondary" onclick="this.closest(\'.cp-line\').remove(); cpRecalc();"><i data-lucide="trash-2"></i></button>';
    group.insertBefore(line, addBtn);
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
