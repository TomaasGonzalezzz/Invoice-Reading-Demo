"""Synthetic Argentine invoices for testing: fictitious data, three layouts, three capture conditions.

Every invoice is rendered as a digital PDF, a scan and a phone photo, and its ground truth is
saved as JSON. A few invoices carry deliberate errors (traps) that the validator must flag.

    uv run python -m facturas.synth --n 50 --out data
"""

from __future__ import annotations

import argparse
import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import qrcode
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .afip import VOUCHER_TYPES, cuit_check_digit, encode_qr_url, voucher_code

W, H = A4

COMPANY_WORDS = ["Metalúrgica", "Distribuidora", "Ferretería", "Talleres", "Logística", "Química",
                 "Plásticos", "Servicios Industriales", "Comercial", "Insumos", "Electromecánica", "Agro"]
COMPANY_NAMES = ["del Norte", "Pilar", "San Martín", "Los Robles", "Garín", "Escobar", "Del Plata",
                 "Tortuguitas", "La Unión", "Río Luján", "Belgrano", "Sur", "Ituzaingó", "Maschwitz"]
COMPANY_SUFFIX = ["S.A.", "S.R.L.", "S.A.S.", "S.A.", "S.R.L."]
FIRST = ["María", "Juan", "Lucía", "Martín", "Sofía", "Diego", "Valentina", "Pablo", "Camila", "Nicolás"]
LAST = ["González", "Rodríguez", "Fernández", "López", "Martínez", "Pérez", "Gómez", "Díaz", "Sosa", "Romero"]
STREETS = ["Av. San Martín", "Belgrano", "Ruta 8 Km", "Av. Libertador", "Colectora Panamericana",
           "Mitre", "Sarmiento", "Rivadavia", "Av. Tomás Márquez", "Lavalle"]
TOWNS = ["Pilar", "Garín", "Escobar", "San Isidro", "Tigre", "General Pacheco", "CABA", "Del Viso"]
ITEMS = [("Tornillo hexagonal M8 x 30 (caja x100)", 2_000, 9_000), ("Rodamiento 6205 2RS", 6_000, 15_000),
         ("Servicio de mantenimiento preventivo", 80_000, 450_000), ("Cable unipolar 2,5 mm (rollo 100 m)", 40_000, 90_000),
         ("Guantes de nitrilo (caja x50)", 5_000, 12_000), ("Flete Pilar - CABA", 30_000, 120_000),
         ("Aceite hidráulico ISO 68 (20 L)", 60_000, 140_000), ("Contactor 25 A 220 V", 25_000, 60_000),
         ("Honorarios profesionales", 150_000, 900_000), ("Pallet de madera 1,2 x 1,0 m", 9_000, 20_000),
         ("Mano de obra soldadura", 50_000, 300_000), ("Correa en V A-42", 7_000, 18_000)]


def make_cuit(rng: random.Random, prefix: str) -> str:
    while True:
        body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(8))
        dv = cuit_check_digit(body)
        if dv is not None:
            return body + str(dv)


def company(rng: random.Random) -> str:
    return f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_NAMES)} {rng.choice(COMPANY_SUFFIX)}"


def person(rng: random.Random) -> str:
    return f"{rng.choice(LAST)} {rng.choice(FIRST)}"


def address(rng: random.Random) -> str:
    return f"{rng.choice(STREETS)} {rng.randint(10, 4800)} - {rng.choice(TOWNS)}, Buenos Aires"


def fmt_ar(x: float, thousands: bool = True) -> str:
    """1234567.8 -> '1.234.567,80' (or '1234567,80')."""
    s = f"{x:,.2f}" if thousands else f"{x:.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_us(x: float) -> str:
    return f"{x:.2f}"


