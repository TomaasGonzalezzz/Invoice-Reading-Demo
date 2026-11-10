"""Build docs/presentacion.pdf from the measured results (results/*.json) and the sample outputs.

    uv run python -m facturas samples --out salida      # sample outputs shown on slide 3
    uv run python docs/build_deck.py

Needs Microsoft Edge or Google Chrome (used headless to print the HTML slides to PDF).
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
RES = ROOT / "results"


def load(name: str) -> dict:
    return json.loads((RES / name).read_text("utf-8"))


def pct(x: float) -> str:
    s = f"{x:.1f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


syn = load("metricas.json")["conditions"]
ext_img = load("externo_test_imagen.json")
ext_foto = load("externo_test_foto.json")
ext_foto_first = load("externo_test_foto_primera_corrida.json")
silent = sum(syn[c]["aprobados_con_error"] for c in syn)
traps = [syn[c]["trampas_detectadas"] for c in syn]
docs_syn = sum(syn[c]["documentos"] for c in syn)

from facturas.revision import review_batch  # noqa: E402
from facturas.schema import Result  # noqa: E402

results = [Result.model_validate_json(p.read_text("utf-8"))
           for p in sorted((ROOT / "salida").glob("*.json"))]
samples = []
for row in review_batch(results):  # same checks as the demo, including repeated invoices
    f = row.result.factura
    samples.append({
        "archivo": row.result.archivo,
        "comp": f"{f.letra or '?'} {f.punto_venta or 0:05d}-{f.numero or 0:08d}",
        "cuit": f.emisor_cuit or "—",
        "total": "—" if f.total is None else f"$ {f.total:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."),
        "ok": row.estado == "ok",
        "motivo": row.motivo or "—",
    })


def sample_rows() -> str:
    rows = []
    for s in samples:
        badge = ('<span class="badge ok">Lista para cargar</span>' if s["ok"]
                 else f'<span class="badge rev">Revisar: {html.escape(s["motivo"])}</span>')
        rows.append(f"<tr><td class='mono'>{html.escape(s['archivo'])}</td><td class='mono'>{s['comp']}</td>"
                    f"<td class='mono'>{s['cuit']}</td><td class='mono num'>{s['total']}</td><td>{badge}</td></tr>")
    return "\n".join(rows)


def kpi(value: str, label: str, tone: str = "") -> str:
    return f'<div class="kpi {tone}"><div class="v">{value}</div><div class="l">{label}</div></div>'


templates = ext_img["por_diseno_100_correctos"]
tmpl_rows = "".join(f"<tr><td>{html.escape(t)}</td><td class='num'>{v}</td><td class='num'>{ext_foto['por_diseno_100_correctos'][t]}</td></tr>"
                    for t, v in templates.items())

SLIDES = f"""
<section class="slide cover">
  <div class="cover-text">
    <div class="eyebrow">Proyecto de portfolio · noviembre 2026</div>
    <h1>Lectura automática de facturas argentinas</h1>
    <p class="lead">De PDF, escaneos y fotos de celular a datos validados, con controles antifraude.</p>
    <p class="author">Tomás González<br><span>Ingeniería en Inteligencia Artificial · Universidad de San Andrés</span></p>
  </div>
  <img class="cover-img" src="../figures/ejemplo_foto.png" alt="Foto de una factura y los datos extraídos">
</section>

<section class="slide">
  <div class="eyebrow">El problema</div>
  <h2>Cada factura que llega, alguien la carga a mano</h2>
  <div class="cards three">
    <div class="card"><div class="n">1</div><h3>Llegan en cualquier formato</h3><p>PDF por mail, escaneos y fotos por WhatsApp, cada proveedor con su propio diseño.</p></div>
    <div class="card"><div class="n">2</div><h3>Se cargan a mano</h3><p>Fecha, CUIT, neto, IVA, total y CAE, una por una, al Excel o al sistema contable.</p></div>
    <div class="card"><div class="n">3</div><h3>Los errores no se ven</h3><p>Lo caro no es la factura que no se puede leer, sino la que se carga mal y nadie revisa.</p></div>
  </div>
  <p class="note">Idea central del proyecto: automatizar la carga, pero <b>saber cuándo no confiar</b> en lo leído.</p>
</section>

