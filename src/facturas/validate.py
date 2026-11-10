"""Checks that decide whether a document can be loaded automatically or needs a person."""

from __future__ import annotations

from datetime import date

from .afip import VOUCHER_TYPES, is_valid_cuit, only_digits
from .schema import Invoice

TOL = 0.05  # pesos of rounding slack


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _as_digits(v) -> str:
    return only_digits(f"{v:.2f}" if isinstance(v, float) else str(v))


def merge_qr(inv: Invoice, qr: dict | None, sources: dict[str, str], text_from_ocr: bool) -> list[str]:
    """Fill fields from the QR and report disagreements with the printed text.

    With a PDF text layer, any disagreement is real and goes to review. With OCR, a difference of
    one or two characters is almost always a misread, so the QR value is kept; a larger difference
    means the document itself is inconsistent.
    """
    if not qr:
        return []
    issues = []
    if not qr.get("_oficial", True):
        issues.append(f"qr_no_oficial ({qr.get('_host')})")
    qr_fields: dict[str, object] = {}
    try:
        qr_fields["fecha_emision"] = str(qr["fecha"])[:10]
        qr_fields["emisor_cuit"] = f"{int(qr['cuit']):011d}"
        qr_fields["punto_venta"] = int(qr["ptoVta"])
        qr_fields["numero"] = int(qr["nroCmp"])
        qr_fields["total"] = round(float(qr["importe"]), 2)
        qr_fields["cae"] = f"{int(qr['codAut']):014d}"
        kind_letter = VOUCHER_TYPES.get(int(qr["tipoCmp"]))
        if kind_letter:
            qr_fields["tipo"], qr_fields["letra"] = kind_letter
        currency = {"PES": "PES", "DOL": "DOL", "060": "EUR", "EUR": "EUR"}.get(str(qr.get("moneda")))
        if currency:
            qr_fields["moneda"] = currency
        if int(qr.get("tipoDocRec", 0)) == 80:
            qr_fields["receptor_cuit"] = f"{int(qr['nroDocRec']):011d}"
    except (KeyError, ValueError, TypeError):
        return ["qr_ilegible"]

    for name, qv in qr_fields.items():
        tv = getattr(inv, name)
        if tv is not None and not _same(tv, qv):
            if text_from_ocr and edit_distance(_as_digits(tv), _as_digits(qv)) <= 2:
                # probably a misread, but a one-digit typo on the paper looks the same: keep the
                # QR value (what ARCA registered) and still ask a person to look
                setattr(inv, name, qv)
                sources[name] = "qr (texto distinto)"
                issues.append(f"qr_no_coincide:{name} (posible error de lectura)")
                continue
            issues.append(f"qr_no_coincide:{name}")
            continue  # keep the printed value; a person decides which one is right
        setattr(inv, name, qv)
        sources[name] = "qr" if tv is None else "qr+texto"
    return issues


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= TOL
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


def check(inv: Invoice, has_qr: bool, today: date | None = None, not_authorized_legend: bool = False) -> list[str]:
    today = today or date.today()
    issues = []
    required = ["tipo", "letra", "punto_venta", "numero", "fecha_emision", "emisor_cuit", "total", "cae"]
    if inv.letra in ("A", "M"):
        required += ["neto_gravado", "receptor_cuit"]
    if inv.letra == "B" and (inv.fecha_emision or "") >= "2025-04-01":
        required.append("iva_contenido")  # Ley 27.743, mandatory on Factura B since April 2025
    issues += [f"falta:{f}" for f in required if getattr(inv, f) in (None, "")]

    if inv.emisor_cuit and not is_valid_cuit(inv.emisor_cuit):
        issues.append("cuit_emisor_invalido")
    if inv.receptor_cuit and not is_valid_cuit(inv.receptor_cuit):
        issues.append("cuit_receptor_invalido")
    if inv.cae and len(only_digits(inv.cae)) != 14:
        issues.append("cae_formato")

    iva = inv.iva_total if inv.iva_total is not None else (inv.iva_21 or 0) + (inv.iva_10_5 or 0)
    itemized = inv.letra in ("A", "M") or inv.iva_total is not None or inv.iva_21 is not None
    if inv.neto_gravado is not None and inv.total is not None and (itemized or inv.letra == "C"):
        if abs(inv.neto_gravado + iva + (inv.otros_tributos or 0) - inv.total) > TOL:
            issues.append("total_no_cierra")
    if inv.neto_gravado and inv.iva_21 and not inv.iva_10_5 and abs(inv.iva_21 - 0.21 * inv.neto_gravado) > TOL:
        issues.append("iva_21_no_coincide")
    if inv.neto_gravado and iva > 0.27 * inv.neto_gravado + TOL:
        issues.append("iva_mayor_al_27")  # no Argentine rate is higher than 27 %
    if inv.letra in ("A", "M") and not iva:
        issues.append("factura_a_sin_iva")
    if inv.letra == "B" and inv.iva_contenido is not None and inv.total:
        if not 0 < inv.iva_contenido <= inv.total * 0.21 / 1.21 + TOL:
            issues.append("iva_contenido_fuera_de_rango")
    if inv.letra == "C" and iva:
        issues.append("factura_c_con_iva")
    if not_authorized_legend:
        issues.append("leyenda_comprobante_no_autorizado")

    if inv.fecha_emision:
        f = date.fromisoformat(inv.fecha_emision)
        if f > today or f.year < 2000:
            issues.append("fecha_fuera_de_rango")
        if inv.cae_vencimiento and date.fromisoformat(inv.cae_vencimiento) < f:
            issues.append("cae_vence_antes_de_emision")
    if not has_qr:
        issues.append("sin_qr")
    return issues
