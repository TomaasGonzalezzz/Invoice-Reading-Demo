"""Where on the page each extracted value was printed, to draw a box over it.

The rules work on plain text lines, so this module goes the other way: it looks for each final
value in the lines the reader produced (preferring the line with the matching label) and turns the
characters it covers into a box. Boxes are only drawn where the value is actually printed, so a
value that came from the QR and differs from the paper gets no box on the text.
"""

from __future__ import annotations

import re
import unicodedata

import numpy as np

from .reader import Page, Token, map_points, row_text
from .rules import CUIT_RE, DATE, IVA, compact, parse_amount, parse_date
from .schema import Invoice

Box = list[list[float]]  # four (x, y) corners in page.image coordinates

AMOUNT_LABELS = {
    "neto_gravado": r"neto|subtotal",
    "iva_21": IVA + r"21",
    "iva_10_5": IVA + r"10[.,]5",
    "iva_total": IVA,
    "otros_tributos": r"tributos|percep|perc\.?iibb|otros",
    "iva_contenido": r"contenido",
    "total": r"(?<!sub)total",
}
NUMBER = re.compile(r"\d[\d.,]*\d|\d")


class _Line:
    def __init__(self, row: list[Token]):
        self.row = row
        self.text, self.spans = row_text(row)
        self.compact = compact(self.text)
        # compact text with a map back to positions in self.text
        chars, index = [], []
        for i, ch in enumerate(self.text):
            if ch.isspace():
                continue
            for c in unicodedata.normalize("NFKD", ch):
                if not unicodedata.combining(c):
                    chars.append(c.lower())
                    index.append(i)
        self.folded, self.folded_index = "".join(chars), index

    def rect(self, start: int, end: int) -> tuple[float, float, float, float] | None:
        """Bounding rectangle (token coordinates) of characters [start, end) of the line."""
        xs0, xs1, ys0, ys1 = [], [], [], []
        for tok, (a, b) in zip(self.row, self.spans):
            lo, hi = max(start, a), min(end, b)
            if lo >= hi:
                continue
            n = max(b - a, 1)
            w = tok.x1 - tok.x0
            xs0.append(tok.x0 + w * (lo - a) / n)
            xs1.append(tok.x0 + w * (hi - a) / n)
            ys0.append(tok.y0)
            ys1.append(tok.y1)
        if not xs0:
            return None
        return min(xs0), min(ys0), max(xs1), max(ys1)


def _pick(cands: list[tuple[int, int, int, bool]], last: bool = False):
    """cands: (line, start, end, label_on_line). Prefer labeled lines; then first or last."""
    if not cands:
        return None
    labeled = [c for c in cands if c[3]] or cands
    return labeled[-1] if last else labeled[0]


