"""PDF/photo -> validated Invoice, with a review flag and the reasons behind it."""

from __future__ import annotations

import time
from pathlib import Path

from . import reader, rules, validate
from .schema import Invoice, Result

FIELDS = list(Invoice.model_fields)


def _review_reasons(inv: Invoice, page: reader.Page, sources: dict[str, str], text_from_ocr: bool) -> list[str]:
    # real invoices say "Comprobante Autorizado"; previews and fakes often say the opposite
    not_authorized = any("noautorizado" in rules.compact(l) for l in page.lines)
    issues = validate.merge_qr(inv, page.qr, sources, text_from_ocr=text_from_ocr)
    issues += validate.check(inv, has_qr=page.qr is not None, not_authorized_legend=not_authorized)
    issues += [f"etiqueta_sin_valor:{f}" for f in rules.labels_without_value(page.lines, inv)]
    return issues


def _qr_total(qr: dict | None) -> float | None:
    try:
        return round(float(qr["importe"]), 2) if qr else None
    except (KeyError, TypeError, ValueError):
        return None


def _qr_cuit(qr: dict | None) -> str | None:
    try:
        return f"{int(qr['cuit']):011d}" if qr else None
    except (KeyError, TypeError, ValueError):
        return None


def process(path: Path, llm=None) -> Result:
    return analyze(path, llm)[0]


def analyze(path: Path, llm=None) -> tuple[Result, reader.Page]:
    """process(), plus the page that was read (image, text lines with positions, QR)."""
    t0 = time.perf_counter()
    page = reader.load(Path(path))
    inv = rules.extract(page.lines)
    src_tag = "texto" if page.text_source == "pdf" else "ocr"
    sources = {f: src_tag for f in FIELDS if getattr(inv, f) is not None}
    issues = _review_reasons(inv, page, sources, text_from_ocr=page.text_source == "ocr")

    if issues and llm is not None:
        reading = llm.extract(page.image)
        if reading is not None:
            llm_sources: dict[str, str] = {}
            for f in FIELDS:
                value = getattr(reading, f)
                if value is None and getattr(inv, f) is not None:
                    setattr(reading, f, getattr(inv, f))  # keep what the rules found
                    llm_sources[f] = sources.get(f, src_tag)
                elif value is not None:
                    llm_sources[f] = "llm"
            # the model reads the printed page, so treat it like a clean text layer
            new_issues = _review_reasons(reading, page, llm_sources, text_from_ocr=False)
            inv, sources, issues = reading, llm_sources, new_issues

    result = Result(
        archivo=Path(path).name,
        estado="revisar" if issues else "ok",
        motivos=issues,
        factura=inv,
        fuentes=sources,
        qr_leido=page.qr is not None,
        qr_total=_qr_total(page.qr),
        qr_cuit=_qr_cuit(page.qr),
        texto_origen=page.text_source,
        segundos=round(time.perf_counter() - t0, 2),
    )
    return result, page