<section class="slide">
  <div class="eyebrow">Qué hace</div>
  <h2>Entra un comprobante, sale un dato listo o un motivo para revisar</h2>
  <div class="flow">
    <div class="box"><b>Entrada</b><span>PDF digital<br>Escaneo<br>Foto de celular</span></div>
    <div class="arrow">→</div>
    <div class="box accent"><b>Lectura y validación</b><span>OCR · QR de ARCA<br>reglas · controles</span></div>
    <div class="arrow">→</div>
    <div class="box"><b>Salida</b><span>Excel y JSON<br>con estado y motivo</span></div>
  </div>
  <table class="out">
    <thead><tr><th>Archivo</th><th>Comprobante</th><th>CUIT emisor</th><th class="num">Total</th><th>Estado</th></tr></thead>
    <tbody>{sample_rows()}</tbody>
  </table>
  <p class="caption">Salida real del programa sobre los 4 ejemplos incluidos en el repositorio. La misma factura A está como foto y como PDF, así que la segunda copia queda como repetida.</p>
</section>

<section class="slide">
  <div class="eyebrow">Cómo funciona</div>
  <h2>Cuatro pasos, y el QR como ancla</h2>
  <div class="cards four">
    <div class="card"><div class="n">1</div><h3>Leer la página</h3><p>Lee el texto del PDF o, en fotos y escaneos, detecta la hoja, la endereza y aplica OCR.</p></div>
    <div class="card"><div class="n">2</div><h3>Leer el QR de ARCA</h3><p>Trae CUIT, número, fecha, total y CAE sin depender del OCR, y cada dato se compara con lo impreso.</p></div>
    <div class="card"><div class="n">3</div><h3>Extraer el resto</h3><p>Reglas que toleran los errores típicos del OCR y los distintos diseños de factura.</p></div>
    <div class="card"><div class="n">4</div><h3>Validar y decidir</h3><p>Si pasa todos los controles, se carga. Si no, va a revisión con el motivo.</p></div>
  </div>
  <p class="note">Opcional: los casos dudosos se le pueden pasar a Claude, y su respuesta vuelve a pasar por los mismos controles.</p>
</section>

<section class="slide">
  <div class="eyebrow">Controles</div>
  <h2>Qué revisa antes de aprobar un comprobante</h2>
  <table class="checks">
    <tr><td>Dígito verificador del CUIT</td><td>Errores de tipeo o de lectura en el CUIT</td></tr>
    <tr><td>Neto + IVA + otros tributos = total</td><td>Totales mal calculados o montos mal leídos</td></tr>
    <tr><td>IVA coherente con el neto (21 %, máximo 27 %)</td><td>IVA mal leído o mal facturado</td></tr>
    <tr><td>IVA contenido en Factura B (Ley 27.743)</td><td>Dato obligatorio faltante</td></tr>
    <tr><td>CAE de 14 dígitos, vigente a la fecha de emisión</td><td>Códigos de autorización inválidos</td></tr>
    <tr><td>Cada dato del QR contra el texto impreso</td><td>Documentos adulterados o mal leídos</td></tr>
    <tr><td>Etiqueta impresa sin monto legible</td><td>Datos que el OCR perdió sin avisar</td></tr>
    <tr><td>La misma factura dos veces en el lote</td><td>Cargas duplicadas (el PDF del mail y la foto)</td></tr>
    <tr class="fraud"><td>QR que apunta a un dominio que no es de ARCA</td><td>Facturas falsas o simuladas</td></tr>
    <tr class="fraud"><td>Leyenda «Comprobante No Autorizado»</td><td>Borradores o comprobantes sin validez</td></tr>
  </table>
</section>

<section class="slide">
  <div class="eyebrow">Cómo lo medí</div>
  <h2>Dos pruebas: una propia y una independiente</h2>
  <div class="cards two">
    <div class="card"><h3>Set propio · {docs_syn} documentos</h3>
      <p>50 facturas ficticias en 3 diseños (A, B, C y notas de crédito). Cada una como PDF, escaneo y foto de celular simulada.</p>
      <p>5 facturas «trampa» con errores a propósito: totales que no cierran, CUIT inválido, QR distinto de lo impreso.</p></div>
    <div class="card"><h3>Benchmark independiente · 200 facturas</h3>
      <p>Dataset público hecho por otra persona (Hugging Face), con 5 diseños que el sistema nunca había visto.</p>
      <p>Ajusté las reglas mirando 55 y reporto los resultados sobre las <b>{ext_img['documentos']} restantes</b>, sin mirarlas antes. También las probé como fotos de celular (esa prueba necesitó una segunda corrida; lo cuento más adelante).</p></div>
  </div>
