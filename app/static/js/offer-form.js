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

  // --- live "Ft / szelet" note ------------------------------------------------
  // The note is server-rendered on load/save (so it works with JS off and is
  // correct on first paint); this keeps it in step while the chef types. Mirrors
  // templating.format_huf EXACTLY: whole forint, U+00A0 thousands separator,
  // U+00A0 before "Ft" — otherwise the live text would differ from the saved one.
  // The label pattern comes from the element's data-tpl (filled from i18n.py), so
  // no Hungarian string is duplicated here. Uses FINAL price, never Fizetve.
  const NBSP = "\u00a0";  // non-breaking space, as format_huf emits

  // Python's round() is HALF-TO-EVEN ("banker's rounding"), but JS Math.round is
  // half-UP: round(12500.5) is 12500 in Python and 12501 in JS. That case is
  // reachable (e.g. 25001 Ft over 2 slices), and the live note must never
  // contradict what the server renders after saving \u2014 so mirror Python here.
  function roundHalfToEven(value) {
    const low = Math.floor(value);
    const frac = value - low;
    if (frac > 0.5) return low + 1;
    if (frac < 0.5) return low;
    return low % 2 === 0 ? low : low + 1;  // exactly .5 \u2192 pick the even neighbour
  }

  function formatHuf(value) {
    const grouped = String(roundHalfToEven(value)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    return grouped + NBSP + "Ft";
  }

  (function livePerPortion() {
    const out = document.getElementById("per-portion");
    const price = document.getElementById("final-price");
    const portions = document.getElementById("portions");
    if (!out || !price || !portions) return;
    const tpl = out.dataset.tpl || "{price}";
    // Exposed so the profit⇄price binding can refresh it after it writes the
    // price programmatically (assigning .value fires no `input` event).
    window.cpUpdatePerPortion = function () {
      // Tolerate a decimal comma, as the server does (decimal_hu).
      const p = parseFloat(String(price.value).replace(",", "."));
      const n = parseInt(portions.value, 10);
      const showable = Number.isFinite(p) && Number.isFinite(n) && n > 0;
      out.textContent = showable ? tpl.replace("{price}", formatHuf(p / n)) : "";
    };
    price.addEventListener("input", window.cpUpdatePerPortion);
    portions.addEventListener("input", window.cpUpdatePerPortion);
  })();

  // --- profit % ⇄ final price ------------------------------------------------
  // Bound pair over the cost base (calculated price = materials + Munkadíj +
  // Rezsi), which every recalc republishes as window.cpCostBase:
  //     price = cost * (1 + pct/100)        pct = (price/cost - 1) * 100
  // Editing either recomputes the other. When the COST BASE moves (a line edit),
  // we follow whichever field the chef touched last, so her most recent intent is
  // never overwritten: last-edited "pct" (also the initial state, i.e. the default
  // 10%) re-derives the price; last-edited "price" re-derives the pct instead.
  // A negative pct is legal — pricing below cost is allowed, just visible.
  (function pricingBinding() {
    const pctEl = document.getElementById("profit-pct");
    const priceEl = document.getElementById("final-price");
    if (!pctEl || !priceEl) return;
    let anchor = "pct";

    const num = (v) => parseFloat(String(v).replace(",", "."));
    const cost = () => Number(window.cpCostBase);

    function priceFromPct() {
      const c = cost();
      const p = num(pctEl.value);
      if (!Number.isFinite(c) || c <= 0 || !Number.isFinite(p)) return;
      priceEl.value = roundHalfToEven(c * (1 + p / 100));
      window.cpUpdatePerPortion && window.cpUpdatePerPortion();
    }

    function pctFromPrice() {
      const c = cost();
      const v = num(priceEl.value);
      if (!Number.isFinite(c) || c <= 0 || !Number.isFinite(v)) return;
      // One decimal is plenty and keeps the field tidy.
      pctEl.value = Math.round((v / c - 1) * 1000) / 10;
    }

    pctEl.addEventListener("input", function () {
      anchor = "pct";
      priceFromPct();
    });
    priceEl.addEventListener("input", function () {
      anchor = "price";
      pctFromPrice();
    });
    // EXTRA group (candle, sparkler asked for at handover): the customer is buying
    // something more, so the FINAL PRICE must go up — the chef should not be paid
    // less for saying yes. Editing such a line therefore re-anchors on the pct,
    // which keeps the margin and lets the price follow the added cost, whatever
    // was edited last. Capture phase so it lands before the recalc is queued;
    // `click` covers the stepper and the delete button, whose programmatic
    // .value writes fire no `input` event.
    const extraEdit = (e) => {
      if (e.target.closest && e.target.closest('[data-pricing-extra]')) anchor = "pct";
    };
    document.body.addEventListener("input", extraEdit, true);
    document.body.addEventListener("change", extraEdit, true);
    document.body.addEventListener(
      "click",
      (e) => {
        // Buttons only — a stray click on the section must not re-anchor.
        if (e.target.closest && e.target.closest('[data-pricing-extra] button')) anchor = "pct";
      },
      true,
    );

    // Called by the recalc fragment once it has published a new cost base.
    window.cpResyncPricing = function () {
      if (anchor === "price") pctFromPrice();
      else priceFromPct();
    };
  })();
})();
