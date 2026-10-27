"use strict";

const $ = (s) => document.querySelector(s);
const api = () => window.pywebview.api;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const money = (c) => (c == null ? "—" : (c < 0 ? "−" : "") + "$" + (Math.abs(c) / 100).toFixed(2));
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const errMsg = (e) => (e && e.message) || String(e);
const STATUS = { successful: "Completed", reverted: "Reverted", trade_protected: "Trade protected" };
const TABS = ["inventory", "offers", "history"];

const state = {
  game: "a8db",
  tab: "inventory",
  loaded: new Set(),
  inventory: { rows: [], cursor: "", sel: new Set(), open: new Set(), req: 0 },
  offers: { rows: [], cursor: "", sel: new Set(), open: new Set(), req: 0 },
  history: { req: 0, kinds: [] },
};

// ---------- helpers ----------

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), error ? 10000 : 6000);
}

// count > 1: a group row (title × count); otherwise one item with its details underneath.
function itemCell(r, count = 1, expander = "") {
  const qty = count > 1 ? ` <span class="qty">×${count}</span>` : "";
  const sub = count === 1 && r.details && r.details.length ? `<span class="sub">${esc(r.details.join(" · "))}</span>` : "";
  const img = r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy">` : "";
  return `<div class="item">${expander}${img}<span>${esc(r.title)}${qty}${sub}</span></div>`;
}

function groupByTitle(rows) {
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.title)) groups.set(r.title, []);
    groups.get(r.title).push(r);
  }
  return [...groups.values()];
}

function range(values) {
  const v = values.filter((x) => x != null);
  if (!v.length) return "—";
  const lo = Math.min(...v), hi = Math.max(...v);
  return lo === hi ? money(lo) : `${money(lo)} – ${money(hi)}`;
}

const PAD = '<span class="expander-pad"></span>'; // keeps single items aligned with group rows
const CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';

function refreshBalance() {
  api().balance().then((c) => ($("#balance").textContent = money(c))).catch(() => {});
}

// ---------- inventory & offers tables ----------

const LISTS = {
  inventory: {
    fetch: (cursor) => api().inventory(state.game, $("#inventory-search").value.trim(), $("#inventory-filter").value === "dmarket", cursor),
    selectable: (r) => r.sellable,
    // cells after the item column, for one item or a whole group
    cells: (rs) =>
      `<td>${[...new Set(rs.map((r) => r.inMarket))].map((m) => m
        ? '<span class="badge dm">DMarket</span>'
        : '<span class="badge" title="Deposit on dmarket.com to sell">Steam</span>').join(" ")}</td>` +
      `<td class="num">${range(rs.map((r) => r.recommendedCents))}</td>`,
    empty: "No items found.",
  },
  offers: {
    fetch: (cursor) => api().offers(state.game, $("#offers-search").value.trim(), cursor),
    selectable: () => true,
    cells: (rs) => {
      const newest = rs.map((r) => r.createdAt).filter(Boolean).sort().pop();
      return `<td class="num">${range(rs.map((r) => r.priceCents))}</td><td>${newest ? new Date(newest).toLocaleDateString() : "—"}</td>`;
    },
    empty: "No active offers.",
  },
};

async function loadList(name, more = false) {
  const s = state[name], req = ++s.req;
  state.loaded.add(name);
  if (!more) {
    s.rows = []; s.cursor = ""; s.total = 0; s.sel.clear();
    renderList(name, true);
  }
  $(`#${name}-more`).disabled = true;
  s.busy = true;
  try {
    // Load every page so groups count all copies. shortcut: stops at 5000 items per click, Load more continues.
    for (let page = 0; page < 50; page++) {
      const r = await LISTS[name].fetch(s.cursor);
      if (req !== s.req) return; // a newer search/refresh superseded this one
      s.rows.push(...r.items);
      s.cursor = r.cursor;
      s.total = r.total;
      if (!s.cursor || !r.items.length) break;
      renderList(name);
    }
  } catch (e) {
    if (req === s.req) toast(errMsg(e), true);
  }
  if (req === s.req) {
    s.busy = false;
    renderList(name);
  }
}

