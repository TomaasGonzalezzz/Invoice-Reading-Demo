"""Local web demo: drop invoices, watch the spreadsheet fill, review what didn't pass, export to Excel.

Everything runs on this computer; no document is sent anywhere.
"""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook

from ..export import MONEY_FMT, to_csv, to_xlsx
from ..revision import FAIL, Row, batch_summary, review_batch
from ..schema import Result
from . import worker

STATIC = Path(__file__).parent / "static"
DEMO_DIR = Path(__file__).resolve().parents[3] / "demo" / "lote"
ALLOWED = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

AMOUNT_FIELDS = ["neto_gravado", "iva_21", "iva_10_5", "iva_total", "otros_tributos", "total"]


@dataclass
class Doc:
    id: str
    orden: int
    data: dict  # what worker.work returned

    @property
    def result(self) -> Result:
        return Result.model_validate(self.data["result"])


@dataclass
class Batch:
    id: str
    dir: Path
    docs: dict[str, Doc] = field(default_factory=dict)

    def ordered(self) -> list[Doc]:
        return sorted(self.docs.values(), key=lambda d: d.orden)


BATCHES: dict[str, Batch] = {}
STATE: dict[str, object] = {"workers": 2}


def problem_fields(row: Row) -> list[str]:
    """Fields to paint red on the image, from the checks that failed."""
    failed = {c.id for c in row.checks if c.estado == FAIL}
    codes = row.result.motivos
    out: set[str] = set()
    if "cuentas" in failed:
        out.update(AMOUNT_FIELDS)
    if "cuit_emisor_invalido" in codes:
        out.add("emisor_cuit")
    if "cuit_receptor_invalido" in codes:
        out.add("receptor_cuit")
    for c in codes:
        if c.startswith("qr_no_coincide:"):
            name = c.split(":", 1)[1].split(" ")[0]
            out.add("comprobante" if name in ("punto_venta", "numero") else name)
            out.add("qr")
        elif c.startswith("qr_no_oficial"):
            out.add("qr")
    if "cae" in failed:
        out.update(["cae", "cae_vencimiento"])
    if "duplicada" in failed:
        out.add("comprobante")
    return sorted(out)


def state(batch: Batch) -> dict:
    docs = batch.ordered()
    rows = review_batch([d.result for d in docs])
    out = []
    for d, row in zip(docs, rows):
        r = row.result
        f = r.factura
        item = {
            "id": d.id, "orden": d.orden, "archivo": r.archivo, "captura": d.data["captura"],
            **row.as_dict(),
            "duplicada_de": docs[row.duplicate_of].id if row.duplicate_of is not None else None,
            "factura": f.model_dump(), "qr_leido": r.qr_leido,
            "campos": d.data["campos"], "problemas": problem_fields(row),
            "imagenes": {k: {**v, "url": f"/api/lotes/{batch.id}/imagenes/{d.id}/{k}.jpg"}
                         for k, v in d.data["imagenes"].items()},
            "cajas": d.data["cajas"],
        }
        out.append(item)
    return {"id": batch.id, "documentos": out, "resumen": batch_summary(rows)}


def rows_of(batch: Batch) -> list[Row]:
    return review_batch([d.result for d in batch.ordered()])


def _new_pool() -> ThreadPoolExecutor:
    # Threads, not processes: OCR, OpenCV and the QR reader release the GIL, each thread keeps its
    # own OCR engine, and one process uses less memory than several.
    pool = ThreadPoolExecutor(STATE["workers"], thread_name_prefix="lector")
    pool.submit(worker.warmup)  # load the OCR model before the first document arrives
    return pool