@dataclass
class Spec:
    id: str
    layout: str
    tipo: str
    letra: str
    punto_venta: int
    numero: int
    fecha: date
    emisor_cuit: str
    emisor_nombre: str
    emisor_iva: str
    emisor_domicilio: str
    receptor_cuit: str | None
    receptor_nombre: str
    receptor_iva: str
    receptor_domicilio: str
    items: list[tuple[str, int, float, float]]  # desc, qty, unit price (net for A), iva rate
    neto: float | None
    iva21: float | None
    iva105: float | None
    otros: float | None
    iva_contenido: float | None
    total: float
    cae: str
    cae_vto: date
    has_qr: bool = True
    trap: str | None = None
    qr_overrides: dict = field(default_factory=dict)
    printed_emisor_cuit: str | None = None


def build_spec(rng: random.Random, idx: int, layout: str, force_letter: str | None = None) -> Spec:
    letra = rng.choices(["A", "B", "C"], weights=[5, 3, 2])[0]
    letra = force_letter or letra
    tipo = rng.choices(["Factura", "Nota de Crédito"], weights=[9, 1])[0]
    fecha = date(2025, 1, 1) + timedelta(days=rng.randint(0, 640))
    emisor_cuit = make_cuit(rng, rng.choice(["30", "33"]) if letra != "C" else rng.choice(["20", "27"]))
    emisor_nombre = company(rng) if letra != "C" else person(rng)
    emisor_iva = "IVA Responsable Inscripto" if letra != "C" else "Responsable Monotributo"

    if letra == "A" or (letra == "C" and rng.random() < 0.5):
        receptor_cuit = make_cuit(rng, rng.choice(["30", "33"]))
        receptor_nombre = company(rng)
        receptor_iva = "IVA Responsable Inscripto"
    else:
        receptor_cuit = None
        receptor_nombre = person(rng) if rng.random() < 0.6 else "Consumidor Final"
        receptor_iva = "Consumidor Final"

    items = []
    for desc, lo, hi in rng.sample(ITEMS, rng.randint(1, 5)):
        rate = 21.0 if (letra != "A" or rng.random() < 0.8) else 10.5
        items.append((desc, rng.randint(1, 12), round(rng.uniform(lo, hi), 2), rate))

    if letra == "A":
        base21 = round(sum(q * p for _, q, p, r in items if r == 21.0), 2)
        base105 = round(sum(q * p for _, q, p, r in items if r == 10.5), 2)
        neto = round(base21 + base105, 2)
        iva21 = round(base21 * 0.21, 2) if base21 else 0.0
        iva105 = round(base105 * 0.105, 2) if base105 else 0.0
        otros = round(neto * 0.03, 2) if rng.random() < 0.3 else 0.0
        total = round(neto + iva21 + iva105 + otros, 2)
        iva_contenido = None
    else:
        neto = iva21 = iva105 = None
        otros = 0.0
        total = round(sum(q * p for _, q, p, _ in items), 2)
        iva_contenido = round(total - total / 1.21, 2) if letra == "B" else None

    return Spec(
        id=f"fac_{idx:03d}", layout=layout, tipo=tipo, letra=letra,
        punto_venta=rng.choice([1, 2, 3, 4, 5, 10, 12]), numero=rng.randint(1, 99_999),
        fecha=fecha, emisor_cuit=emisor_cuit, emisor_nombre=emisor_nombre, emisor_iva=emisor_iva,
        emisor_domicilio=address(rng), receptor_cuit=receptor_cuit, receptor_nombre=receptor_nombre,
        receptor_iva=receptor_iva, receptor_domicilio=address(rng), items=items, neto=neto,
        iva21=iva21, iva105=iva105, otros=otros, iva_contenido=iva_contenido, total=total,
        cae="7" + "".join(str(rng.randint(0, 9)) for _ in range(13)), cae_vto=fecha + timedelta(days=10),
    )


def apply_trap(spec: Spec, trap: str, rng: random.Random) -> None:
    spec.trap = trap
    if trap == "total_no_cierra":
        spec.total = round(spec.total + rng.choice([100.0, 1000.0, 0.9]), 2)
    elif trap == "cuit_invalido":
        d = spec.emisor_cuit
        spec.printed_emisor_cuit = d[:10] + str((int(d[10]) + 3) % 10)
    elif trap == "qr_distinto":
        spec.qr_overrides["importe"] = round(spec.total * 1.1, 2)