function renderList(name, loading = false) {
  const s = state[name], L = LISTS[name];
  s.groups = groupByTitle(s.rows);
  const itemRow = (r, cell, cls = "") => {
    const ok = L.selectable(r), on = s.sel.has(r.id);
    return `<tr class="${cls}${on ? " selected" : ""}${ok ? "" : " disabled"}">` +
      `<td class="check"><input type="checkbox" data-id="${esc(r.id)}" ${on ? "checked" : ""} ${ok ? "" : "disabled"} aria-label="Select ${esc(r.title)}"></td>` +
      `<td>${cell}</td>${L.cells([r])}</tr>`;
  };
  const html = s.groups.map((g, gi) => {
    if (g.length === 1) return itemRow(g[0], itemCell(g[0], 1, PAD));
    const title = g[0].title, open = s.open.has(title);
    const ok = g.filter(L.selectable), on = ok.filter((r) => s.sel.has(r.id)).length;
    const expander = `<button class="expander" type="button" data-group="${gi}" aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} ${g.length} copies of ${esc(title)}">${CHEVRON}</button>`;
    const head = `<tr class="group${on ? " selected" : ""}${ok.length ? "" : " disabled"}">` +
      `<td class="check"><input type="checkbox" data-group="${gi}" ${on && on === ok.length ? "checked" : ""} ${on && on < ok.length ? "data-mixed" : ""} ${ok.length ? "" : "disabled"} aria-label="Select all ${esc(title)}"></td>` +
      `<td>${itemCell(g[0], g.length, expander)}</td>${L.cells(g)}</tr>`;
    const kids = open ? g.map((r) => {
      const sub = r.details && r.details.length ? `<span class="sub">${esc(r.details.join(" · "))}</span>` : "";
      return itemRow(r, `<div class="child-item">${esc(r.title)}${sub}</div>`, "child");
    }).join("") : "";
    return head + kids;
  }).join("");
  $(`#${name}-body`).innerHTML = html || `<tr class="empty"><td colspan="4">${loading ? "Loading…" : L.empty}</td></tr>`;
  $(`#${name}-body`).querySelectorAll("[data-mixed]").forEach((c) => (c.indeterminate = true));
  const more = $(`#${name}-more`);
  more.hidden = loading || s.busy || !s.cursor;
  more.disabled = false;
  const total = s.total || s.rows.length;
  $(`#${name}-count`).textContent = loading ? ""
    : s.busy ? `Loading… ${s.rows.length} of ${total}`
    : s.cursor ? `${s.rows.length} of ${total} items` : plural(s.rows.length, "item");
  syncSelection(name);
}

function syncSelection(name) {
  const s = state[name], n = s.sel.size;
  const selectable = s.rows.filter(LISTS[name].selectable);
  const all = $(`#${name}-all`);
  all.checked = selectable.length > 0 && selectable.every((r) => s.sel.has(r.id));
  all.indeterminate = n > 0 && !all.checked;
  if (name === "inventory") {
    $("#list-btn").disabled = !n;
    $("#list-btn").textContent = n ? `List for sale (${n})` : "List for sale";
  } else {
    $("#reprice-btn").disabled = $("#remove-btn").disabled = !n;
  }
}

const selected = (name) => state[name].rows.filter((r) => state[name].sel.has(r.id));

// Re-render, then put keyboard focus back on the control that triggered it.
function rerender(name, focusSelector) {
  renderList(name);
  const el = $(`#${name}-body`).querySelector(focusSelector);
  if (el) el.focus();
}

for (const name of ["inventory", "offers"]) {
  $(`#${name}-body`).addEventListener("change", (e) => {
    const s = state[name], { id, group } = e.target.dataset;
    if (id != null) {
      e.target.checked ? s.sel.add(id) : s.sel.delete(id);
      rerender(name, `input[data-id="${CSS.escape(id)}"]`);
    } else if (group != null) {
      s.groups[group].filter(LISTS[name].selectable).forEach((r) => (e.target.checked ? s.sel.add(r.id) : s.sel.delete(r.id)));
      rerender(name, `input[data-group="${group}"]`);
    }
  });
  $(`#${name}-body`).addEventListener("click", (e) => {
    const btn = e.target.closest(".expander");
    if (!btn) return;
    const s = state[name], title = s.groups[btn.dataset.group][0].title;
    s.open.has(title) ? s.open.delete(title) : s.open.add(title);
    rerender(name, `.expander[data-group="${btn.dataset.group}"]`);
  });
  $(`#${name}-all`).addEventListener("change", (e) => {
    const s = state[name];
    s.rows.filter(LISTS[name].selectable).forEach((r) => (e.target.checked ? s.sel.add(r.id) : s.sel.delete(r.id)));
    renderList(name);
  });
  $(`#${name}-more`).addEventListener("click", () => loadList(name, true));
  let timer;
  $(`#${name}-search`).addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => loadList(name), 300);
  });
}
$("#inventory-filter").addEventListener("change", () => loadList("inventory"));

