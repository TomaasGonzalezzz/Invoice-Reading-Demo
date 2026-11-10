"""Excel and CSV files for a reviewed batch.

The Excel has three sheets: every invoice (doubtful ones highlighted, with the reason), the review
queue, and a short summary. The CSV is a generic import file with only the invoices ready to load:
semicolon separated, dates as dd/mm/aaaa and decimal comma, the way a spreadsheet set to Spanish
(Argentina) expects them. Each accounting system has its own import layout, so the columns may need
mapping on the other side.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .afip import format_cuit
from .revision import FAIL, Row, batch_summary

MONEY_FMT = '#,##0.00;-#,##0.00'

# (header, getter, kind)
COLUMNS = [
    ("Estado", lambda r: "Lista para cargar" if r.estado == "ok" else "Para revisar", "text"),
    ("Motivo", lambda r: r.motivo or "", "text"),
    ("Fecha", lambda r: r.result.factura.fecha_emision, "date"),
    ("Tipo", lambda r: r.result.factura.tipo, "text"),
    ("Letra", lambda r: r.result.factura.letra, "text"),
    ("Punto de venta", lambda r: r.result.factura.punto_venta, "int"),
    ("Número", lambda r: r.result.factura.numero, "int"),
    ("CUIT emisor", lambda r: r.result.factura.emisor_cuit, "cuit"),
    ("Emisor", lambda r: r.result.factura.emisor_razon_social, "text"),
    ("Neto gravado", lambda r: r.result.factura.neto_gravado, "money"),
    ("IVA 21 %", lambda r: r.result.factura.iva_21, "money"),
    ("IVA 10,5 %", lambda r: r.result.factura.iva_10_5, "money"),
    ("IVA total", lambda r: r.result.factura.iva_total, "money"),
    ("Otros tributos", lambda r: r.result.factura.otros_tributos, "money"),
    ("Total", lambda r: r.result.factura.total, "money"),
    ("CAE", lambda r: r.result.factura.cae, "text"),
    ("Vto. CAE", lambda r: r.result.factura.cae_vencimiento, "date"),
    ("Moneda", lambda r: r.result.factura.moneda, "text"),
    ("IVA contenido", lambda r: r.result.factura.iva_contenido, "money"),
    ("CUIT receptor", lambda r: r.result.factura.receptor_cuit, "cuit"),
    ("Receptor", lambda r: r.result.factura.receptor_razon_social, "text"),
    ("Archivo", lambda r: r.result.archivo, "text"),
]

INK = "1F2937"
HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
REVIEW_FILL = PatternFill("solid", fgColor="FDF1DC")
THIN = Side(style="thin", color="E5E7EB")


def _value(getter, kind, row):
    v = getter(row)
    if v is None:
        return None
    if kind == "date":
        return date.fromisoformat(v)
    if kind == "cuit":
        return format_cuit(v)
    return v


def _header(ws, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"


def _widths(ws, minimum: int = 9, maximum: int = 48) -> None:
    for col in ws.columns:
        longest = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(minimum, longest + 2), maximum)


def to_xlsx(rows: list[Row], path: Path | None = None) -> bytes:
    """Write the batch to an Excel file; returns its bytes (and saves it when a path is given)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Comprobantes"
    _header(ws, [c[0] for c in COLUMNS])
    for row in rows:
        ws.append([_value(g, k, row) for _, g, k in COLUMNS])
        for cell, (_, _, kind) in zip(ws[ws.max_row], COLUMNS):
            if kind == "money":
                cell.number_format = MONEY_FMT
            elif kind == "date":
                cell.number_format = "dd/mm/yyyy"
            cell.border = Border(bottom=THIN)
            if row.estado != "ok":
                cell.fill = REVIEW_FILL
    ws.auto_filter.ref = ws.dimensions
    _widths(ws)

    queue = wb.create_sheet("Para revisar")
    _header(queue, ["Archivo", "Comprobante", "Emisor", "Motivo", "Detalle"])
    for row in rows:
        if row.estado == "ok":
            continue
        f = row.result.factura
        comp = (f"{f.tipo or ''} {f.letra or ''} {f.punto_venta:05d}-{f.numero:08d}".strip()
                if f.punto_venta is not None and f.numero is not None else "")
        detail = " ".join(c.detalle for c in row.checks if c.estado == FAIL)
        queue.append([row.result.archivo, comp, f.emisor_razon_social, row.motivo, detail])
        for cell in queue[queue.max_row]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(bottom=THIN)
    _widths(queue)
    queue.column_dimensions["E"].width = 80

    s = batch_summary(rows)
    summary = wb.create_sheet("Resumen")
    lines = [("Comprobantes", s["total"]), ("Listos para cargar", s["ok"]), ("Para revisar", s["revisar"]),
             ("Importe total de los listos para cargar", s["importe_ok"]),
             ("IVA de las facturas A listas para cargar", s["iva_ok"])]
    for label, value in lines:
        summary.append([label, value])
    for r in (4, 5):
        summary.cell(r, 2).number_format = MONEY_FMT
    for r in range(1, len(lines) + 1):
        summary.cell(r, 1).font = Font(color=INK)
    summary.column_dimensions["A"].width = 44
    summary.column_dimensions["B"].width = 18
    summary.append([])
    summary.append(["Notas de crédito restan. Las facturas para revisar no se suman hasta que alguien las mire."])
    summary.cell(summary.max_row, 1).font = Font(italic=True, color="6B7280")

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if path is not None:
        Path(path).write_bytes(data)
    return data


CSV_COLUMNS = ["Fecha", "Tipo", "Letra", "Punto de venta", "Número", "CUIT emisor", "Emisor", "Neto gravado",
               "IVA 21", "IVA 10,5", "Otros tributos", "Total", "CAE", "Vto. CAE"]


def _num(v: float | None) -> str:
    return "" if v is None else f"{v:.2f}".replace(".", ",")


def _date(iso: str | None) -> str:
    return "" if not iso else date.fromisoformat(iso).strftime("%d/%m/%Y")


def to_csv(rows: list[Row]) -> bytes:
    """Only the invoices ready to load. UTF-8 with BOM so Excel opens accents correctly."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for row in rows:
        if row.estado != "ok":
            continue
        f = row.result.factura
        w.writerow([_date(f.fecha_emision), f.tipo or "", f.letra or "",
                    "" if f.punto_venta is None else f"{f.punto_venta:05d}",
                    "" if f.numero is None else f"{f.numero:08d}",
                    format_cuit(f.emisor_cuit) if f.emisor_cuit else "", f.emisor_razon_social or "",
                    _num(f.neto_gravado), _num(f.iva_21), _num(f.iva_10_5), _num(f.otros_tributos),
                    _num(f.total), f.cae or "", _date(f.cae_vencimiento)])
    return ("﻿" + buf.getvalue()).encode("utf-8")