</section>

<section class="slide">
  <div class="eyebrow">Resultados · set propio</div>
  <h2>Ningún comprobante aprobado con un dato mal</h2>
  <div class="kpis">
    {kpi(str(silent), "documentos aprobados<br>con algún dato mal", "good")}
    {kpi(f"{sum(int(t.split('/')[0]) for t in traps)}/{sum(int(t.split('/')[1]) for t in traps)}", "trampas detectadas<br>(5 por formato)", "good")}
    {kpi(pct(syn['pdf']['aprobados_automaticamente_pct']) + " %", "de los PDF normales<br>aprobados solos")}
    {kpi(pct(syn['foto']['aprobados_automaticamente_pct']) + " %", "de las fotos normales<br>aprobadas solas")}
  </div>
  <img class="chart" src="../figures/resultados.png" alt="Resultado por tipo de captura">
</section>

<section class="slide">
  <div class="eyebrow">Resultados · benchmark independiente</div>
  <h2>Diseños nunca vistos: {pct(ext_img['precision_campos_global'])} % de los datos correctos</h2>
  <div class="kpis">
    {kpi(pct(ext_img['precision_campos_global']) + " %", "de los campos correctos<br>(imagen)")}
    {kpi(pct(ext_foto['precision_campos_global']) + " %", "de los campos correctos<br>(foto de celular, 2.ª corrida)")}
    {kpi(pct(ext_img['documentos_100_correctos_pct']) + " %", "de las facturas con<br>todos los datos bien")}
    {kpi(pct(ext_img['marcados_para_revision_pct']) + " %", "marcadas como sospechosas<br>(ver nota)", "warn")}
  </div>
  <div class="split">
    <table class="small">
      <thead><tr><th>Diseño</th><th class="num">Imagen</th><th class="num">Foto</th></tr></thead>
      <tbody>{tmpl_rows}</tbody>
      <caption>Facturas con todos los datos correctos, por diseño</caption>
    </table>
    <div class="callout">
      <b>Por qué todas fueron a revisión, y está bien.</b> Las facturas del dataset imprimen
      «Comprobante No Autorizado» y su QR apunta a un dominio que no es de ARCA. Una factura así
      no debe cargarse sola: los controles antifraude la frenaron en el {pct(ext_img['marcados_para_revision_pct'])} % de los casos.
    </div>
  </div>
</section>

<section class="slide">
  <div class="eyebrow">Qué aprendí</div>
  <h2>Lo que salió mal en el camino y cómo lo corregí</h2>
  <div class="cards three tight">
    <div class="card"><h3>La prueba engañaba</h3><p>El QR se leía en solo 24 % de las fotos. La culpa era de la simulación (fotos de 1300 px), no del lector. Ahora son de 1600 px, como en WhatsApp.</p></div>
    <div class="card"><h3>Trampas mal repartidas</h3><p>Todas las facturas trampa habían caído en el mismo diseño, así que detectarlas no probaba nada. Ahora están repartidas.</p></div>
    <div class="card"><h3>Separar ajuste y prueba</h3><p>Ajusté reglas con 55 facturas externas y medí sobre las otras {ext_img['documentos']}. Las 5 que había abierto antes de separar pasaron a ajuste.</p></div>
    <div class="card"><h3>La foto a ciegas falló</h3><p>Primera corrida: {pct(ext_foto_first['precision_campos_global'])} % de campos bien. El OCR leía «($)» como «(S)». Lo corregí y volví a medir: {pct(ext_foto['precision_campos_global'])} %, ya no a ciegas.</p></div>
    <div class="card"><h3>Un dato perdido no puede pasar</h3><p>Ese error dejaba neto e IVA vacíos sin frenar la factura. Agregué un control: si la etiqueta está y el monto no, va a revisión.</p></div>
    <div class="card"><h3>Lo que el QR no cubre</h3><p>Los nombres no están en el QR: un error de OCR como «Disenio» por «Diseño» no lo detecta ningún control. Es un límite conocido.</p></div>
  </div>