// ---------- transaction history (sold + bought, merged) ----------

async function loadHistory(more = false) {
  const h = state.history, req = ++h.req;
  state.loaded.add("history");
  if (!more) {
    const f = $("#history-filter").value;
    h.kinds = f === "all" ? ["sold", "bought"] : [f];
    for (const k of h.kinds) h[k] = { rows: [], cursor: "", done: false };
    renderHistory(true);
  }
  $("#history-more").disabled = true;
  try {
    const pending = h.kinds.filter((k) => !h[k].done);
    const res = await Promise.all(pending.map((k) => api().history(k, h[k].cursor).then((r) => [k, r])));
    if (req !== h.req) return;
    for (const [k, r] of res) {
      h[k].rows.push(...r.rows);
      h[k].cursor = r.cursor;
      h[k].done = !r.cursor || !r.rows.length;
    }
  } catch (e) {
    if (req === h.req) toast(errMsg(e), true);
  }
  if (req === h.req) renderHistory();
}

function renderHistory(loading = false) {
  const h = state.history, streams = h.kinds.map((k) => h[k]);
  // Two independently paged streams: hide rows older than what every unfinished stream has reached,
  // otherwise a later page could slot rows in above ones already shown.
  const cutoff = Math.max(0, ...streams.filter((s) => !s.done).map((s) => (s.rows.length ? s.rows[s.rows.length - 1].date : Infinity)));
  const rows = streams.flatMap((s) => s.rows).filter((r) => r.date >= cutoff).sort((a, b) => b.date - a.date);
  $("#history-body").innerHTML = rows.length
    ? rows.map((r) =>
        `<tr><td><span class="badge ${r.kind}">${r.kind === "sold" ? "Sold" : "Bought"}</span></td>` +
        `<td>${esc(r.title)}</td>` +
        `<td class="num">${money(r.priceCents)}</td>` +
        `<td class="num">${money(r.feeCents)}</td>` +
        `<td class="num ${r.netCents > 0 ? "pos" : "neg"}">${r.netCents > 0 ? "+" : ""}${money(r.netCents)}</td>` +
        `<td><span class="badge ${esc(r.status)}">${esc(STATUS[r.status] || r.status || "—")}</span></td>` +
        `<td>${r.date ? new Date(r.date * 1000).toLocaleString() : "—"}</td></tr>`).join("")
    : `<tr class="empty"><td colspan="7">${loading ? "Loading…" : streams.every((s) => s.done) ? "No transactions yet." : "Load more to see older transactions."}</td></tr>`;
  const more = $("#history-more");
  more.hidden = loading || streams.every((s) => s.done);
  more.disabled = false;
}

$("#history-filter").addEventListener("change", () => loadHistory());
$("#history-more").addEventListener("click", () => loadHistory(true));

// ---------- confirm dialog (list / reprice / remove) ----------

const dlg = $("#dlg");

function setWarn(msg, error = false) {
  $("#dlg-warn").textContent = msg;
  $("#dlg-warn").className = "warn" + (error ? " error" : "");
}

