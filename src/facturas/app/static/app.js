"use strict";

const PARALLEL = 2; // documents uploaded at the same time (the server reads two at a time)
const S = { batch: null, entries: [], docs: {}, summary: null, open: null, view: "original", seen: new Set(), queued: new Set() };
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ICON = {
  check: '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  cross: '<svg viewBox="0 0 16 16"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>',
  dash: '<svg viewBox="0 0 16 16"><path d="M4.5 8h7"/></svg>',
  alert: '<svg viewBox="0 0 20 20"><path d="M10 6.5v4.5M10 14h.01"/><circle cx="10" cy="10" r="7.5"/></svg>',
  ok: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5"/><path d="M6.8 10.2l2.2 2.2 4.2-4.6"/></svg>',
};

const SRC_TITLE = { qr: "Confirmado con el QR de ARCA", qr_corrigio: "Tomado del QR de ARCA", pdf: "Texto del PDF", imagen: "Leído de la imagen", llm: "Leído por Claude" };

function money(v) {
  if (v === null || v === undefined) return "—";
  const s = Math.abs(v).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (v < 0 ? "-$ " : "$ ") + s;
}
const dateAr = (iso) => (iso ? iso.split("-").reverse().join("/") : "—");
const cuit = (c) => (c && c.length === 11 ? `${c.slice(0, 2)}-${c.slice(2, 10)}-${c.slice(10)}` : c || "");
const kindClass = (k) => "k-" + (k || "").split(" ")[0].toLowerCase();

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

/* ---------------------------------------------------------------- adding files */

async function ensureBatch() {
  if (S.batch) return;
  const r = await fetch("/api/lotes", { method: "POST" });
  S.batch = (await r.json()).id;
}

async function addFiles(files) {
  files = [...files].filter((f) => /\.(pdf|jpe?g|png|webp|tiff?|bmp)$/i.test(f.name));
  if (!files.length) return toast("Subí archivos PDF, JPG o PNG.");
  await ensureBatch();
  $("#empty").hidden = true;
  $("#work").hidden = false;
  for (const f of files) {
    const isPdf = /\.pdf$/i.test(f.name);
    S.entries.push({ key: S.entries.length, file: f, name: f.name, status: "queued", doc: null, thumb: isPdf ? null : URL.createObjectURL(f) });
  }
  renderAll();
  pump();
}

function pump() {
  const active = S.entries.filter((e) => e.status === "reading").length;
  const next = S.entries.filter((e) => e.status === "queued").slice(0, Math.max(0, PARALLEL - active));
  next.forEach(upload);
}

