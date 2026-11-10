"""Runs in a worker thread: read one document and prepare everything the page shows about it."""

from __future__ import annotations

from pathlib import Path

import cv2

from ..locate import locate, to_flattened
from ..pipeline import analyze
from ..revision import date_ar, money
from ..afip import format_cuit
from ..schema import Invoice, Result

MAX_SIDE = 1500  # images sent to the browser


def warmup() -> None:
    """Load the OCR model once, before the first document arrives."""
    from ..reader import _ocr_engine

    _ocr_engine()


def _save(img, path: Path) -> tuple[float, int, int]:
    h, w = img.shape[:2]
    s = min(1.0, MAX_SIDE / max(h, w))
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 86])
    return s, img.shape[1], img.shape[0]


def _scaled(boxes: dict, s: float) -> dict:
    return {k: [[[round(x * s, 1), round(y * s, 1)] for x, y in b] for b in v] for k, v in boxes.items()}


def capture_kind(path: Path, text_source: str, page_found: bool) -> str:
    if path.suffix.lower() == ".pdf":
        return "PDF" if text_source == "pdf" else "PDF escaneado"
    return "Foto" if page_found else "Escaneo"


def display_fields(inv: Invoice, sources: dict[str, str]) -> list[dict]:
    """The fields shown next to the image, each tied to the boxes with the same id."""

    def src(*names: str) -> str:
        tags = [sources.get(n, "") for n in names if sources.get(n)]
        if not tags:
            return ""
        if all(t in ("qr", "qr+texto") for t in tags):
            return "qr"
        if any(t.startswith("qr (") for t in tags):
            return "qr_corrigio"
        if any(t in ("qr", "qr+texto") for t in tags):
            return "qr"
        return "pdf" if tags[0] == "texto" else ("llm" if tags[0] == "llm" else "imagen")

    out = []
    if inv.punto_venta is not None and inv.numero is not None:
        comp = f"{inv.tipo or ''} {inv.letra or ''} {inv.punto_venta:05d}-{inv.numero:08d}".strip()
        out.append({"id": "comprobante", "etiqueta": "Comprobante", "valor": comp, "fuente": src("punto_venta", "numero")})
    rows = [
        ("fecha_emision", "Fecha de emisión", date_ar(inv.fecha_emision) if inv.fecha_emision else None),
        ("emisor_razon_social", "Proveedor", inv.emisor_razon_social),
        ("emisor_cuit", "CUIT del proveedor", format_cuit(inv.emisor_cuit) if inv.emisor_cuit else None),
        ("receptor_razon_social", "Cliente", inv.receptor_razon_social),
        ("receptor_cuit", "CUIT del cliente", format_cuit(inv.receptor_cuit) if inv.receptor_cuit else None),
        ("neto_gravado", "Neto gravado", money(inv.neto_gravado) if inv.neto_gravado is not None else None),
        ("iva_21", "IVA 21 %", money(inv.iva_21) if inv.iva_21 else None),
        ("iva_10_5", "IVA 10,5 %", money(inv.iva_10_5) if inv.iva_10_5 else None),
        ("iva_total", "IVA", money(inv.iva_total) if inv.iva_total and not (inv.iva_21 or inv.iva_10_5) else None),
        ("otros_tributos", "Otros tributos", money(inv.otros_tributos) if inv.otros_tributos else None),
        ("iva_contenido", "IVA contenido", money(inv.iva_contenido) if inv.iva_contenido else None),
        ("total", "Total", money(inv.total) if inv.total is not None else None),
        ("cae", "CAE", inv.cae),
        ("cae_vencimiento", "Vencimiento del CAE", date_ar(inv.cae_vencimiento) if inv.cae_vencimiento else None),
    ]
    for fid, label, value in rows:
        if value:
            out.append({"id": fid, "etiqueta": label, "valor": value, "fuente": src(fid)})
    return out


def work(path: str, outdir: str, doc_id: str) -> dict:
    path_, out = Path(path), Path(outdir)
    try:
        result, page = analyze(path_)
    except Exception as exc:  # a broken or unsupported file must not stop the batch
        r = Result(archivo=path_.name, estado="revisar", motivos=["no_se_pudo_abrir"], factura=Invoice())
        return {"result": r.model_dump(), "error": str(exc), "captura": "Archivo", "campos": [],
                "imagenes": {}, "cajas": {}}

    boxes = locate(page, result.factura)
    images, cajas = {}, {}
    s, w, h = _save(page.image, out / f"{doc_id}_original.jpg")
    images["original"] = {"w": w, "h": h}
    cajas["original"] = _scaled(boxes, s)
    if page.flattened is not None:
        s, w, h = _save(page.flattened, out / f"{doc_id}_enderezada.jpg")
        images["enderezada"] = {"w": w, "h": h}
        cajas["enderezada"] = _scaled(to_flattened(page, boxes), s)
    return {
        "result": result.model_dump(),
        "captura": capture_kind(path_, page.text_source, page.page_found),
        "campos": display_fields(result.factura, result.fuentes),
        "imagenes": images,
        "cajas": cajas,
    }