// Rows are grouped by title: one price per title, applied to every selected copy.
function openDialog(cfg) {
  dlg.cfg = cfg;
  dlg.step = cfg.priced ? "edit" : "review";
  cfg.groups = groupByTitle(cfg.rows).map((items) => {
    const known = items.map((r) => r.cents).filter((c) => c != null);
    return { items, title: items[0].title, market: null, cents: known.length ? Math.min(...known) : null };
  });
  $("#dlg-title").textContent = cfg.title;
  $("#dlg-desc").textContent = cfg.desc;
  $("#dlg-head").innerHTML = cfg.priced
    ? `<th>Item</th><th class="num">${cfg.refLabel}</th><th class="num">Lowest listing</th><th class="num">Your price each ($)</th>`
    : `<th>Item</th><th class="num">Price</th>`;
  $("#dlg-body").innerHTML = cfg.groups.map((g) =>
    `<tr><td>${itemCell(g.items[0], g.items.length)}</td>` + (cfg.priced
      ? `<td class="num">${range(g.items.map((r) => r.ref))}</td><td class="num market">…</td>` +
        `<td class="num"><input class="price-input" inputmode="decimal" aria-label="Price each for ${esc(g.title)}" value="${g.cents ? (g.cents / 100).toFixed(2) : ""}"></td>`
      : `<td class="num">${range(g.items.map((r) => r.cents))}</td>`) + "</tr>").join("");
  syncDialog();
  dlg.showModal();
  if (cfg.priced) {
    api().best_prices(state.game, cfg.groups.map((g) => g.title))
      .then((m) => cfg.groups.forEach((g) => (g.market = m[g.title] ?? null)))
      .catch(() => {})
      .finally(() => {
        if (dlg.cfg !== cfg) return;
        dlg.querySelectorAll(".market").forEach((td, i) => (td.textContent = money(cfg.groups[i].market)));
        syncDialog();
      });
  }
}

function syncDialog() {
  const { cfg, step } = dlg, review = step === "review";
  dlg.querySelectorAll(".price-input").forEach((inp) => (inp.disabled = review));
  $("#dlg-back").hidden = !(cfg.priced && review);
  const total = cfg.rows.reduce((a, r) => a + (r.cents || 0), 0);
  const primary = $("#dlg-primary");
  primary.textContent = review ? `${cfg.verb} ${plural(cfg.rows.length, "item")} · ${money(total)}` : "Review";
  primary.className = "btn " + (cfg.danger ? "danger" : "primary");
  // Typo guard: flag prices far below the market (or the reference price when there is no market).
  let low = 0;
  dlg.querySelectorAll("#dlg-body tr").forEach((tr, i) => {
    const g = cfg.groups[i], refs = g.items.map((r) => r.ref).filter((c) => c != null);
    const base = g.market ?? (refs.length ? Math.min(...refs) : null);
    const isLow = review && cfg.priced && base > 0 && g.cents < base * 0.7;
    tr.classList.toggle("low", isLow);
    low += isLow;
  });
  setWarn(low ? `${plural(low, "price")} more than 30% below the market price. Check before confirming.` : "");
}

function readPrices() {
  let ok = true;
  dlg.querySelectorAll(".price-input").forEach((inp, i) => {
    const v = inp.value.trim().replace(",", ".");
    const good = /^\d+(\.\d{1,2})?$/.test(v) && Number(v) > 0;
    inp.setAttribute("aria-invalid", String(!good));
    if (good) {
      const g = dlg.cfg.groups[i];
      g.cents = Math.round(Number(v) * 100);
      g.items.forEach((r) => (r.cents = g.cents));
    }
    ok = ok && good;
  });
  if (!ok) setWarn("Enter a price above 0 with up to 2 decimals, e.g. 12.50", true);
  return ok;
}

function report(res, cfg) {
  const titles = Object.fromEntries(cfg.rows.map((r) => [r.id, r.title]));
  const failed = res.failed || [];
  let msg = `${cfg.pastVerb} ${plural(res.ok, "item")}.`;
  if (failed.length) {
    msg += `\n${failed.length} failed:\n` + failed.map((f) => `• ${titles[f.assetId || f.offerId] || "Item"}: ${f.message || f.code}`).join("\n");
  }
  toast(msg, failed.length > 0);
}

$("#dlg-primary").addEventListener("click", async () => {
  const cfg = dlg.cfg, btn = $("#dlg-primary");
  if (dlg.step === "edit") {
    if (readPrices()) { dlg.step = "review"; syncDialog(); }
    return;
  }
  btn.disabled = true;
  try {
    const res = await cfg.send(cfg.rows);
    dlg.close();
    report(res, cfg);
    cfg.done();
  } catch (e) {
    setWarn(errMsg(e), true);
  } finally {
    btn.disabled = false;
  }
});
$("#dlg-back").addEventListener("click", () => { dlg.step = "edit"; syncDialog(); });
$("#dlg-cancel").addEventListener("click", () => dlg.close());