</section>

<section class="slide">
  <div class="eyebrow">Límites y próximos pasos</div>
  <h2>Qué falta para usarlo con un cliente real</h2>
  <ul class="steps">
    <li><b>Medir con facturas reales del cliente.</b> Los dos sets de prueba son sintéticos. El paso uno con cualquier empresa es probarlo sobre 50 a 100 comprobantes suyos.</li>
    <li><b>Remitos y detalle de ítems.</b> Hoy lee encabezado y totales de facturas y notas. No encontré un dataset público de remitos para medirlo.</li>
    <li><b>Nombres con tildes.</b> El OCR a veces lee «ó» como «6» («C6rdoba»). Una corrección de números dentro de palabras resolvería la mayoría de los errores que quedan.</li>
    <li><b>Medir la ayuda de Claude.</b> Está programada y probada con tests, pero falta medirla con una clave de API.</li>
    <li><b>Integración.</b> Volcar los datos aprobados a Google Sheets o al sistema contable, y la cola de revisión a una planilla compartida.</li>
  </ul>
</section>

<section class="slide end">
  <h2>Gracias</h2>
  <p class="lead">Código, resultados y demo:<br><b>github.com/TomaasGonzalezzz/Argentine-Invoice-Extraction</b></p>
  <p class="author">Tomás González<br><span>Ingeniería en IA, UdeSA · Técnico electromecánico<br>linkedin.com/in/tomas-gonzalez-213a511b5</span></p>
  <p class="tiny">Stack: Python, OpenCV, RapidOCR, zxing-cpp, pdfplumber, pydantic.<br>
  Todas las facturas mostradas son ficticias.</p>
