"""Rule-based field extraction from text lines (PDF text layer or OCR).

OCR often drops spaces ("FechadeEmision:19/02/2025") and confuses a few characters
("$" -> "S", "I.V.A." -> "LV.A."), so labels are matched on a compact, accent-free
version of each line and the patterns tolerate those confusions.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from .afip import VOUCHER_TYPES, only_digits
from .schema import Invoice

DATE = r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})"
# label, optional currency in parentheses ("Total (USD)"), separators, then the number;
# "s" is tolerated because OCR often reads "$" as "S"
# The number must not run into an "o" (OCR's favorite stand-in for 0): "2.0O5.500" must be
# rejected, not read as 2.0.
AMOUNT = r"(?:\((?:\$|s|ars|pes|usd|u\$s|dol|eur)\))?[:$s.]*\$?\s*(-?\d[\d.,]*\d|\d)(?![\d.,]*[o0-9])"

# Labels whose amount must be readable when the label is on the page. If OCR sees the label but
# not the number next to it, the field is missing for a reading reason, and a person should look.
AMOUNT_LABELS = {
    "neto_gravado": r"(?:importeneto|netogravado|netograv)",
    "iva_total": r"(?<![a-z])(?:i\.?v\.?a\.?|l\.?v\.?a\.?)\((?:\$|s|ars|usd|eur)\)",
    "total": r"(?<![a-z])(?:importe)?total(?:\(|:|\$|\d|$)",
}
IVA = r"(?:i\.?v\.?a\.?|l\.?v\.?a\.?|1\.?v\.?a\.?)"
RECEPTOR_HINT = re.compile(r"documento|receptor|cliente|se\w{1,2}or|apellido", re.I)
CUIT_RE = re.compile(r"(?<!\d)(\d{2})[-\s]?(\d{8})[-\s]?(\d)(?!\d)")


def strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def norm(s: str) -> str:
    return strip_accents(s).lower()


def compact(s: str) -> str:
    return re.sub(r"\s+", "", norm(s))


def parse_amount(raw: str) -> float | None:
    """Accept 1.234,56 / 1234,56 / 1234.56 / 1,234.56 and OCR mixes like 1.234.56."""
    s = raw.strip().replace(" ", "")
    neg = s.startswith("-")
    s = s.lstrip("-")
    seps = [i for i, ch in enumerate(s) if ch in ".,"]
    if seps and len(s) - seps[-1] - 1 == 2:
        whole, dec = s[: seps[-1]], s[seps[-1] + 1:]
    else:
        whole, dec = s, "0"
    whole = re.sub(r"[.,]", "", whole)
    if not whole.isdigit() or not dec.isdigit():
        return None
    value = float(f"{whole}.{dec}")
    return -value if neg else value


def parse_date(d: str, m: str, y: str) -> str | None:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def _last_amount(lines: list[str], label: str, skip: str | None = None) -> float | None:
    pattern = re.compile(label + AMOUNT)
    value = None
    for line in lines:
        if skip and re.search(skip, line):
            continue
        for m in pattern.finditer(line):
            v = parse_amount(m.group(1))
            if v is not None:
                value = v
    return value


SUFFIX = re.compile(r"(\s*)(s\.?\s?a\.?\s?s\.?|s\.?\s?r\.?\s?l\.?|s\.?\s?a\.?)\s*$", re.I)


def clean_name(name: str | None) -> str | None:
    """'Distribuidora Rio LujanS.A.s.' -> 'Distribuidora Rio Lujan S.A.S.'

    OCR also drops the space between words ('ServiciosEjemplo'); a capital letter right after a
    lowercase one marks where it was. (Names are scored ignoring spaces, so this only fixes display.)
    """
    if not name:
        return name
    name = re.sub(r"(?<=[a-záéíóúñü])(?=[A-ZÁÉÍÓÚÑÜ])", " ", name)
    m = SUFFIX.search(name)
    # only a real company suffix: dotted, or separated by a space ("Mesa" must stay "Mesa")
    if m and m.start() > 0 and ("." in m.group(2) or m.group(1)):
        letters = re.sub(r"[^a-z]", "", m.group(2).lower())
        name = name[: m.start()].rstrip() + " " + ".".join(letters.upper()) + "."
    return re.sub(r"\s{2,}", " ", name).strip()


def _segment_after(line: str, label: re.Pattern) -> str | None:
    m = label.search(line)
    if not m:
        return None
    value = re.split(r"\s{3,}", line[m.end():].strip())[0].strip(" :")
    return value or None


def labels_without_value(lines: list[str], inv: Invoice) -> list[str]:
    """Fields whose label is printed on the page but whose amount was not read."""
    c_lines = [compact(l) for l in lines]
    return [f for f, pat in AMOUNT_LABELS.items()
            if getattr(inv, f) is None and any(re.search(pat, cl) for cl in c_lines)]


def extract(lines: list[str]) -> Invoice:
    c_lines = [compact(l) for l in lines]
    n_lines = [norm(l) for l in lines]
    whole = "\n".join(c_lines)
    inv = Invoice()

    # voucher type and letter
    if "notadecredito" in whole:
        inv.tipo = "Nota de Crédito"
    elif "notadedebito" in whole:
        inv.tipo = "Nota de Débito"
    elif "factura" in whole:
        inv.tipo = "Factura"
    for cl in c_lines:
        m = re.search(r"c[o0a][dpg][a-z]{0,4}\.?[:.]?(\d{2,3})(?!\d)", cl)
        if m and int(m.group(1)) in VOUCHER_TYPES:
            kind, letter = VOUCHER_TYPES[int(m.group(1))]
            inv.letra = letter
            inv.tipo = inv.tipo or kind
            break
    if inv.letra is None:
        m = re.search(r"(?:factura|notadecredito|notadedebito)([abcm])(?![a-z])", whole)
        if m:
            inv.letra = m.group(1).upper()
    if inv.letra is None:
        for nl in n_lines:
            if re.search(r"factura|nota de", nl):
                m = re.search(r"(?:^|\s)([abcm])(?:\s|$)", nl)
                if m:
                    inv.letra = m.group(1).upper()
                    break
    head = [l.strip() for l in lines[:15]]
    if inv.letra is None:  # the big letter box often comes out as a line of its own
        inv.letra = next((h for h in head if h in {"A", "B", "C", "M"}), None)
    if inv.letra is None:  # ...or only the code under it survives ("001")
        code = next((int(h) for h in head if re.fullmatch(r"0{0,2}(1|2|3|6|7|8|11|12|13|51)", h)), None)
        if code:
            inv.tipo, inv.letra = inv.tipo or VOUCHER_TYPES[code][0], VOUCHER_TYPES[code][1]

    # point of sale and number: "00025-00000333" is unambiguous, so it wins over labels
    for cl in c_lines:
        m = re.search(r"(?<!\d)(\d{4,5})-(\d{8})(?!\d)", cl)
        if m:
            inv.punto_venta, inv.numero = int(m.group(1)), int(m.group(2))
            break
    for cl in c_lines:
        m = re.search(r"(?:puntodeventa|pto\.?vta\.?)[:.]?(\d{1,5})", cl)
        if m and inv.punto_venta is None:
            inv.punto_venta = int(m.group(1))
        m = re.search(r"(?:comp\.?)?nro\.?[:.]?(\d{1,8})(?![\d-])", cl)
        if m and inv.numero is None and "cae" not in cl:
            inv.numero = int(m.group(1))

    # dates
    for cl in c_lines:
        if re.search(r"inicio|vto|venc|desde|hasta|pago|periodo", cl):
            continue
        m = re.search(r"fecha[a-z]{0,10}[:.]?" + DATE, cl)
        if m:
            inv.fecha_emision = parse_date(*m.groups())
            break
    for cl in c_lines:
        if re.search(r"c\.?a\.?e", cl):
            m = re.search(r"v(?:to|enc)[a-z.]*[^0-9]{0,15}?" + DATE, cl)
            if m:
                inv.cae_vencimiento = parse_date(*m.groups())
            m = re.search(r"(?<!\d)(\d{14})(?!\d)", cl)
            if m and inv.cae is None:
                inv.cae = m.group(1)

    # Who is who. A field belongs to the customer when its own text segment says so
    # ("Documento: CUIT ...", "Cliente: ..."), or when it sits below the customer block's start.
    # Layout order alone is not enough: OCR can drop the issuer's small print, and two-column
    # layouts put both parties on the same lines.
    razon = re.compile(r"raz.n\s*social\s*:", re.I)
    receptor_labels = re.compile(r"(?:apellido\s*y\s*nombre\s*/\s*raz.n\s*social|cliente|se\w{1,2}or\(?es\)?)\s*:", re.I)
    receptor_start = next((i for i, l in enumerate(lines)
                           if receptor_labels.search(l) or norm(l).strip().startswith("receptor")), None)

    def in_receptor(i: int, segment: str, nth_on_line: int = 0) -> bool:
        if RECEPTOR_HINT.search(segment):
            return True
        if nth_on_line > 0:  # second party on the same line of a two-column layout
            return True
        return receptor_start is not None and i >= receptor_start

    emisor_cuits, receptor_cuits = [], []
    for i, line in enumerate(n_lines):
        for seg in re.split(r"\s{3,}", line):
            for m in CUIT_RE.finditer(seg):
                d = "".join(m.groups())
                if d[:2] not in {"20", "23", "24", "27", "30", "33", "34"}:
                    continue
                (receptor_cuits if in_receptor(i, seg[: m.start()]) else emisor_cuits).append(d)
    if receptor_start is None and not receptor_cuits and len(emisor_cuits) > 1:
        receptor_cuits = [c for c in emisor_cuits[1:] if c != emisor_cuits[0]]
        emisor_cuits = emisor_cuits[:1]
    inv.emisor_cuit = emisor_cuits[0] if emisor_cuits else None
    inv.receptor_cuit = next((c for c in receptor_cuits if c != inv.emisor_cuit), None)

    # names
    for i, line in enumerate(lines):
        segs = [s for s in re.split(r"\s{3,}", line.strip()) if razon.search(s) or receptor_labels.search(s)]
        for k, seg in enumerate(segs):
            label = receptor_labels if receptor_labels.search(seg) else razon
            value = _segment_after(seg, label)
            if not value:
                continue
            if label is receptor_labels or in_receptor(i, seg[: label.search(seg).start()], k):
                if inv.receptor_razon_social is None:
                    inv.receptor_razon_social = None if compact(value) == "consumidorfinal" else value
            elif inv.emisor_razon_social is None:
                inv.emisor_razon_social = value
    if inv.emisor_razon_social is None:  # no label: the issuer's name is the page title
        for line in lines:
            nl = norm(line).strip()
            if nl in {"original", "duplicado", "triplicado", "emisor"} or not nl:
                continue
            first = re.split(r"\s{3,}", line.strip())[0]
            first = re.sub(r"\s+[ABCM]$", "", first)
            inv.emisor_razon_social = first.strip()
            break

    # amounts (the same labels on any letter; what each letter must have is the validator's job)
    inv.total = _last_amount(c_lines, r"(?<![a-z])(?:importe)?total")
    inv.neto_gravado = _last_amount(c_lines, r"(?:importe)?neto(?:gra?v?(?:ado)?)?\.?")
    if inv.neto_gravado is None and inv.letra in ("A", "M"):
        inv.neto_gravado = _last_amount(c_lines, r"(?<![a-z])subtotal", skip=r"c/iva")
    inv.iva_21 = _last_amount(c_lines, IVA + r"21(?:[.,]0+)?%")
    inv.iva_10_5 = _last_amount(c_lines, IVA + r"10[.,]50?%")
    generic_iva = _last_amount(c_lines, r"(?<![a-z])" + IVA + r"(?=\(|:)")  # "IVA ($)", "IVA:"
    if generic_iva is not None:
        inv.iva_total = generic_iva
    elif inv.iva_21 is not None or inv.iva_10_5 is not None:
        inv.iva_total = round((inv.iva_21 or 0) + (inv.iva_10_5 or 0), 2)
    inv.otros_tributos = _last_amount(
        c_lines, r"(?:(?:importe)?otrostributos|percepcion(?:iibb)?|perc\.?iibb|(?<![a-z])otros(?=\(|:))")
    if inv.letra == "B":
        inv.iva_contenido = _last_amount(c_lines, IVA + r"conten[a-z]{1,3}(?:\(ley27\.?743\))?")

    if inv.total is None:
        # The "TOTAL" label is sometimes lost. Pick a printed amount that closes the arithmetic;
        # this only chooses among values on the page, it never computes one.
        expected = None
        if inv.neto_gravado is not None and inv.iva_total:
            expected = inv.neto_gravado + inv.iva_total + (inv.otros_tributos or 0)
        elif inv.iva_contenido:
            expected = inv.iva_contenido * 1.21 / 0.21
        if expected:
            tol = 0.05 if inv.neto_gravado is not None else max(0.05, 0.002 * expected)
            for cl in reversed(c_lines):
                hits = [parse_amount(a) for a in re.findall(r"\d[\d.,]*\d", cl)]
                match = next((h for h in hits if h is not None and abs(h - expected) <= tol), None)
                if match is not None:
                    inv.total = match
                    break

    inv.emisor_razon_social = clean_name(inv.emisor_razon_social)
    inv.receptor_razon_social = clean_name(inv.receptor_razon_social)
    if inv.total is not None:
        if re.search(r"moneda:?(?:eur|euro)|\(eur\)", whole):
            inv.moneda = "EUR"
        elif re.search(r"moneda:?(?:usd|dol)|dolar|\(usd\)|u\$s", whole):
            inv.moneda = "DOL"
        else:
            inv.moneda = "PES"
    if inv.emisor_cuit:
        inv.emisor_cuit = only_digits(inv.emisor_cuit)
    return inv