def locate(page: Page, inv: Invoice) -> dict[str, list[Box]]:
    """Field name -> boxes (in page.image coordinates). Fields that can't be found are left out."""
    if not page.rows or page.to_image is None:
        return {}
    lines = [_Line(r) for r in page.rows if r]
    found: dict[str, list[tuple[int, int, int]]] = {}

    def put(name: str, hit) -> None:
        if hit is not None:
            found.setdefault(name, []).append(hit[:3])

    # amounts: the rules keep the last labeled match, so do the same
    for name, label in AMOUNT_LABELS.items():
        value = getattr(inv, name)
        if not value:
            continue
        cands = []
        for i, ln in enumerate(lines):
            has_label = re.search(label, ln.compact) is not None
            for m in NUMBER.finditer(ln.text):
                v = parse_amount(m.group())
                if v is not None and abs(v - value) < 0.005:
                    cands.append((i, m.start(), m.end(), has_label))
        put(name, _pick(cands, last=True))

    # CUITs
    for name in ("emisor_cuit", "receptor_cuit"):
        value = getattr(inv, name)
        if not value:
            continue
        cands = []
        for i, ln in enumerate(lines):
            for m in CUIT_RE.finditer(ln.text):
                if "".join(m.groups()) == value:
                    cands.append((i, m.start(), m.end(), "cuit" in ln.compact or "c.u.i.t" in ln.compact))
        put(name, _pick(cands))

    # dates
    date_re = re.compile(DATE)
    for name, good, bad in (("fecha_emision", r"fecha|emisi", r"vto|venc|hasta|desde|inicio|pago|cae"),
                            ("cae_vencimiento", r"cae", r"$^")):
        value = getattr(inv, name)
        if not value:
            continue
        cands = []
        for i, ln in enumerate(lines):
            if re.search(bad, ln.compact) and not (name == "fecha_emision" and "emisi" in ln.compact):
                continue
            for m in date_re.finditer(ln.text):
                if parse_date(*m.groups()) == value:
                    cands.append((i, m.start(), m.end(), re.search(good, ln.compact) is not None))
        put(name, _pick(cands))

    # CAE
    if inv.cae:
        cands = [(i, m.start(), m.end(), "cae" in ln.compact or "c.a.e" in ln.compact)
                 for i, ln in enumerate(lines) for m in re.finditer(r"(?<!\d)\d{14}(?!\d)", ln.text)
                 if m.group() == inv.cae]
        put("cae", _pick(cands))

    # point of sale and number: "0004-00076009", or each one after its label
    if inv.punto_venta is not None and inv.numero is not None:
        hit = None
        for i, ln in enumerate(lines):
            for m in re.finditer(r"(?<!\d)(\d{4,5})\s?-\s?(\d{8})(?!\d)", ln.text):
                if int(m.group(1)) == inv.punto_venta and int(m.group(2)) == inv.numero:
                    hit = (i, m.start(), m.end())
                    break
            if hit:
                break
        if hit:
            put("comprobante", hit)
        else:
            for value, label in ((inv.punto_venta, r"punto|pto"), (inv.numero, r"nro|numero|n°")):
                cands = [(i, m.start(), m.end(), re.search(label, ln.compact) is not None)
                         for i, ln in enumerate(lines) for m in re.finditer(r"\d+", ln.text)
                         if int(m.group()) == value and len(m.group()) >= 4]
                put("comprobante", _pick([c for c in cands if c[3]]))

    # names: accent-, case- and space-insensitive search
    for name, label in (("emisor_razon_social", r"razonsocial"), ("receptor_razon_social", r"cliente|razonsocial|senor")):
        value = getattr(inv, name)
        if not value:
            continue
        target = compact(value)
        cands = []
        for i, ln in enumerate(lines):
            k = ln.folded.find(target)
            if k >= 0:
                start, end = ln.folded_index[k], ln.folded_index[k + len(target) - 1] + 1
                cands.append((i, start, end, re.search(label, ln.compact) is not None))
        if name == "receptor_razon_social":
            cands = [c for c in cands if c[3]] or cands[-1:]
        put(name, _pick(cands))

    boxes: dict[str, list[Box]] = {}
    for name, hits in found.items():
        for i, start, end in hits:
            r = lines[i].rect(start, end)
            if r is None:
                continue
            x0, y0, x1, y1 = r
            pad = 0.18 * (y1 - y0)
            corners = [(x0 - pad, y0 - pad), (x1 + pad, y0 - pad), (x1 + pad, y1 + pad), (x0 - pad, y1 + pad)]
            boxes.setdefault(name, []).append(map_points(page.to_image, corners))
    if page.qr and page.qr.get("_box"):
        boxes["qr"] = [page.qr["_box"]]
    return boxes


def to_flattened(page: Page, boxes: dict[str, list[Box]]) -> dict[str, list[Box]]:
    """The same boxes on the straightened page (page.flattened)."""
    if page.flattened is None or page.to_image is None:
        return {}
    back = np.linalg.inv(page.to_image)
    return {k: [map_points(back, b) for b in v] for k, v in boxes.items()}