async function upload(entry) {
  entry.status = "reading";
  renderAll();
  const fd = new FormData();
  fd.append("archivo", entry.file, entry.name);
  fd.append("orden", String(entry.key));
  try {
    const r = await fetch(`/api/lotes/${S.batch}/documentos`, { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    entry.doc = data.documento;
    entry.status = "done";
    applyState(data.lote);
  } catch (err) {
    entry.status = "error";
    entry.error = String(err.message || err);
  }
  renderAll();
  pump();
}

function applyState(lote) {
  S.docs = {};
  for (const d of lote.documentos) S.docs[d.id] = d;
  S.summary = lote.resumen;
  if (S.open && S.docs[S.open]) fillDetail(S.docs[S.open], false);
}

async function loadExample() {
  const r = await fetch("/api/ejemplo");
  const { archivos } = await r.json();
  if (!archivos.length) return toast("No se encontró el lote de ejemplo (carpeta demo/lote).");
  const files = await Promise.all(
    archivos.map(async (a) => {
      const blob = await (await fetch(a.url)).blob();
      return new File([blob], a.nombre, { type: blob.type });
    })
  );
  addFiles(files);
}

/* ---------------------------------------------------------------- rendering */

function renderAll() {
  renderKpis();
  renderPile();
  renderRows();
  renderQueue();
  const anyDone = S.entries.some((e) => e.status === "done");
  $("#btn-excel").disabled = !anyDone;
  $("#btn-csv").disabled = !anyDone;
}

function counts() {
  const done = S.entries.filter((e) => e.status === "done" || e.status === "error").length;
  const docs = S.entries.filter((e) => e.doc).map((e) => S.docs[e.doc]).filter(Boolean);
  const ok = docs.filter((d) => d.estado === "ok").length;
  return { total: S.entries.length, done, ok, rev: done - ok };
}

function renderKpis() {
  const c = counts();
  $("#k-proc").textContent = `${c.done} de ${c.total}`;
  $("#k-bar").style.width = c.total ? `${(100 * c.done) / c.total}%` : "0";
  $("#k-ok").textContent = c.ok;
  $("#k-rev").textContent = c.rev;
  const s = S.summary || { importe_ok: 0, iva_ok: 0 };
  $("#k-imp").textContent = money(s.importe_ok);
  $("#k-iva").textContent = `IVA de facturas A: ${money(s.iva_ok)}`;
}

function renderPile() {
  const pile = $("#pile");
  if (pile.children.length !== S.entries.length) {
    pile.innerHTML = "";
    for (const e of S.entries) {
      const t = el("div", "thumb");
      t.title = e.name;
      t.innerHTML = e.thumb ? `<img src="${e.thumb}" alt="">` : '<div class="pdf"><b>PDF</b></div>';
      t.appendChild(el("span", "badge"));
      t.addEventListener("click", () => e.doc && openDetail(e.doc));
      pile.appendChild(t);
    }
  }
  S.entries.forEach((e, i) => {
    const t = pile.children[i];
    const d = e.doc && S.docs[e.doc];
    t.className = "thumb " + (e.status === "queued" ? "queued" : e.status === "reading" ? "reading" : d && d.estado === "ok" ? "done-ok" : "done-rev");
    t.querySelector(".badge").textContent = e.status === "done" || e.status === "error" ? (d && d.estado === "ok" ? "✓" : "!") : "";
    if (!e.thumb && d && d.imagenes.original) {
      e.thumb = d.imagenes.original.url;
      t.querySelector(".pdf")?.replaceWith(Object.assign(document.createElement("img"), { src: e.thumb, alt: "" }));
    }
  });
}

function chip(d) {
  return d.estado === "ok"
    ? `<span class="chip ok">${ICON.check}Lista</span>`
    : `<span class="chip rev">${ICON.alert}Revisar</span>`;
}

function renderRows() {
  const tbody = $("#rows");
  tbody.innerHTML = "";
  for (const e of S.entries) {
    const d = e.doc && S.docs[e.doc];
    const tr = el("tr");
    if (!d) {
      const st = e.status === "error"
        ? `<span class="chip rev">Error</span>`
        : e.status === "reading" ? `<span class="chip reading"><i class="spin"></i>Leyendo…</span>` : `<span class="chip wait">En cola</span>`;
      tr.innerHTML = `<td class="c-state">${st}</td>
        <td><i class="skel" style="width:70px"></i></td><td><i class="skel" style="width:140px"></i></td>
        <td class="c-comp"><div class="comp"><i class="skel" style="width:120px"></i><small><span>${esc(e.name)}</span></small></div></td>
        <td class="num"><i class="skel" style="width:70px"></i></td>
        <td class="num"><i class="skel" style="width:60px"></i></td><td class="num"><i class="skel" style="width:80px"></i></td>`;
      tbody.appendChild(tr);
      continue;
    }
    const f = d.factura;
    const nc = f.tipo === "Nota de Crédito";
    const comp = f.punto_venta != null && f.numero != null
      ? `${nc ? "NC" : "FC"} ${f.letra || ""} ${String(f.punto_venta).padStart(5, "0")}-${String(f.numero).padStart(8, "0")}` : "—";
    const iva = f.iva_total ?? f.iva_contenido;
    tr.className = (d.estado === "ok" ? "" : "rev ") + (S.open === d.id ? "sel " : "") + (S.seen.has(d.id) ? "" : "fresh");
    S.seen.add(d.id);
    tr.innerHTML = `<td class="c-state">${chip(d)}</td>
      <td class="mono">${dateAr(f.fecha_emision)}</td>
      <td><div class="prov"><b title="${esc(f.emisor_razon_social)}">${esc(f.emisor_razon_social || "—")}</b><small>${esc(cuit(f.emisor_cuit))}</small></div></td>
      <td class="c-comp"><div class="comp"><span class="mono">${comp}</span><small><b class="kind ${kindClass(d.captura)}">${esc(d.captura)}</b><span title="${esc(d.archivo)}">${esc(d.archivo)}</span></small></div></td>
      <td class="num">${money(f.neto_gravado)}</td>
      <td class="num">${money(iva)}</td>
      <td class="num total ${nc ? "neg" : ""}">${f.total == null ? "—" : (nc ? "−" : "") + money(f.total)}</td>`;
    tr.addEventListener("click", () => openDetail(d.id));
    tbody.appendChild(tr);
  }
}

function renderQueue() {
  const c = counts();
  const docs = S.entries.map((e) => e.doc && S.docs[e.doc]).filter((d) => d && d.estado !== "ok");
  $("#q-count").textContent = docs.length;
  const pending = c.total - c.done;
  $("#q-sum").innerHTML = !c.done
    ? "Leyendo las primeras facturas…"
    : pending
      ? `Van <b>${c.done} de ${c.total}</b>: <b>${c.ok}</b> cargadas solas y <b>${c.rev}</b> para revisar.`
      : `<b>${c.ok} de ${c.total}</b> cargadas solas, <b>${c.rev}</b> para revisar.`;
  const ol = $("#queue");
  ol.innerHTML = "";
  if (!docs.length) {
    ol.appendChild(el("li", "queue-empty", c.done ? "Nada para revisar por ahora." : ""));
    ol.lastChild.style.cursor = "default";
    return;
  }
  for (const d of docs) {
    const fail = d.controles.find((k) => k.estado === "error");
    const li = el("li", (S.open === d.id ? "sel " : "") + (S.queued.has(d.id) ? "" : "enter"));
    S.queued.add(d.id);
    li.innerHTML = `<p class="q-reason">${esc(d.motivo)}</p>
      <p class="q-detail">${esc(fail ? fail.detalle : "")}</p><p class="q-file">${esc(d.archivo)}</p>`;
    li.addEventListener("click", () => openDetail(d.id));
    ol.appendChild(li);
  }
}

/* ---------------------------------------------------------------- detail */

function openDetail(id) {
  const d = S.docs[id];
  if (!d) return;
  S.open = id;
  S.view = "original";
  $("#detail").hidden = false;
  fillDetail(d, true);
  renderRows();
  renderQueue();
}

function closeModal(m) {
  m.hidden = true;
  if (m.id === "detail") {
    S.open = null;
    renderRows();
    renderQueue();
  }
}

function fillDetail(d, fresh) {
  const f = d.factura;
  $("#d-kind").textContent = [d.captura, f.tipo && f.letra ? `${f.tipo} ${f.letra}` : null].filter(Boolean).join(" · ");
  $("#d-title").textContent = f.emisor_razon_social || d.archivo;
  $("#d-file").textContent = d.archivo;
  const v = $("#d-verdict");
  v.className = "verdict " + (d.estado === "ok" ? "ok" : "rev");
  v.innerHTML = d.estado === "ok"
    ? `${ICON.ok}<span>Lista para cargar: pasó todos los controles.</span>`
    : `${ICON.alert}<span>Para revisar: ${esc(d.motivo)}.</span>`;

  const ul = $("#d-checks");
  ul.innerHTML = "";
  for (const c of d.controles) {
    const li = el("li", c.estado);
    const icon = c.estado === "ok" ? ICON.check : c.estado === "error" ? ICON.cross : ICON.dash;
    li.innerHTML = `<span class="mark ${c.estado}">${icon}</span><div><b>${esc(c.titulo)}</b><p title="${esc(c.detalle)}">${esc(c.detalle)}</p></div>`;
    ul.appendChild(li);
  }

  const dl = $("#d-fields");
  dl.innerHTML = "";
  const fields = [...d.campos];
  const qrBad = d.problemas.includes("qr");
  fields.push({ id: "qr", etiqueta: "QR de ARCA", valor: !d.qr_leido ? "No encontrado" : qrBad ? "No coincide" : "Leído y verificado", fuente: "" });
  const label = { qr: "QR", qr_corrigio: "QR", pdf: "PDF", imagen: "Imagen", llm: "Claude" };
  const boxes = (d.cajas[S.view] || {});
  for (const fl of fields) {
    const row = el("div", "f");
    row.dataset.id = fl.id;
    if (d.problemas.includes(fl.id)) row.classList.add("bad");
    if (!boxes[fl.id]) row.classList.add("nobox");
    row.innerHTML = `<span class="f-label">${esc(fl.etiqueta)}${fl.fuente ? `<span class="src-text" title="${esc(SRC_TITLE[fl.fuente] || "")}"><i class="src ${fl.fuente}"></i>${label[fl.fuente] || ""}</span>` : ""}</span>
      <span class="val">${esc(fl.valor)}</span>`;
    row.addEventListener("mouseenter", () => hot(fl.id));
    row.addEventListener("mouseleave", () => hot(null));
    dl.appendChild(row);
  }
  d._fields = fields;

  document.querySelectorAll("#d-views button").forEach((b) => {
    b.classList.toggle("on", b.dataset.view === S.view);
    b.disabled = !d.imagenes[b.dataset.view];
  });
  if (fresh) $(".info").scrollTop = 0;
  drawViewer(d);
}

function drawViewer(d) {
  const img = $("#d-img");
  const svg = $("#d-svg");
  const meta = d.imagenes[S.view] || d.imagenes.original;
  svg.innerHTML = "";
  if (!meta) {
    img.removeAttribute("src");
    return;
  }
  if (img.getAttribute("src") !== meta.url) img.src = meta.url;
  img.alt = d.archivo;
  svg.setAttribute("viewBox", `0 0 ${meta.w} ${meta.h}`);
  fitFrame(meta);
  const shown = new Set(d._fields.map((x) => x.id));
  const boxes = d.cajas[S.view] || {};
  for (const [id, polys] of Object.entries(boxes)) {
    if (!shown.has(id)) continue;
    const fl = d._fields.find((x) => x.id === id);
    for (const p of polys) {
      const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      poly.setAttribute("points", p.map((q) => q.join(",")).join(" "));
      const cls = d.problemas.includes(id) ? "bad" : id === "qr" || fl.fuente === "qr" || fl.fuente === "qr_corrigio" ? "qr-ok" : "";
      poly.setAttribute("class", `box ${cls}`);
      poly.dataset.id = id;
      poly.addEventListener("mouseenter", () => hot(id));
      poly.addEventListener("mouseleave", () => hot(null));
      svg.appendChild(poly);
    }
  }
  const tag = document.createElementNS("http://www.w3.org/2000/svg", "g");
  tag.id = "d-tag";
  svg.appendChild(tag);
}

function fitFrame(meta) {
  const stage = $("#d-stage");
  const frame = $("#d-frame");
  const sw = stage.clientWidth - 32;
  const sh = stage.clientHeight - 16;
  const s = Math.min(sw / meta.w, sh / meta.h);
  frame.style.width = `${Math.floor(meta.w * s)}px`;
  frame.style.height = `${Math.floor(meta.h * s)}px`;
}

function hot(id) {
  const d = S.docs[S.open];
  if (!d) return;
  document.querySelectorAll("#d-svg .box").forEach((b) => {
    b.classList.toggle("hot", b.dataset.id === id);
    b.classList.toggle("dim", id !== null && b.dataset.id !== id);
  });
  document.querySelectorAll("#d-fields .f").forEach((r) => r.classList.toggle("hot", r.dataset.id === id));
  const tag = $("#d-tag");
  if (!tag) return;
  tag.innerHTML = "";
  if (!id) return;
  const poly = document.querySelector(`#d-svg .box[data-id="${id}"]`);
  const fl = d._fields.find((x) => x.id === id);
  if (!poly || !fl) return;
  const pts = poly.getAttribute("points").split(" ").map((q) => q.split(",").map(Number));
  const meta = d.imagenes[S.view] || d.imagenes.original;
  const fs = Math.max(13, meta.w * 0.017);
  const text = `${fl.etiqueta}: ${fl.valor}`;
  const tw = text.length * fs * 0.56 + fs;
  const th = fs * 1.7;
  const minX = Math.min(...pts.map((p) => p[0]));
  const minY = Math.min(...pts.map((p) => p[1]));
  const maxY = Math.max(...pts.map((p) => p[1]));
  const x = Math.max(4, Math.min(minX, meta.w - tw - 4));
  const y = minY - th - 6 > 4 ? minY - th - 6 : maxY + 6;
  const ns = "http://www.w3.org/2000/svg";
  const r = document.createElementNS(ns, "rect");
  Object.entries({ x, y, width: tw, height: th, rx: fs * 0.35, class: "tag-bg" }).forEach(([k, v]) => r.setAttribute(k, v));
  const t = document.createElementNS(ns, "text");
  Object.entries({ x: x + fs * 0.5, y: y + th * 0.68, class: "tag-tx", "font-size": fs }).forEach(([k, v]) => t.setAttribute(k, v));
  t.textContent = text;
  tag.append(r, t);
  requestAnimationFrame(() => {
    const real = t.getComputedTextLength();
    if (real) {
      r.setAttribute("width", real + fs);
      if (x + real + fs > meta.w - 4) {
        const nx = Math.max(4, meta.w - real - fs - 4);
        r.setAttribute("x", nx);
        t.setAttribute("x", nx + fs * 0.5);
      }
    }
  });
}

function step(delta) {
  const ids = S.entries.map((e) => e.doc).filter((id) => id && S.docs[id]);
  const i = ids.indexOf(S.open);
  if (i < 0) return;
  const next = ids[(i + delta + ids.length) % ids.length];
  openDetail(next);
}

/* ---------------------------------------------------------------- export */

async function exportExcel() {
  const a = Object.assign(document.createElement("a"), { href: `/api/lotes/${S.batch}/excel`, download: "comprobantes.xlsx" });
  document.body.appendChild(a);
  a.click();
  a.remove();
  const r = await fetch(`/api/lotes/${S.batch}/excel/vista`);
  const data = await r.json();
  const c = counts();
  $("#x-sub").textContent = `Descargado: ${c.done} comprobantes, ${c.rev} marcados para revisar. Así queda el archivo:`;
  const tabs = $("#x-tabs");
  tabs.innerHTML = "";
  data.hojas.forEach((h, i) => {
    const b = el("button", i === 0 ? "on" : "", esc(h.nombre));
    b.type = "button";
    b.addEventListener("click", () => {
      tabs.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      drawSheet(h);
    });
    tabs.appendChild(b);
  });
  drawSheet(data.hojas[0]);
  $("#xls").hidden = false;
}

function drawSheet(h) {
  const t = $("#x-grid");
  const n = Math.max(...h.filas.map((f) => f.celdas.length));
  const letters = [...Array(n)].map((_, i) => String.fromCharCode(65 + i));
  const cols = h.anchos.map((w) => `<col style="width:${Math.round(Math.min(w, 48) * 7.2)}px">`).join("");
  let html = `<colgroup><col style="width:34px">${cols}</colgroup><thead><tr><th></th>${letters.map((l) => `<th>${l}</th>`).join("")}</tr></thead><tbody>`;
  h.filas.forEach((f, i) => {
    const cls = h.nombre !== "Resumen" && i === 0 ? "h" : f.resaltada ? "r" : "";
    html += `<tr class="${cls}"><td class="rn">${i + 1}</td>${f.celdas.map((c, j) => `<td class="${f.numericas[j] ? "n" : ""}" title="${esc(c)}">${esc(c)}</td>`).join("")}</tr>`;
  });
  t.innerHTML = html + "</tbody>";
}

function exportCsv() {
  const a = Object.assign(document.createElement("a"), { href: `/api/lotes/${S.batch}/csv`, download: "comprobantes_para_importar.csv" });
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast(`CSV descargado con las ${counts().ok} facturas listas para cargar.`);
}

/* ---------------------------------------------------------------- wiring */

$("#file-input").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
$("#file-input-2").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
$("#btn-ejemplo").addEventListener("click", loadExample);
$("#btn-ejemplo-2").addEventListener("click", loadExample);
$("#btn-excel").addEventListener("click", exportExcel);
$("#btn-csv").addEventListener("click", exportCsv);
$("#k-rev-card").addEventListener("click", () => {
  const first = S.entries.map((e) => e.doc && S.docs[e.doc]).find((d) => d && d.estado !== "ok");
  if (first) openDetail(first.id);
});
$("#d-prev").addEventListener("click", () => step(-1));
$("#d-next").addEventListener("click", () => step(1));
document.querySelectorAll("#d-views button").forEach((b) =>
  b.addEventListener("click", () => {
    S.view = b.dataset.view;
    fillDetail(S.docs[S.open], false);
  })
);
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => closeModal(b.closest(".modal"))));
document.addEventListener("keydown", (e) => {
  const open = [...document.querySelectorAll(".modal")].find((m) => !m.hidden);
  if (e.key === "Escape" && open) closeModal(open);
  if (open && open.id === "detail" && e.key === "ArrowRight") step(1);
  if (open && open.id === "detail" && e.key === "ArrowLeft") step(-1);
});
window.addEventListener("resize", () => {
  const d = S.open && S.docs[S.open];
  if (d) fitFrame(d.imagenes[S.view] || d.imagenes.original);
});

const drop = $("#drop");
["dragenter", "dragover"].forEach((t) =>
  window.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); })
);
["dragleave", "drop"].forEach((t) =>
  window.addEventListener(t, (e) => { e.preventDefault(); if (t === "drop" || e.target === document.documentElement) drop.classList.remove("over"); })
);
window.addEventListener("drop", (e) => {
  if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
});