def qr_payload(spec: Spec) -> dict:
    code = voucher_code(spec.tipo, spec.letra)
    if spec.receptor_cuit:
        tipo_doc, nro_doc = 80, int(spec.receptor_cuit)
    else:
        tipo_doc, nro_doc = 99, 0
    payload = {"ver": 1, "fecha": spec.fecha.isoformat(), "cuit": int(spec.emisor_cuit),
               "ptoVta": spec.punto_venta, "tipoCmp": code, "nroCmp": spec.numero,
               "importe": spec.total, "moneda": "PES", "ctz": 1, "tipoDocRec": tipo_doc,
               "nroDocRec": nro_doc, "tipoCodAut": "E", "codAut": int(spec.cae)}
    payload.update(spec.qr_overrides)
    return payload


def qr_image(spec: Spec) -> ImageReader:
    img = qrcode.make(encode_qr_url(qr_payload(spec)), box_size=4, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


# ---------------------------------------------------------------- layouts

def _disclaimer(c: canvas.Canvas) -> None:
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillGray(0.45)
    c.drawCentredString(W / 2, 18, "Documento de prueba generado sintéticamente. Datos ficticios, sin validez fiscal.")
    c.setFillGray(0)


def layout_afip(c: canvas.Canvas, s: Spec) -> None:
    """Layout similar to the government's free online invoicing tool."""
    code = voucher_code(s.tipo, s.letra)
    top = H - 40
    c.rect(30, top - 18, W - 60, 18)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W / 2, top - 13, "ORIGINAL")
    c.rect(30, top - 150, W - 60, 132)
    c.line(W / 2, top - 150, W / 2, top - 66)
    c.rect(W / 2 - 24, top - 66, 48, 48)
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(W / 2, top - 46, s.letra)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W / 2, top - 60, f"COD. {code:02d}")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, top - 45, s.emisor_nombre.upper()[:34])
    c.setFont("Helvetica", 8.5)
    c.drawString(40, top - 80, f"Razón Social: {s.emisor_nombre}")
    c.drawString(40, top - 95, f"Domicilio Comercial: {s.emisor_domicilio}"[:60])
    c.drawString(40, top - 110, f"Condición frente al IVA: {s.emisor_iva}")

    x = W / 2 + 35
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, top - 45, s.tipo.upper())
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x, top - 70, f"Punto de Venta: {s.punto_venta:05d}    Comp. Nro: {s.numero:08d}")
    c.drawString(x, top - 85, f"Fecha de Emisión: {s.fecha:%d/%m/%Y}")
    c.setFont("Helvetica", 8.5)
    c.drawString(x, top - 105, f"CUIT: {s.printed_emisor_cuit or s.emisor_cuit}")
    c.drawString(x, top - 118, f"Ingresos Brutos: 901-{s.emisor_cuit[2:8]}-{s.emisor_cuit[8]}")
    c.drawString(x, top - 131, f"Fecha de Inicio de Actividades: 01/03/{2008 + s.numero % 12}")

    y = top - 175
    c.rect(30, y - 8, W - 60, 22)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(40, y, f"Período Facturado Desde: {s.fecha.replace(day=1):%d/%m/%Y}   Hasta: {s.fecha:%d/%m/%Y}"
                        f"   Fecha de Vto. para el pago: {s.fecha + timedelta(days=30):%d/%m/%Y}")

    y -= 30
    c.rect(30, y - 50, W - 60, 60)
    c.setFont("Helvetica", 8.5)
    doc = f"CUIT: {s.receptor_cuit}" if s.receptor_cuit else "DNI: -"
    c.drawString(40, y - 5, doc)
    c.drawString(200, y - 5, f"Apellido y Nombre / Razón Social: {s.receptor_nombre}")
    c.drawString(40, y - 22, f"Condición frente al IVA: {s.receptor_iva}")
    c.drawString(300, y - 22, f"Domicilio: {s.receptor_domicilio}"[:52])
    c.drawString(40, y - 39, "Condición de venta: Cuenta Corriente")

    y -= 75
    c.setFillGray(0.85)
    c.rect(30, y - 4, W - 60, 16, fill=1, stroke=0)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 7.5)
    cols = [35, 70, 290, 335, 395, 450, 500] if s.letra == "A" else [35, 70, 300, 360, 430, 500]
    heads = (["Código", "Producto / Servicio", "Cantidad", "Precio Unit.", "Subtotal", "Alícuota", "Subtotal c/IVA"]
             if s.letra == "A" else ["Código", "Producto / Servicio", "Cantidad", "Precio Unit.", "% Bonif", "Subtotal"])
    for cx, h in zip(cols, heads):
        c.drawString(cx, y, h)
    c.setFont("Helvetica", 8)
    for i, (desc, q, p, r) in enumerate(s.items):
        y -= 16
        sub = q * p
        row = [f"{i + 1:03d}", desc[:40], f"{q},00", fmt_ar(p, False)]
        if s.letra == "A":
            row += [fmt_ar(sub, False), f"{r:g}%".replace(".", ","), fmt_ar(sub * (1 + r / 100), False)]
        else:
            row += ["0,00", fmt_ar(sub, False)]
        for cx, v in zip(cols, row):
            c.drawString(cx, y, v)

    y = 260
    c.rect(30, y - 115, W - 60, 130)
    c.setFont("Helvetica-Bold", 8.5)
    lines = []
    if s.letra == "A":
        lines = [("Importe Neto Gravado: $", s.neto), ("IVA 27%: $", 0.0), ("IVA 21%: $", s.iva21),
                 ("IVA 10.5%: $", s.iva105), ("IVA 5%: $", 0.0), ("IVA 2.5%: $", 0.0), ("IVA 0%: $", 0.0),
                 ("Importe Otros Tributos: $", s.otros), ("Importe Total: $", s.total)]
    else:
        sub = round(sum(q * p for _, q, p, _ in s.items), 2)
        lines = [("Subtotal: $", sub), ("Importe Otros Tributos: $", s.otros), ("Importe Total: $", s.total)]
    for i, (label, val) in enumerate(lines):
        yy = y - i * 12.5
        c.drawRightString(470, yy, label)
        c.drawRightString(555, yy, fmt_ar(val or 0.0, False))
    if s.letra == "B":
        c.setFont("Helvetica", 7.5)
        c.drawString(40, y - 70, "Régimen de Transparencia Fiscal al Consumidor (Ley 27.743)")
        c.drawString(40, y - 82, f"IVA Contenido: $ {fmt_ar(s.iva_contenido, False)}")

    if s.has_qr:
        c.drawImage(qr_image(s), 35, 45, 85, 85)
    c.setFont("Helvetica-BoldOblique", 10)
    c.drawString(135, 95, "Comprobante Autorizado")
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(555, 100, f"CAE N°: {s.cae}")
    c.drawRightString(555, 85, f"Fecha de Vto. de CAE: {s.cae_vto:%d/%m/%Y}")
    c.setFont("Helvetica", 8)
    c.drawCentredString(W / 2, 55, "Pág. 1/1")