async def _run(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(STATE["pool"], fn, *args)


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["pool"] = _new_pool()
    STATE["root"] = Path(tempfile.mkdtemp(prefix="facturas-demo-"))
    try:
        yield
    finally:
        STATE["pool"].shutdown(cancel_futures=True)
        shutil.rmtree(STATE["root"], ignore_errors=True)


app = FastAPI(title="Lector de facturas", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.post("/api/lotes")
def new_batch() -> dict:
    bid = uuid.uuid4().hex[:10]
    d = STATE["root"] / bid
    d.mkdir(parents=True)
    BATCHES[bid] = Batch(bid, d)
    return {"id": bid}


def _batch(bid: str) -> Batch:
    if bid not in BATCHES:
        raise HTTPException(404, "Lote inexistente")
    return BATCHES[bid]


@app.post("/api/lotes/{bid}/documentos")
async def add_document(bid: str, archivo: UploadFile = File(...), orden: int = Form(0)) -> JSONResponse:
    batch = _batch(bid)
    name = Path(archivo.filename or "documento").name
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(400, "Formato no soportado: subí PDF, JPG o PNG.")
    doc_id = uuid.uuid4().hex[:8]
    path = batch.dir / f"{doc_id}{Path(name).suffix.lower()}"
    path.write_bytes(await archivo.read())
    named = batch.dir / doc_id
    named.mkdir()
    shown = named / re.sub(r'[\\/:*?"<>|]', "_", name)  # keep the original name for the results
    shutil.move(path, shown)
    data = await _run(worker.work, str(shown), str(batch.dir), doc_id)
    batch.docs[doc_id] = Doc(doc_id, orden, data)
    return JSONResponse({"documento": doc_id, "lote": state(batch)})


@app.get("/api/lotes/{bid}")
def get_batch(bid: str) -> dict:
    return state(_batch(bid))


@app.get("/api/lotes/{bid}/imagenes/{doc_id}/{vista}.jpg")
def image(bid: str, doc_id: str, vista: str) -> FileResponse:
    batch = _batch(bid)
    if vista not in ("original", "enderezada") or doc_id not in batch.docs:
        raise HTTPException(404)
    return FileResponse(batch.dir / f"{doc_id}_{vista}.jpg")


@app.get("/api/lotes/{bid}/excel")
def excel(bid: str) -> Response:
    data = to_xlsx(rows_of(_batch(bid)))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="comprobantes.xlsx"'})


@app.get("/api/lotes/{bid}/csv")
def csv_file(bid: str) -> Response:
    return Response(to_csv(rows_of(_batch(bid))), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="comprobantes_para_importar.csv"'})


def _cell_text(cell) -> str:
    v = cell.value
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) or (isinstance(v, int) and cell.number_format == MONEY_FMT):
        return f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return str(v)


@app.get("/api/lotes/{bid}/excel/vista")
def excel_preview(bid: str) -> dict:
    """The generated workbook read back, so the page can show exactly what the file contains."""
    wb = load_workbook(io.BytesIO(to_xlsx(rows_of(_batch(bid)))))
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(max_row=min(ws.max_row, 60)):
            cells = [_cell_text(c) for c in row]
            rows.append({"celdas": cells,
                         "numericas": [isinstance(c.value, (int, float)) and not isinstance(c.value, bool) for c in row],
                         "resaltada": ws.title == "Comprobantes" and cells[0] == "Para revisar"})
        widths = [ws.column_dimensions[c.column_letter].width or 10 for c in next(ws.iter_rows(max_row=1))]
        sheets.append({"nombre": ws.title, "filas": rows, "anchos": widths})
    return {"archivo": "comprobantes.xlsx", "hojas": sheets}


@app.get("/api/ejemplo")
def demo_files() -> dict:
    files = sorted(p.name for p in DEMO_DIR.iterdir() if p.suffix.lower() in ALLOWED) if DEMO_DIR.exists() else []
    order = [n for n in _demo_order() if n in files] + [n for n in files if n not in _demo_order()]
    return {"archivos": [{"nombre": n, "url": f"/ejemplo/{n}"} for n in order]}


def _demo_order() -> list[str]:
    from ..demo_set import PLAN

    return [p[0] for p in PLAN]


@app.get("/ejemplo/{name}")
def demo_file(name: str) -> FileResponse:
    path = (DEMO_DIR / name).resolve()
    if path.parent != DEMO_DIR.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