</section>
"""

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
@page { size: 1280px 720px; margin: 0; }
* { box-sizing: border-box; }
:root { --ink:#0f172a; --ink2:#475569; --muted:#94a3b8; --line:#e2e8f0; --bg:#ffffff; --soft:#f8fafc;
        --accent:#1f5fbf; --accent-soft:#e8f0fc; --good:#0b8a3e; --good-soft:#e6f6ec; --warn:#b45309; --warn-soft:#fdf3e4;
        --bad:#c2410c; }
html, body { margin:0; padding:0; background:#fff; }
body { font-family: Inter, "Segoe UI", system-ui, sans-serif; color: var(--ink); -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.slide { width:1280px; height:720px; padding:64px 80px 56px; position:relative; overflow:hidden; page-break-after:always; background:var(--bg); }
.slide::before { content:""; position:absolute; left:0; top:0; bottom:0; width:10px; background:var(--accent); }
.slide::after { content:"Lectura automática de facturas · Tomás González"; position:absolute; left:80px; bottom:22px; font-size:12px; color:var(--muted); }
.eyebrow { font-size:15px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); margin-bottom:10px; }
h1 { font-size:54px; line-height:1.08; margin:0 0 20px; font-weight:800; letter-spacing:-.02em; }
h2 { font-size:36px; line-height:1.15; margin:0 0 32px; font-weight:700; letter-spacing:-.01em; }
h3 { font-size:20px; margin:0 0 10px; font-weight:700; }
p { font-size:18px; line-height:1.5; color:var(--ink2); margin:0 0 12px; }
.lead { font-size:22px; color:var(--ink2); }
.author { margin-top:36px; font-size:20px; color:var(--ink); font-weight:600; }
.author span { font-weight:400; color:var(--ink2); font-size:17px; }
.cover { display:grid; grid-template-columns: 520px 1fr; gap:40px; align-items:center; padding-right:48px; }
.cover-img { width:100%; border-radius:14px; box-shadow:0 18px 40px rgba(15,23,42,.14); border:1px solid var(--line); }
.cards { display:grid; gap:22px; }
.cards.three { grid-template-columns: repeat(3, 1fr); }
.cards.four { grid-template-columns: repeat(4, 1fr); }
.cards.two { grid-template-columns: repeat(2, 1fr); }
.card { background:var(--soft); border:1px solid var(--line); border-radius:14px; padding:24px 24px 18px; }
.card p { font-size:17px; }
.cards.tight { gap:16px; }
.cards.tight .card { padding:18px 20px 12px; }
.cards.tight h3 { font-size:18px; }
.cards.tight p { font-size:15.5px; line-height:1.45; }
.checks td { padding:9px 14px !important; font-size:17px !important; }
.n { width:36px; height:36px; border-radius:50%; background:var(--accent); color:#fff; font-weight:700; display:flex; align-items:center; justify-content:center; margin-bottom:14px; }
.note { margin-top:28px; font-size:19px; color:var(--ink); background:var(--accent-soft); border-radius:10px; padding:16px 20px; }
.flow { display:flex; align-items:stretch; gap:18px; margin-bottom:26px; }
.box { flex:1; border:1px solid var(--line); border-radius:12px; padding:16px 20px; background:var(--soft); }
.box b { display:block; font-size:18px; margin-bottom:6px; }
.box span { font-size:16px; color:var(--ink2); line-height:1.45; }
.box.accent { background:var(--accent); border-color:var(--accent); }
.box.accent b, .box.accent span { color:#fff; }
.arrow { display:flex; align-items:center; font-size:30px; color:var(--muted); }
table { border-collapse:collapse; width:100%; }
.out th, .out td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); font-size:15px; }
.out th { color:var(--ink2); font-weight:600; font-size:13px; text-transform:uppercase; letter-spacing:.04em; }
.mono { font-family:"JetBrains Mono", Consolas, monospace; font-size:14px; }
.num { text-align:right !important; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; font-size:13px; font-weight:600; }
.badge.ok { background:var(--good-soft); color:var(--good); }
.badge.rev { background:var(--warn-soft); color:var(--warn); }
.caption { font-size:14px; color:var(--muted); margin-top:10px; }
.checks td { padding:11px 14px; font-size:18px; border-bottom:1px solid var(--line); }
.checks td:first-child { font-weight:600; width:55%; }
.checks td:last-child { color:var(--ink2); }
.checks tr.fraud td { background:var(--warn-soft); }
.checks tr.fraud td:first-child::before { content:"Antifraude · "; color:var(--warn); font-weight:700; }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:18px; margin-bottom:22px; }
.kpi { border:1px solid var(--line); border-radius:12px; padding:16px 20px; background:var(--soft); }
.kpi .v { font-size:38px; font-weight:800; letter-spacing:-.02em; color:var(--ink); }
.kpi .l { font-size:14px; color:var(--ink2); line-height:1.35; margin-top:4px; }
.kpi.good .v { color:var(--good); }
.kpi.warn .v { color:var(--warn); }
.chart { width:100%; max-height:300px; object-fit:contain; }
.split { display:grid; grid-template-columns: 1fr 1.1fr; gap:28px; align-items:start; }
.small th, .small td { padding:7px 10px; font-size:15px; border-bottom:1px solid var(--line); text-align:left; }
.small caption { caption-side:bottom; font-size:13px; color:var(--muted); text-align:left; padding-top:8px; }
.callout { background:var(--warn-soft); border-radius:12px; padding:18px 22px; font-size:17px; line-height:1.5; color:var(--ink); }
.steps { margin:0; padding-left:0; list-style:none; }
.steps li { font-size:19px; line-height:1.5; color:var(--ink2); padding:14px 0 14px 28px; border-bottom:1px solid var(--line); position:relative; }
.steps li::before { content:""; position:absolute; left:0; top:24px; width:12px; height:12px; border-radius:3px; background:var(--accent); }
.steps b { color:var(--ink); }
.end { display:flex; flex-direction:column; justify-content:center; }
.end h2 { font-size:54px; }
.tiny { font-size:14px; color:var(--muted); margin-top:40px; }
"""

page = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Lectura automática de facturas argentinas</title>
<style>{CSS}</style></head><body>{SLIDES}</body></html>"""

out_html = DOCS / "presentacion.html"
out_html.write_text(page, "utf-8")

browser = next((p for p in [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
] if p and Path(p).exists()), None)
if browser is None:
    raise SystemExit("No encontré Edge ni Chrome para exportar el PDF; abrí docs/presentacion.html e imprimilo.")
pdf = DOCS / "presentacion.pdf"
subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                "--virtual-time-budget=8000", f"--print-to-pdf={pdf}", out_html.as_uri()], check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(f"PDF: {pdf}")