$("#list-btn").addEventListener("click", () => openDialog({
  title: "List items for sale",
  desc: "Prices start at DMarket's recommended price. Adjust them, review, then confirm.",
  rows: selected("inventory").map((r) => ({ ...r, ref: r.recommendedCents, cents: r.recommendedCents })),
  priced: true, refLabel: "Recommended", verb: "List", pastVerb: "Listed",
  send: (rows) => api().create_offers(rows.map((r) => ({ assetId: r.id, priceCents: r.cents }))),
  done: () => { loadList("inventory"); loadList("offers"); refreshBalance(); },
}));

$("#reprice-btn").addEventListener("click", () => openDialog({
  title: "Change prices",
  desc: "Set new prices for the selected offers, review, then confirm.",
  rows: selected("offers").map((r) => ({ ...r, ref: r.priceCents, cents: r.priceCents })),
  priced: true, refLabel: "Current", verb: "Update", pastVerb: "Updated",
  send: (rows) => api().update_offers(rows.map((r) => ({ offerId: r.id, priceCents: r.cents }))),
  done: () => loadList("offers"),
}));

$("#remove-btn").addEventListener("click", () => openDialog({
  title: "Remove offers from sale",
  desc: "These listings will be taken off the market. The items stay in your DMarket inventory.",
  rows: selected("offers").map((r) => ({ ...r, cents: r.priceCents })),
  priced: false, danger: true, verb: "Remove", pastVerb: "Removed",
  send: (rows) => api().delete_offers(rows.map((r) => r.id)),
  done: () => { loadList("offers"); loadList("inventory"); },
}));

// ---------- tabs, header, theme, boot ----------

function showTab(name) {
  state.tab = name;
  for (const t of TABS) {
    const on = t === name;
    $(`#tab-${t}`).setAttribute("aria-selected", String(on));
    $(`#tab-${t}`).tabIndex = on ? 0 : -1;
    $(`#panel-${t}`).hidden = !on;
  }
  if (!state.loaded.has(name)) reload(name);
}

const reload = (name) => (name === "history" ? loadHistory() : loadList(name));

TABS.forEach((t) => $(`#tab-${t}`).addEventListener("click", () => showTab(t)));
$(".tabs").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const i = TABS.indexOf(state.tab);
  const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
  showTab(next);
  $(`#tab-${next}`).focus();
});

$("#game").addEventListener("change", (e) => {
  state.game = e.target.value;
  state.loaded.delete("inventory");
  state.loaded.delete("offers");
  if (state.tab !== "history") reload(state.tab);
});

$("#refresh").addEventListener("click", () => {
  refreshBalance();
  reload(state.tab);
});

function setTheme(theme, save) {
  document.documentElement.dataset.theme = theme;
  $("#theme").setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  if (save) api().set_theme(theme);
}
$("#theme").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true));

function resetData() {
  // Bumping req makes any in-flight response from the previous account get dropped.
  for (const name of ["inventory", "offers"]) Object.assign(state[name], { rows: [], cursor: "", total: 0, req: state[name].req + 1 });
  for (const name of ["inventory", "offers"]) state[name].sel.clear();
  state.history = { req: state.history.req + 1, kinds: [] };
  state.loaded.clear();
  $("#balance").textContent = "—";
}

async function connect(account) {
  $("#retry").disabled = $("#account").disabled = true;
  resetData();
  let s;
  try {
    s = await api().connect(account ?? null);
  } catch (e) {
    s = { state: "error", message: errMsg(e), accounts: [] };
  }
  $("#retry").disabled = $("#account").disabled = false;
  $("#account-field").hidden = !s.accounts.length;
  $("#account").innerHTML = s.accounts.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
  $("#account").value = s.account ?? "";
  const ok = s.state === "ok";
  $("#setup").hidden = ok;
  $("#main").hidden = !ok;
  if (!ok) {
    $("#setup-reason").textContent = s.state === "missing" ? "No API keys found in .env." : `Could not connect to "${s.account}": ${s.message}`;
    return;
  }
  $("#balance").textContent = money(s.balanceCents);
  showTab(state.tab);
}
$("#retry").addEventListener("click", () => connect($("#account").value || null));
$("#account").addEventListener("change", (e) => connect(e.target.value));

async function boot() {
  setTheme((await api().get_theme()) || document.documentElement.dataset.theme, false);
  connect();
}
if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
