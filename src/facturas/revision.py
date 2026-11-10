"""Plain-language checks for a batch of invoices: what passed, what didn't and why.

The validator speaks in short codes ("total_no_cierra", "qr_no_coincide:total"). This module groups
them into the handful of checks a person cares about, explains each one in Spanish, and adds the
one check that needs the whole batch: the same invoice loaded twice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .afip import format_cuit, is_valid_cuit
from .schema import Result

FIELD_NAMES = {
    "tipo": "tipo de comprobante", "letra": "letra", "punto_venta": "punto de venta", "numero": "número",
    "fecha_emision": "fecha de emisión", "emisor_cuit": "CUIT del emisor", "receptor_cuit": "CUIT del receptor",
    "emisor_razon_social": "razón social del emisor", "receptor_razon_social": "razón social del receptor",
    "neto_gravado": "neto gravado", "iva_total": "IVA", "iva_21": "IVA 21 %", "iva_10_5": "IVA 10,5 %",
    "otros_tributos": "otros tributos", "iva_contenido": "IVA contenido", "total": "total", "cae": "CAE",
    "cae_vencimiento": "vencimiento del CAE", "moneda": "moneda",
}
ESSENTIAL = ("punto_venta", "numero", "fecha_emision", "emisor_cuit", "total", "cae")

OK, FAIL, NA = "ok", "error", "no_aplica"


@dataclass
class Check:
    id: str
    titulo: str
    estado: str  # ok | error | no_aplica
    detalle: str


def money(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"-$ {s}" if v < 0 else f"$ {s}"  # no line break after the sign


def date_ar(iso: str | None) -> str:
    if not iso:
        return "—"
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def invoice_key(r: Result) -> tuple | None:
    """What identifies an invoice: issuer, type, letter, point of sale and number."""
    f = r.factura
    if not f.emisor_cuit or f.punto_venta is None or f.numero is None:
        return None
    return (f.emisor_cuit, f.tipo, f.letra, f.punto_venta, f.numero)


def find_duplicates(results: list[Result]) -> dict[int, int]:
    """Index of each repeated invoice -> index of the first copy in the batch."""
    first: dict[tuple, int] = {}
    dups: dict[int, int] = {}
    for i, r in enumerate(results):
        key = invoice_key(r)
        if key is None:
            continue
        if key in first:
            dups[i] = first[key]
        else:
            first[key] = i
    return dups


def unreadable(r: Result) -> bool:
    """Nothing usable came out: no QR and most of the essential fields missing."""
    missing = sum(getattr(r.factura, f) in (None, "") for f in ESSENTIAL)
    return not r.qr_leido and missing >= 4


def _field_list(names: list[str]) -> str:
    names = [FIELD_NAMES.get(n, n) for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def checks(r: Result, duplicate_of: str | None = None) -> list[Check]:
    """The checks shown to a person for one invoice, in a fixed order."""
    f, codes = r.factura, r.motivos
    out: list[Check] = []

    # 1. Reading
    if r.motivos == ["no_se_pudo_abrir"]:
        out.append(Check("lectura", "Datos completos", FAIL, "El archivo no se pudo abrir: está dañado o no es una factura."))
        return out
    missing = [c.split(":", 1)[1] for c in codes if c.startswith("falta:")]
    blank = [c.split(":", 1)[1] for c in codes if c.startswith("etiqueta_sin_valor:")]
    if unreadable(r):
        out.append(Check("lectura", "Datos completos", FAIL,
                         "No se pudo leer el comprobante: la imagen está muy borrosa, oscura o cortada. "
                         "Conviene pedir otra foto o el PDF."))
        return out
    if missing or blank:
        parts = []
        if missing:
            parts.append(f"No se encontró {_field_list(missing)}.")
        if blank:
            parts.append(f"Se ve la etiqueta de {_field_list(blank)}, pero el monto no se pudo leer.")
        out.append(Check("lectura", "Datos completos", FAIL, " ".join(parts)))
    elif "fecha_fuera_de_rango" in codes:
        out.append(Check("lectura", "Datos completos", FAIL, f"La fecha de emisión ({date_ar(f.fecha_emision)}) no es posible."))
    else:
        out.append(Check("lectura", "Datos completos", OK, "Se leyeron todos los datos obligatorios."))

    # 2. CUIT check digit
    bad = []
    if "cuit_emisor_invalido" in codes:
        bad.append(f"del emisor ({format_cuit(f.emisor_cuit)})")
    if "cuit_receptor_invalido" in codes:
        bad.append(f"del receptor ({format_cuit(f.receptor_cuit)})")
    if bad:
        subject = f"El CUIT {bad[0]} tiene" if len(bad) == 1 else f"Los CUIT {bad[0]} y {bad[1]} tienen"
        out.append(Check("cuit", "CUIT válido", FAIL,
                         f"{subject} mal el dígito verificador: está mal escrito o es falso."))
    elif f.emisor_cuit:
        cuits = [format_cuit(c) for c in (f.emisor_cuit, f.receptor_cuit) if c and is_valid_cuit(c)]
        out.append(Check("cuit", "CUIT válido", OK, f"Dígito verificador correcto ({', '.join(cuits)})."))
    else:
        out.append(Check("cuit", "CUIT válido", NA, "No se encontró el CUIT."))

    # 3. Arithmetic
    iva = f.iva_total if f.iva_total is not None else (f.iva_21 or 0) + (f.iva_10_5 or 0)
    if "total_no_cierra" in codes:
        expected = (f.neto_gravado or 0) + iva + (f.otros_tributos or 0)
        diff = f.total - expected
        detail = (f"Neto {money(f.neto_gravado)} + IVA {money(iva)}"
                  + (f" + otros {money(f.otros_tributos)}" if f.otros_tributos else "")
                  + f" = {money(expected)}, pero el total impreso es {money(f.total)} "
                  f"({'sobran' if diff > 0 else 'faltan'} {money(abs(diff))}).")
        out.append(Check("cuentas", "Neto + IVA = total", FAIL, detail))
    elif any(c in codes for c in ("iva_21_no_coincide", "iva_mayor_al_27", "factura_a_sin_iva",
                                  "factura_c_con_iva", "iva_contenido_fuera_de_rango")):
        why = {"iva_21_no_coincide": "El IVA 21 % no es el 21 % del neto.",
               "iva_mayor_al_27": "El IVA es mayor al 27 % del neto, y ninguna alícuota es tan alta.",
               "factura_a_sin_iva": "Es una factura A y no discrimina IVA.",
               "factura_c_con_iva": "Es una factura C y discrimina IVA.",
               "iva_contenido_fuera_de_rango": "El IVA contenido no es coherente con el total."}
        out.append(Check("cuentas", "Neto + IVA = total", FAIL, " ".join(why[c] for c in why if c in codes)))
    elif f.neto_gravado is not None and f.total is not None:
        out.append(Check("cuentas", "Neto + IVA = total", OK,
                         f"{money(f.neto_gravado)} + {money(iva)}"
                         + (f" + {money(f.otros_tributos)}" if f.otros_tributos else "")
                         + f" = {money(f.total)}."))
    else:
        out.append(Check("cuentas", "Neto + IVA = total", NA, f"La factura {f.letra or ''} no discrimina IVA.".replace("  ", " ")))

    # 4. QR vs printed text
    qr_codes = [c for c in codes if c.startswith(("qr_", "sin_qr"))]
    if "sin_qr" in codes:
        out.append(Check("qr", "QR de ARCA coincide con lo impreso", FAIL,
                         "No tiene QR de ARCA (obligatorio en facturas electrónicas) o no se pudo leer."))
    elif qr_codes:
        msgs = []
        for c in qr_codes:
            if c.startswith("qr_no_oficial"):
                host = c[c.find("(") + 1:-1] if "(" in c else ""
                msgs.append(f"El QR apunta a {host}, que no es un sitio de ARCA: posible factura falsa.")
            elif c == "qr_ilegible":
                msgs.append("El QR no tiene el formato de ARCA.")
            elif c.startswith("qr_no_coincide:"):
                name = c.split(":", 1)[1].split(" ")[0]
                if "posible error de lectura" in c:
                    msgs.append(f"El {FIELD_NAMES.get(name, name)} impreso difiere en uno o dos caracteres del QR; "
                                "puede ser un error de lectura. Se tomó el del QR.")
                elif name == "emisor_cuit" and r.qr_cuit and f.emisor_cuit:
                    msgs.append(f"El QR registra el CUIT {format_cuit(r.qr_cuit)} y la factura impresa dice "
                                f"{format_cuit(f.emisor_cuit)}.")
                elif name == "total" and r.qr_total is not None:
                    msgs.append(f"El QR registra un total de {money(r.qr_total)} y la factura impresa dice "
                                f"{money(f.total)}.")
                else:
                    msgs.append(f"El {FIELD_NAMES.get(name, name)} del QR no coincide con el impreso.")
        out.append(Check("qr", "QR de ARCA coincide con lo impreso", FAIL, " ".join(msgs)))
    else:
        out.append(Check("qr", "QR de ARCA coincide con lo impreso", OK,
                         "CUIT, punto de venta, número, fecha, total y CAE coinciden con el QR."))

    # 5. CAE
    if "leyenda_comprobante_no_autorizado" in codes:
        out.append(Check("cae", "CAE presente y vigente", FAIL, "La factura dice «Comprobante No Autorizado»: no tiene validez fiscal."))
    elif "falta:cae" in codes:
        out.append(Check("cae", "CAE presente y vigente", FAIL, "No se encontró el CAE."))
    elif "cae_formato" in codes:
        out.append(Check("cae", "CAE presente y vigente", FAIL, "El CAE no tiene 14 dígitos."))
    elif "cae_vence_antes_de_emision" in codes:
        out.append(Check("cae", "CAE presente y vigente", FAIL,
                         f"El CAE venció el {date_ar(f.cae_vencimiento)}, antes de la fecha de emisión "
                         f"({date_ar(f.fecha_emision)})."))
    elif f.cae:
        venc = f", vence el {date_ar(f.cae_vencimiento)}" if f.cae_vencimiento else ""
        out.append(Check("cae", "CAE presente y vigente", OK, f"CAE {f.cae}{venc}."))
    else:
        out.append(Check("cae", "CAE presente y vigente", NA, "Sin CAE."))

    # 6. Duplicates
    if duplicate_of:
        out.append(Check("duplicada", "No está repetida", FAIL,
                         f"Es la misma factura que «{duplicate_of}» (mismo emisor, punto de venta y número)."))
    elif invoice_key(r):
        out.append(Check("duplicada", "No está repetida", OK, "No hay otra factura con el mismo emisor y número en el lote."))
    else:
        out.append(Check("duplicada", "No está repetida", NA, "Sin número de comprobante para comparar."))
    return out


SHORT = {
    "lectura": "Datos incompletos",
    "cuit": "CUIT inválido",
    "cuentas": "Las cuentas no cierran",
    "qr": "El QR no coincide",
    "cae": "Problema con el CAE",
    "duplicada": "Factura repetida",
}


def summary_reason(items: list[Check]) -> str | None:
    """One short line for the review queue, e.g. 'Las cuentas no cierran: ...'."""
    failed = [c for c in items if c.estado == FAIL]
    if not failed:
        return None
    first = failed[0]
    if first.id == "lectura" and first.detalle.startswith("No se pudo leer"):
        return "No se pudo leer"
    # a misread CUIT that the QR corrected is not the headline when something else failed
    more = len(failed) - 1
    return SHORT[first.id] + (f" (y {more} control{'es' if more > 1 else ''} más)" if more else "")


@dataclass
class Row:
    """One invoice of a batch, with its checks and final status."""

    result: Result
    checks: list[Check]
    duplicate_of: int | None = None

    @property
    def estado(self) -> str:
        return "revisar" if any(c.estado == FAIL for c in self.checks) else "ok"

    @property
    def motivo(self) -> str | None:
        return summary_reason(self.checks)

    def as_dict(self) -> dict:
        return {"estado": self.estado, "motivo": self.motivo, "duplicada_de": self.duplicate_of,
                "controles": [asdict(c) for c in self.checks]}


def review_batch(results: list[Result]) -> list[Row]:
    dups = find_duplicates(results)
    return [Row(r, checks(r, results[dups[i]].archivo if i in dups else None), dups.get(i))
            for i, r in enumerate(results)]


def batch_summary(rows: list[Row]) -> dict:
    ok = [r for r in rows if r.estado == "ok"]

    def signed(r: Row, v: float | None) -> float:
        return -(v or 0) if r.result.factura.tipo == "Nota de Crédito" else (v or 0)

    return {
        "total": len(rows),
        "ok": len(ok),
        "revisar": len(rows) - len(ok),
        "importe_ok": round(sum(signed(r, r.result.factura.total) for r in ok), 2),
        "iva_ok": round(sum(signed(r, r.result.factura.iva_total) for r in ok if r.result.factura.letra in ("A", "M")), 2),
    }
