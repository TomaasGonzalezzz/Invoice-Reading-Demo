"""The demo batch: 20 fictitious purchase invoices as they reach a small business.

Digital PDFs from e-mail, scanner images and crooked phone photos (desk, shadow, angle), all
addressed to the same fictitious company. Five carry a problem the checks must catch: a total that
doesn't add up, an invalid CUIT, a QR that disagrees with the printed amount, an unreadable photo
and the same invoice sent twice (PDF by e-mail and a photo by WhatsApp).

Every name is made up and every CUIT is built from a high number range so that it is unlikely to
belong to anyone; check digits are valid except where the invalid CUIT is the point.

    python -m facturas.demo_set --out demo/lote
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np

from .afip import cuit_check_digit
from .synth import ITEMS, Spec, apply_trap, ground_truth, rasterize, render_pdf, simulate_scan

BUYER = ("Pyme Ejemplo S.A.", "Av. Siempre Viva 742 - Pilar, Buenos Aires")
# Names without accents: OCR sometimes drops them ("Ferreteria"), and the QR can't confirm names.
SUPPLIERS = [
    "Distribuidora Ejemplo S.A.", "Herrajes Ficticios S.R.L.", "Transportes Demo S.A.S.",
    "Metales Modelo S.A.", "Insumos de Prueba S.R.L.", "Envases Ejemplo S.A.",
    "Electricidad Ficticia S.R.L.", "Pinturas Demo S.A.", "Fletes de Muestra S.A.S.",
    "Servicios Ejemplo S.R.L.", "Embalajes Ficticios S.A.", "Talleres Demo S.R.L.",
]
STREETS = ["Calle Falsa", "Av. Ejemplo", "Pasaje Ficticio", "Calle de Prueba", "Av. Modelo"]

# (file name, supplier index, layout, capture, problem)
PLAN = [
    ("FC-A-00003-00004127.pdf", 0, "afip", "pdf", None),
    ("IMG-20261103-WA0007.jpg", 1, "moderno", "foto", None),
    ("factura_metales_modelo.pdf", 3, "compacto", "pdf", None),
    ("IMG-20261103-WA0012.jpg", 2, "afip", "foto", "total_no_cierra"),
    ("Escaneo_0001.jpg", 4, "moderno", "scan", None),
    ("FC_00005-00012890.pdf", 5, "moderno", "pdf", None),
    ("IMG-20261104-WA0003.jpg", 6, "compacto", "foto", None),
    ("Factura_Pinturas_Demo.pdf", 7, "afip", "pdf", "cuit_invalido"),
    ("IMG-20261104-WA0015.jpg", 8, "moderno", "foto", None),
    ("Escaneo_0002.jpg", 9, "afip", "scan", "qr_distinto"),
    ("NC-A-00002-00000318.pdf", 10, "compacto", "pdf", "nota_credito"),
    ("IMG-20261105-WA0001.jpg", 11, "afip", "foto", None),
    ("IMG-20261105-WA0009.jpg", 0, "moderno", "foto", "ilegible"),
    ("FC-A-00001-00000877.pdf", 2, "afip", "pdf", None),
    ("Escaneo_0003.jpg", 1, "compacto", "scan", None),
    ("IMG-20261106-WA0004.jpg", 5, "moderno", "foto", "duplicada"),  # photo of the 6th file
    ("IMG-20261106-WA0011.jpg", 4, "moderno", "foto", None),
    ("FC_00002-00000641.pdf", 6, "moderno", "pdf", None),
    ("IMG-20261107-WA0002.jpg", 9, "compacto", "foto", None),
    ("FC-A-00004-00002233.pdf", 11, "afip", "pdf", None),
]
DUPLICATE_OF = {15: 5}
# On this scan the OCR reads "Percepción IIBB" as "Percepcion lIIBB" and loses the amount, so the
# invoice goes to review. The demo shows a perception elsewhere; the miss is noted in EVALUATION.md.
NO_PERCEPTION = {4}


def fictitious_cuit(rng: random.Random, prefix: str = "30") -> str:
    """CUIT with a valid check digit in the 30-9xxxxxxx range (companies are numbered far below it)."""
    while True:
        body = prefix + "9" + "".join(str(rng.randint(0, 9)) for _ in range(7))
        dv = cuit_check_digit(body)
        if dv is not None:
            return body + str(dv)


def build(rng: random.Random, idx: int, supplier: int, layout: str, credit_note: bool,
          supplier_cuits: list[str], buyer_cuit: str, perception: bool = True) -> Spec:
    fecha = date(2026, 10, 1) + timedelta(days=rng.randint(0, 36))
    items = []
    for desc, lo, hi in rng.sample(ITEMS, rng.randint(1, 4)):
        rate = 21.0 if rng.random() < 0.8 else 10.5
        items.append((desc, rng.randint(1, 10), round(rng.uniform(lo, hi), 2), rate))
    base21 = round(sum(q * p for _, q, p, r in items if r == 21.0), 2)
    base105 = round(sum(q * p for _, q, p, r in items if r == 10.5), 2)
    neto = round(base21 + base105, 2)
    iva21 = round(base21 * 0.21, 2) if base21 else 0.0
    iva105 = round(base105 * 0.105, 2) if base105 else 0.0
    otros = round(neto * 0.03, 2) if rng.random() < 0.3 and perception else 0.0
    return Spec(
        id=f"demo_{idx:02d}", layout=layout, tipo="Nota de Crédito" if credit_note else "Factura", letra="A",
        punto_venta=rng.choice([1, 2, 3, 4, 5]), numero=rng.randint(100, 15_000), fecha=fecha,
        emisor_cuit=supplier_cuits[supplier], emisor_nombre=SUPPLIERS[supplier],
        emisor_iva="IVA Responsable Inscripto",
        emisor_domicilio=f"{rng.choice(STREETS)} {rng.randint(100, 3000)} - Buenos Aires",
        receptor_cuit=buyer_cuit, receptor_nombre=BUYER[0], receptor_iva="IVA Responsable Inscripto",
        receptor_domicilio=BUYER[1], items=items, neto=neto, iva21=iva21, iva105=iva105, otros=otros,
        iva_contenido=None, total=round(neto + iva21 + iva105 + otros, 2),
        cae="7" + "".join(str(rng.randint(0, 9)) for _ in range(13)), cae_vto=fecha + timedelta(days=10),
    )


# ---------------------------------------------------------------- phone photo

def _desk(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    """A plain wooden desk: warm base color and soft horizontal grain."""
    noise = cv2.resize(rng.normal(0, 1, (max(h // 40, 2), max(w // 400, 2))).astype(np.float32), (w, h),
                       interpolation=cv2.INTER_CUBIC)
    yy = np.mgrid[0:h, 0:w][0].astype(np.float32)
    grain = 0.5 + 0.5 * np.sin(2 * np.pi * (yy / rng.uniform(30, 55) + 0.8 * noise))
    fine = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), sigmaX=6, sigmaY=0.8)
    base = np.array(rng.choice([(62, 98, 140), (70, 110, 152), (58, 84, 118)]), np.float32)  # BGR
    shade = 0.86 + 0.10 * grain + 0.03 * fine
    return np.clip(base[None, None, :] * shade[..., None], 0, 255)


def phone_photo(page: np.ndarray, rng: np.random.Generator, angle: float, shadow: bool = True,
                long_side: int = 2000) -> np.ndarray:
    """A sheet photographed on a desk: rotated, a little tilted, uneven light and a soft shadow."""
    h, w = page.shape[:2]
    pad_x, pad_y = int(0.13 * w), int(0.09 * h)
    H2, W2 = h + 2 * pad_y, w + 2 * pad_x
    desk = _desk(H2, W2, rng)

    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    c = np.float32([W2 / 2, H2 / 2])
    rect = src - np.float32([w / 2, h / 2])
    t = np.deg2rad(angle)
    rot = np.float32([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    dst = rect @ rot.T * 0.97 + c
    keystone = rng.uniform(0.02, 0.045) * w  # phone tilted back: the top of the sheet looks narrower
    dst[0, 0] += keystone
    dst[1, 0] -= keystone
    dst += rng.uniform(-0.012, 0.012, (4, 2)).astype(np.float32) * [w, h]
    m = cv2.getPerspectiveTransform(src, dst)

    paper = page.astype(np.float32) * np.float32([0.95, 0.97, 1.0])  # warm indoor light
    warped = cv2.warpPerspective(paper, m, (W2, H2), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), m, (W2, H2))
    # the sheet's own soft shadow on the desk
    drop = cv2.GaussianBlur(np.roll(mask, (int(0.006 * H2), int(0.004 * W2)), (0, 1)), (0, 0), 0.008 * W2)
    out = desk * (1 - 0.45 * drop[..., None])
    out = out * (1 - mask[..., None]) + warped * mask[..., None]

    # light: a gradient across the frame and a vignette
    yy, xx = np.mgrid[0:H2, 0:W2].astype(np.float32)
    a = rng.uniform(0, 2 * np.pi)
    light = 0.86 + 0.16 * ((np.cos(a) * xx / W2 + np.sin(a) * yy / H2) + 1) / 2
    r2 = ((xx - W2 / 2) / W2) ** 2 + ((yy - H2 / 2) / H2) ** 2
    light *= 1 - 0.25 * r2
    if shadow:  # the phone's (or the hand's) shadow over one corner
        corner = rng.integers(0, 4)
        cx = W2 * (0.92 if corner in (1, 2) else 0.08)
        cy = H2 * (0.9 if corner in (2, 3) else 0.1)
        blob = np.zeros((H2, W2), np.float32)
        cv2.ellipse(blob, (int(cx), int(cy)), (int(0.38 * W2), int(0.22 * H2)), rng.uniform(-30, 30), 0, 360, 1, -1)
        blob = cv2.GaussianBlur(blob, (0, 0), 0.07 * W2)
        light *= 1 - 0.27 * blob
    out = out * light[..., None]
    out = cv2.GaussianBlur(out, (0, 0), 0.7)
    out = np.clip(out + rng.normal(0, 3.0, out.shape), 0, 255).astype(np.uint8)
    s = long_side / max(out.shape[:2])
    return cv2.resize(out, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


def unreadable(photo: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Moved while shooting, in a dim room: the sheet is there but no text survives."""
    k = 41
    kernel = np.zeros((k, k), np.float32)
    kernel[k // 2, :] = 1
    rot = cv2.getRotationMatrix2D((k / 2, k / 2), rng.uniform(20, 40), 1)
    kernel = cv2.warpAffine(kernel, rot, (k, k))
    kernel /= kernel.sum()
    out = cv2.filter2D(photo.astype(np.float32), -1, kernel) * 0.55
    return np.clip(out + rng.normal(0, 6, out.shape), 0, 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("demo/lote"))
    ap.add_argument("--gt", type=Path, default=None, help="where to save the ground truth (JSON)")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    nrng = np.random.default_rng(args.seed)
    supplier_cuits = [fictitious_cuit(rng, "30" if i % 3 else "33") for i in range(len(SUPPLIERS))]
    buyer_cuit = fictitious_cuit(rng)
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    work = args.out.parent / "_pdf_tmp"
    work.mkdir(exist_ok=True)

    truth = {}
    specs: list[Spec] = []
    for i, (name, supplier, layout, capture, problem) in enumerate(PLAN):
        if i in DUPLICATE_OF:
            spec = specs[DUPLICATE_OF[i]]
        else:
            spec = build(rng, i + 1, supplier, layout, problem == "nota_credito", supplier_cuits, buyer_cuit,
                         perception=i not in NO_PERCEPTION)
            if problem in ("total_no_cierra", "cuit_invalido", "qr_distinto"):
                apply_trap(spec, problem, rng)
        specs.append(spec)
        pdf = work / f"{spec.id}.pdf"
        render_pdf(spec, pdf)
        target = args.out / name
        if capture == "pdf":
            shutil.copy(pdf, target)
        elif capture == "scan":
            cv2.imwrite(str(target), simulate_scan(rasterize(pdf, 150), nrng), [cv2.IMWRITE_JPEG_QUALITY, 80])
        else:
            angle = float(nrng.choice([-1, 1]) * nrng.uniform(2.5, 7))
            photo = phone_photo(rasterize(pdf, 150), nrng, angle)
            if problem == "ilegible":
                photo = unreadable(photo, nrng)
            cv2.imwrite(str(target), photo, [cv2.IMWRITE_JPEG_QUALITY, 85])
        truth[name] = {"problema": problem, "factura": ground_truth(spec)}
    shutil.rmtree(work)
    if args.gt:
        args.gt.write_text(json.dumps(truth, ensure_ascii=False, indent=2), "utf-8")
    print(f"{len(PLAN)} comprobantes en {args.out}/")


if __name__ == "__main__":
    main()