def layout_moderno(c: canvas.Canvas, s: Spec) -> None:
    """Layout in the style of a commercial invoicing software."""
    code = voucher_code(s.tipo, s.letra)
    c.setFillColorRGB(0.12, 0.25, 0.45)
    c.rect(0, H - 8, W, 8, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(40, H - 55, s.emisor_nombre)
    c.setFillGray(0.25)
    c.setFont("Helvetica", 8)
    cuit = s.printed_emisor_cuit or s.emisor_cuit
    c.drawString(40, H - 72, s.emisor_domicilio)
    c.drawString(40, H - 84, f"CUIT {cuit[:2]}-{cuit[2:10]}-{cuit[10:]}  |  IIBB 901-{s.emisor_cuit[2:8]}  |  Inicio act. 03/2014")
    c.drawString(40, H - 96, s.emisor_iva)
    c.setFillGray(0)

    c.setStrokeColorRGB(0.12, 0.25, 0.45)
    c.roundRect(370, H - 120, 190, 85, 6)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(382, H - 57, f"{s.tipo.upper()} {s.letra}")
    c.setFont("Helvetica", 7)
    c.drawString(382, H - 68, f"Cód. {code:02d}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(382, H - 86, f"N° {s.punto_venta:04d}-{s.numero:08d}")
    c.setFont("Helvetica", 9)
    c.drawString(382, H - 104, f"Fecha: {s.fecha:%d-%m-%Y}")

    y = H - 150
    c.setFillColorRGB(0.94, 0.95, 0.97)
    c.rect(35, y - 48, W - 70, 62, fill=1, stroke=0)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(45, y, f"Cliente: {s.receptor_nombre}")
    c.setFont("Helvetica", 8.5)
    if s.receptor_cuit:
        r = s.receptor_cuit
        c.drawString(45, y - 15, f"CUIT: {r[:2]}-{r[2:10]}-{r[10:]}")
    c.drawString(45, y - 30, f"Cond. IVA: {s.receptor_iva}")
    c.drawString(300, y - 15, f"Dirección: {s.receptor_domicilio}"[:55])
    c.drawString(300, y - 30, "Cond. de venta: 30 días")

    y -= 85
    c.setFont("Helvetica-Bold", 8.5)
    for cx, h in zip([40, 330, 380, 470], ["Descripción", "Cant.", "P. Unit.", "Importe"]):
        c.drawString(cx, y, h)
    c.line(35, y - 5, W - 35, y - 5)
    c.setFont("Helvetica", 8.5)
    for desc, q, p, r in s.items:
        y -= 17
        c.drawString(40, y, desc[:52])
        c.drawRightString(350, y, str(q))
        c.drawRightString(450, y, fmt_ar(p))
        c.drawRightString(555, y, fmt_ar(q * p))

    y = 250
    c.line(330, y + 15, W - 35, y + 15)
    rows = []
    if s.letra == "A":
        rows.append(("Subtotal", s.neto))
        if s.iva21:
            rows.append(("IVA 21%", s.iva21))
        if s.iva105:
            rows.append(("IVA 10,5%", s.iva105))
        if s.otros:
            rows.append(("Percepción IIBB", s.otros))
    for label, val in rows:
        c.setFont("Helvetica", 9)
        c.drawString(340, y, label)
        c.drawRightString(555, y, fmt_ar(val))
        y -= 15
    c.setFont("Helvetica-Bold", 12)
    c.drawString(340, y - 4, "TOTAL")
    c.drawRightString(555, y - 4, f"$ {fmt_ar(s.total)}")
    if s.letra == "B":
        c.setFont("Helvetica", 8)
        c.drawString(340, y - 22, "IVA contenido (Ley 27.743)")
        c.drawRightString(555, y - 22, fmt_ar(s.iva_contenido))

    if s.has_qr:
        c.drawImage(qr_image(s), W - 125, 40, 85, 85)
    c.setFont("Helvetica", 8.5)
    c.drawString(40, 75, f"CAE: {s.cae}     Vto. CAE: {s.cae_vto:%d-%m-%Y}")
    c.setFillGray(0.4)
    c.drawString(40, 60, "Gracias por su compra.")
    c.setFillGray(0)


def layout_compacto(c: canvas.Canvas, s: Spec) -> None:
    """Older-style layout with dotted abbreviations and dot decimals."""
    code = voucher_code(s.tipo, s.letra)
    cuit = s.printed_emisor_cuit or s.emisor_cuit
    c.setFont("Times-Bold", 15)
    c.drawCentredString(W / 2, H - 50, s.emisor_nombre)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(W / 2, H - 64, s.emisor_domicilio)
    c.drawCentredString(W / 2, H - 76, f"C.U.I.T. N°: {cuit[:2]}-{cuit[2:10]}-{cuit[10:]}   -   {s.emisor_iva}")
    c.line(40, H - 86, W - 40, H - 86)

    c.rect(45, H - 135, 40, 40)
    c.setFont("Times-Bold", 24)
    c.drawCentredString(65, H - 122, s.letra)
    c.setFont("Times-Roman", 7)
    c.drawCentredString(65, H - 132, f"Código {code:03d}")
    c.setFont("Times-Bold", 13)
    c.drawString(100, H - 108, s.tipo)
    c.setFont("Times-Roman", 10)
    c.drawString(100, H - 125, f"Pto. Vta.: {s.punto_venta:04d}   Nro.: {s.numero:08d}")
    c.drawString(360, H - 108, f"Fecha emisión: {s.fecha:%d/%m/%Y}")

    y = H - 165
    c.drawString(45, y, f"Señor(es): {s.receptor_nombre}")
    if s.receptor_cuit:
        r = s.receptor_cuit
        c.drawString(360, y, f"C.U.I.T.: {r[:2]}-{r[2:10]}-{r[10:]}")
    c.drawString(45, y - 15, f"I.V.A.: {s.receptor_iva}")
    c.line(40, y - 25, W - 40, y - 25)

    y -= 45
    c.setFont("Times-Bold", 9.5)
    for cx, h in zip([45, 330, 400, 480], ["Detalle", "Cantidad", "Unitario", "Importe"]):
        c.drawString(cx, y, h)
    c.setFont("Times-Roman", 9.5)
    for desc, q, p, r in s.items:
        y -= 15
        c.drawString(45, y, desc[:50])
        c.drawString(340, y, str(q))
        c.drawRightString(455, y, fmt_us(p))
        c.drawRightString(550, y, fmt_us(q * p))

    y = 235
    c.setFont("Times-Roman", 10)
    rows = []
    if s.letra == "A":
        rows = [("Neto Gravado:", s.neto)]
        if s.iva21:
            rows.append(("I.V.A. 21,00 %:", s.iva21))
        if s.iva105:
            rows.append(("I.V.A. 10,50 %:", s.iva105))
        if s.otros:
            rows.append(("Perc. IIBB:", s.otros))
    for label, val in rows:
        c.drawString(360, y, label)
        c.drawRightString(550, y, fmt_us(val))
        y -= 14
    c.setFont("Times-Bold", 12)
    c.drawString(360, y - 3, "TOTAL:")
    c.drawRightString(550, y - 3, fmt_us(s.total))
    if s.letra == "B":
        c.setFont("Times-Roman", 9)
        c.drawString(360, y - 20, "I.V.A. Contenido:")
        c.drawRightString(550, y - 20, fmt_us(s.iva_contenido))

    if s.has_qr:
        c.drawImage(qr_image(s), 45, 40, 80, 80)
    c.setFont("Times-Roman", 10)
    c.drawString(140, 85, f"C.A.E. N°: {s.cae}")
    c.drawString(140, 70, f"Vencimiento C.A.E.: {s.cae_vto:%d/%m/%Y}")


LAYOUTS = {"afip": layout_afip, "moderno": layout_moderno, "compacto": layout_compacto}


def render_pdf(spec: Spec, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"{spec.tipo} {spec.letra} {spec.punto_venta:05d}-{spec.numero:08d}")
    LAYOUTS[spec.layout](c, spec)
    _disclaimer(c)
    c.showPage()
    c.save()


# ---------------------------------------------------------------- capture conditions

def rasterize(pdf_path: Path, dpi: int) -> np.ndarray:
    page = pdfium.PdfDocument(str(pdf_path))[0]
    img = page.render(scale=dpi / 72).to_pil().convert("RGB")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def simulate_scan(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-2.0, 2.0), 1.0)
    gray = cv2.warpAffine(gray, m, (w, h), borderValue=245)
    gray = cv2.GaussianBlur(gray, (3, 3), 0.6)
    gray = np.clip(gray.astype(np.float32) * rng.uniform(0.9, 1.0) + rng.normal(0, 7, gray.shape), 0, 255)
    return gray.astype(np.uint8)


def simulate_photo(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    pad = int(0.08 * w)
    canvas_img = np.full((h + 2 * pad, w + 2 * pad, 3), rng.integers(60, 120, 3), np.uint8)
    canvas_img[pad:pad + h, pad:pad + w] = img
    H2, W2 = canvas_img.shape[:2]
    src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    jitter = rng.uniform(-0.05, 0.05, (4, 2)) * [w, h]
    dst = (src + jitter).astype(np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(canvas_img, m, (W2, H2), borderMode=cv2.BORDER_REPLICATE)
    # uneven light: a soft gradient plus a shadow band
    yy, xx = np.mgrid[0:H2, 0:W2].astype(np.float32)
    angle = rng.uniform(0, 2 * np.pi)
    grad = 0.82 + 0.18 * ((np.cos(angle) * xx / W2 + np.sin(angle) * yy / H2) + 1) / 2
    out = np.clip(out.astype(np.float32) * grad[..., None] * rng.uniform(0.9, 1.05, 3), 0, 255).astype(np.uint8)
    out = cv2.GaussianBlur(out, (3, 3), rng.uniform(0.6, 1.1))
    scale = 1600 / max(out.shape[:2])  # WhatsApp "standard quality" sends ~1600 px
    return cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def ground_truth(spec: Spec) -> dict:
    return {
        "tipo": spec.tipo, "letra": spec.letra, "punto_venta": spec.punto_venta, "numero": spec.numero,
        "fecha_emision": spec.fecha.isoformat(),
        "emisor_cuit": spec.printed_emisor_cuit or spec.emisor_cuit,
        "emisor_razon_social": spec.emisor_nombre,
        "receptor_cuit": spec.receptor_cuit,
        "receptor_razon_social": None if spec.receptor_nombre == "Consumidor Final" else spec.receptor_nombre,
        "moneda": "PES",
        "neto_gravado": spec.neto,
        "iva_21": spec.iva21 if spec.letra == "A" else None,
        "iva_10_5": spec.iva105 if spec.letra == "A" else None,
        "iva_total": round(spec.iva21 + spec.iva105, 2) if spec.letra == "A" else None,
        "otros_tributos": spec.otros if spec.letra == "A" else None,
        "iva_contenido": spec.iva_contenido,
        "total": spec.total, "cae": spec.cae, "cae_vencimiento": spec.cae_vto.isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    nrng = np.random.default_rng(args.seed)
    for sub in ("pdf", "scan", "foto", "gt"):
        (args.out / sub).mkdir(parents=True, exist_ok=True)

    layouts = list(LAYOUTS)
    # spread traps over the three layouts; arithmetic traps need itemized IVA (letter A)
    traps = {4: "total_no_cierra", 15: "cuit_invalido", 29: "qr_distinto", 39: "total_no_cierra", 44: "qr_distinto"}
    no_qr = {10, 35}
    manifest = []
    for i in range(args.n):
        spec = build_spec(rng, i + 1, layouts[i % len(layouts)],
                          force_letter="A" if traps.get(i) == "total_no_cierra" else None)
        if i in traps:
            apply_trap(spec, traps[i], rng)
        if i in no_qr:
            spec.has_qr = False
        pdf_path = args.out / "pdf" / f"{spec.id}.pdf"
        render_pdf(spec, pdf_path)
        cv2.imwrite(str(args.out / "scan" / f"{spec.id}.jpg"), simulate_scan(rasterize(pdf_path, 150), nrng),
                    [cv2.IMWRITE_JPEG_QUALITY, 70])
        cv2.imwrite(str(args.out / "foto" / f"{spec.id}.jpg"), simulate_photo(rasterize(pdf_path, 150), nrng),
                    [cv2.IMWRITE_JPEG_QUALITY, 75])
        gt = ground_truth(spec)
        meta = {"id": spec.id, "layout": spec.layout, "trap": spec.trap, "has_qr": spec.has_qr}
        (args.out / "gt" / f"{spec.id}.json").write_text(json.dumps({"meta": meta, "factura": gt},
                                                                    ensure_ascii=False, indent=2), "utf-8")
        manifest.append(meta)
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")
    print(f"{args.n} facturas x 3 condiciones en {args.out}/")


if __name__ == "__main__":
    main()
