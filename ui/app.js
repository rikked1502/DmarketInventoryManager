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
  inventory: { rows: [], cursor: "", sel: new Set(), req: 0 },
  offers: { rows: [], cursor: "", sel: new Set(), req: 0 },
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

function itemCell(r) {
  const sub = r.float ? `<span class="sub">Float ${esc(Number(r.float).toFixed(6))}</span>` : "";
  const img = r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy">` : "";
  return `<div class="item">${img}<span>${esc(r.title)}${sub}</span></div>`;
}

function refreshBalance() {
  api().balance().then((c) => ($("#balance").textContent = money(c))).catch(() => {});
}

// ---------- inventory & offers tables ----------

const LISTS = {
  inventory: {
    fetch: (cursor) => api().inventory(state.game, $("#inventory-search").value.trim(), $("#inventory-filter").value === "dmarket", cursor),
    selectable: (r) => r.sellable,
    row: (r) =>
      `<td>${itemCell(r)}</td>` +
      `<td>${r.inMarket ? '<span class="badge dm">DMarket</span>' : '<span class="badge" title="Deposit on dmarket.com to sell">Steam</span>'}</td>` +
      `<td class="num">${money(r.recommendedCents)}</td>`,
    empty: "No items found.",
  },
  offers: {
    fetch: (cursor) => api().offers(state.game, $("#offers-search").value.trim(), cursor),
    selectable: () => true,
    row: (r) =>
      `<td>${itemCell(r)}</td><td class="num">${money(r.priceCents)}</td>` +
      `<td>${r.createdAt ? new Date(r.createdAt).toLocaleDateString() : "—"}</td>`,
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
  try {
    const r = await LISTS[name].fetch(s.cursor);
    if (req !== s.req) return; // a newer search/refresh superseded this one
    s.rows.push(...r.items);
    s.cursor = r.cursor;
    s.total = r.total;
  } catch (e) {
    if (req === s.req) toast(errMsg(e), true);
  }
  if (req === s.req) renderList(name);
}

function renderList(name, loading = false) {
  const s = state[name], L = LISTS[name];
  $(`#${name}-body`).innerHTML = s.rows.length
    ? s.rows.map((r, i) => {
        const ok = L.selectable(r), on = s.sel.has(r.id);
        return `<tr class="${on ? "selected" : ""}${ok ? "" : " disabled"}">` +
          `<td class="check"><input type="checkbox" data-i="${i}" ${on ? "checked" : ""} ${ok ? "" : "disabled"} aria-label="Select ${esc(r.title)}"></td>` +
          L.row(r) + "</tr>";
      }).join("")
    : `<tr class="empty"><td colspan="4">${loading ? "Loading…" : L.empty}</td></tr>`;
  const more = $(`#${name}-more`);
  more.hidden = loading || !s.cursor;
  more.disabled = false;
  $(`#${name}-count`).textContent = loading ? "" : `${s.rows.length} of ${s.total || s.rows.length}`;
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

for (const name of ["inventory", "offers"]) {
  $(`#${name}-body`).addEventListener("change", (e) => {
    const i = e.target.dataset.i;
    if (i == null) return;
    const id = state[name].rows[i].id;
    e.target.checked ? state[name].sel.add(id) : state[name].sel.delete(id);
    e.target.closest("tr").classList.toggle("selected", e.target.checked);
    syncSelection(name);
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

function openDialog(cfg) {
  dlg.cfg = cfg;
  dlg.step = cfg.priced ? "edit" : "review";
  $("#dlg-title").textContent = cfg.title;
  $("#dlg-desc").textContent = cfg.desc;
  $("#dlg-head").innerHTML = cfg.priced
    ? `<th>Item</th><th class="num">${cfg.refLabel}</th><th class="num">Lowest listing</th><th class="num">Your price ($)</th>`
    : `<th>Item</th><th class="num">Price</th>`;
  $("#dlg-body").innerHTML = cfg.rows.map((r) =>
    `<tr><td>${itemCell(r)}</td>` + (cfg.priced
      ? `<td class="num">${money(r.ref)}</td><td class="num market">…</td>` +
        `<td class="num"><input class="price-input" inputmode="decimal" aria-label="Price for ${esc(r.title)}" value="${r.cents ? (r.cents / 100).toFixed(2) : ""}"></td>`
      : `<td class="num">${money(r.cents)}</td>`) + "</tr>").join("");
  syncDialog();
  dlg.showModal();
  if (cfg.priced) {
    api().best_prices(state.game, cfg.rows.map((r) => r.title))
      .then((m) => cfg.rows.forEach((r) => (r.market = m[r.title] ?? null)))
      .catch(() => {})
      .finally(() => {
        if (dlg.cfg !== cfg) return;
        dlg.querySelectorAll(".market").forEach((td, i) => (td.textContent = money(cfg.rows[i].market)));
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
    const r = cfg.rows[i], base = r.market ?? r.ref;
    const isLow = review && cfg.priced && base > 0 && r.cents < base * 0.7;
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
    if (good) dlg.cfg.rows[i].cents = Math.round(Number(v) * 100);
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

async function connect() {
  $("#retry").disabled = true;
  let s;
  try {
    s = await api().connect();
  } catch (e) {
    s = { state: "error", message: errMsg(e) };
  }
  $("#retry").disabled = false;
  const ok = s.state === "ok";
  $("#setup").hidden = ok;
  $("#main").hidden = !ok;
  if (!ok) {
    $("#setup-reason").textContent = s.state === "missing" ? "No API keys found in .env." : `Could not connect: ${s.message}`;
    return;
  }
  $("#balance").textContent = money(s.balanceCents);
  showTab(state.tab);
}
$("#retry").addEventListener("click", connect);

async function boot() {
  setTheme((await api().get_theme()) || document.documentElement.dataset.theme, false);
  connect();
}
if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
