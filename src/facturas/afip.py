"""Argentine fiscal rules: CUIT check digit, voucher types and the AFIP/ARCA QR code.

Electronic invoices issued since 2021 must carry a QR code that points to
https://www.afip.gob.ar/fe/qr/?p=<base64 JSON>. The JSON holds the issuer CUIT,
point of sale, voucher type and number, date, total and CAE, so when the QR is
readable those fields do not depend on OCR at all.
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)

# Voucher codes from the AFIP table (only the ones a typical pyme receives).
VOUCHER_TYPES: dict[int, tuple[str, str]] = {
    1: ("Factura", "A"),
    2: ("Nota de Débito", "A"),
    3: ("Nota de Crédito", "A"),
    6: ("Factura", "B"),
    7: ("Nota de Débito", "B"),
    8: ("Nota de Crédito", "B"),
    11: ("Factura", "C"),
    12: ("Nota de Débito", "C"),
    13: ("Nota de Crédito", "C"),
    51: ("Factura", "M"),
}
VOUCHER_CODE = {v: k for k, v in VOUCHER_TYPES.items()}

QR_BASE_URL = "https://www.afip.gob.ar/fe/qr/"


def only_digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def cuit_check_digit(first10: str) -> int | None:
    """Return the check digit for the first 10 digits, or None if no valid digit exists."""
    total = sum(int(d) * w for d, w in zip(first10, CUIT_WEIGHTS))
    dv = 11 - total % 11
    if dv == 11:
        return 0
    if dv == 10:
        return None
    return dv


def is_valid_cuit(cuit: str | int | None) -> bool:
    digits = only_digits(str(cuit or ""))
    if len(digits) != 11 or digits[:2] not in {"20", "23", "24", "27", "30", "33", "34"}:
        return False
    return cuit_check_digit(digits[:10]) == int(digits[10])


def format_cuit(cuit: str | int) -> str:
    d = only_digits(str(cuit))
    return f"{d[:2]}-{d[2:10]}-{d[10:]}" if len(d) == 11 else str(cuit)


def voucher_code(kind: str, letter: str) -> int | None:
    return VOUCHER_CODE.get((kind, letter))


def encode_qr_url(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return f"{QR_BASE_URL}?p={base64.b64encode(raw).decode()}"


OFFICIAL_QR_HOSTS = ("afip.gob.ar", "arca.gob.ar")
QR_KEYS = {"cuit", "ptoVta", "tipoCmp", "nroCmp", "importe", "codAut"}


def is_official_host(host: str) -> bool:
    host = host.lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in OFFICIAL_QR_HOSTS)


def decode_qr_url(url: str) -> dict | None:
    """Parse an invoice QR URL (".../fe/qr/?p=<base64 JSON>").

    Returns the payload plus "_host" and "_oficial", or None if it does not look like an invoice QR.
    A QR that decodes fine but points to a non-government domain is kept: it is a fraud signal,
    not noise, and the validator reports it.
    """
    try:
        parsed = urlparse(url.strip())
        p = parse_qs(parsed.query).get("p", [None])[0]
        if not p:
            return None
        p = p.replace(" ", "+")  # parse_qs turns "+" into spaces
        p += "=" * (-len(p) % 4)  # some issuers drop the padding
        data = json.loads(base64.urlsafe_b64decode(p.replace("+", "-").replace("/", "_")).decode("utf-8"))
        if not isinstance(data, dict) or not QR_KEYS <= data.keys():
            return None
        data["_host"] = parsed.netloc
        data["_oficial"] = is_official_host(parsed.netloc)
        return data
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
