"""Figures for the README from results/metricas*.json.

    uv run python -m facturas.figures --results results --out figures
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#ffffff"
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
GOOD, CRITICAL, REVIEW = "#0ca30c", "#d03b3b", "#c3c2b7"  # status colors: ok / silent error / review
COND_LABEL = {"pdf": "PDF digital", "scan": "Escaneo", "foto": "Foto de celular"}


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=INK2, length=0)


def outcomes(runs: dict[str, dict], path: Path) -> None:
    """runs: {"Reglas + QR": metrics, "Reglas + QR + Claude": metrics}"""
    rows = []
    for method, m in runs.items():
        for cond in ("pdf", "scan", "foto"):
            c = m["conditions"][cond]["conteo"]
            n = c["total"]
            label = COND_LABEL[cond] if len(runs) == 1 else f"{COND_LABEL[cond]}\n{method}"
            rows.append((label, 100 * c["ok_correct"] / n, 100 * c["ok_wrong"] / n, 100 * c["review"] / n))
    rows.reverse()
    fig, ax = plt.subplots(figsize=(8.5, 0.62 * len(rows) + 1.3), facecolor=SURFACE)
    _style(ax)
    for i, (label, good, bad, rev) in enumerate(rows):
        left = 0
        for value, color, text_color in ((good, GOOD, "#ffffff"), (bad, CRITICAL, "#ffffff"), (rev, REVIEW, INK)):
            if value <= 0:
                continue
            ax.barh(i, value, left=left, height=0.62, color=color, edgecolor=SURFACE, linewidth=2)
            if value >= 7:
                ax.text(left + value / 2, i, f"{value:.0f} %", ha="center", va="center", color=text_color, fontsize=9)
            left += value
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], color=INK, fontsize=9.5)
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 25, 50, 75, 100], ["0 %", "25 %", "50 %", "75 %", "100 %"], color=MUTED, fontsize=8.5)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (GOOD, CRITICAL, REVIEW)]
    ax.legend(handles, ["Aprobada automáticamente y correcta", "Aprobada con algún campo mal",
                        "Enviada a revisión"], loc="lower center", bbox_to_anchor=(0.45, 1.0), ncol=3,
              frameon=False, fontsize=8.5, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def showcase(image_path: Path, prediction: dict, path: Path) -> None:
    """Photo on the left, extracted and validated fields on the right."""
    import cv2

    img = cv2.cvtColor(cv2.imread(str(image_path)), cv2.COLOR_BGR2RGB)
    fig = plt.figure(figsize=(12, 6.75), facecolor=SURFACE)
    ax_img = fig.add_axes([0.02, 0.04, 0.42, 0.92])
    ax_img.imshow(img)
    ax_img.axis("off")
    ax = fig.add_axes([0.48, 0.04, 0.5, 0.92])
    ax.axis("off")
    inv, src = prediction["factura"], prediction["fuentes"]
    ok = prediction["estado"] == "ok"
    ax.text(0, 0.97, "Foto de celular  →  datos validados", fontsize=15, color=INK, weight="bold", va="top")
    ax.text(0, 0.905, f"{'✓ Lista para cargar' if ok else '⚠ Para revisar'}   ·   QR leído y verificado"
            if prediction["qr_leido"] else "", fontsize=10.5, color=GOOD if ok else CRITICAL, va="top")
    money = lambda v: "—" if v is None else f"$ {v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")  # noqa: E731
    rows = [("Comprobante", f"{inv['tipo']} {inv['letra']}  {inv['punto_venta']:05d}-{inv['numero']:08d}", "numero"),
            ("Fecha", inv["fecha_emision"], "fecha_emision"),
            ("CUIT emisor", inv["emisor_cuit"], "emisor_cuit"),
            ("Emisor", inv["emisor_razon_social"], "emisor_razon_social"),
            ("CUIT receptor", inv["receptor_cuit"] or "Consumidor final", "receptor_cuit"),
            ("Neto gravado", money(inv["neto_gravado"]), "neto_gravado"),
            ("IVA 21 %", money(inv["iva_21"]), "iva_21"),
            ("Total", money(inv["total"]), "total"),
            ("CAE", inv["cae"], "cae"),
            ("Vto. CAE", inv["cae_vencimiento"], "cae_vencimiento")]
    y = 0.8
    for label, value, field in rows:
        ax.text(0, y, label, fontsize=10.5, color=INK2, va="center")
        ax.text(0.34, y, str(value), fontsize=11, color=INK, va="center", family="DejaVu Sans Mono")
        tag = src.get(field, "")
        tag = "QR + texto" if tag == "qr+texto" else ("QR" if tag.startswith("qr") else ("OCR" if tag == "ocr" else tag))
        ax.text(1.0, y, tag, fontsize=8.5, color=MUTED, va="center", ha="right")
        ax.plot([0, 1], [y - 0.034, y - 0.034], color=GRID, linewidth=0.8)
        y -= 0.068
    ax.text(0, 0.06, "Controles: dígito verificador de CUIT · Neto + IVA + tributos = Total · IVA 21 % del neto\n"
                     "· Formato y vencimiento del CAE · Cada dato del QR comparado con el texto impreso",
            fontsize=9, color=INK2, va="bottom", linespacing=1.5)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def batch_collage(folder: Path, names: list[tuple[str, str]], path: Path, height: int = 420) -> None:
    """A row of documents from the demo batch, each with its capture type underneath."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    def font(size: int, bold: bool = False):
        for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    thumbs = []
    for name, label in names:
        p = folder / name
        if p.suffix.lower() == ".pdf":
            img = pdfium.PdfDocument(str(p))[0].render(scale=110 / 72).to_pil().convert("RGB")
        else:
            img = Image.open(p).convert("RGB")
        img = img.resize((round(img.width * height / img.height), height), Image.LANCZOS)
        thumbs.append((img, label))
    gap, pad, label_h = 34, 40, 64
    width = sum(t.width for t, _ in thumbs) + gap * (len(thumbs) - 1) + 2 * pad
    canvas = Image.new("RGB", (width, height + label_h + 2 * pad), "white")
    shadow = Image.new("L", canvas.size, 0)
    draw_s = ImageDraw.Draw(shadow)
    x = pad
    for img, _ in thumbs:
        draw_s.rounded_rectangle((x + 4, pad + 8, x + img.width + 4, pad + img.height + 8), 10, fill=70)
        x += img.width + gap
    canvas.paste(Image.new("RGB", canvas.size, "#98a2b3"), mask=shadow.filter(ImageFilter.GaussianBlur(10)))
    draw = ImageDraw.Draw(canvas)
    x = pad
    for img, label in thumbs:
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), 8, fill=255)
        canvas.paste(img, (x, pad), mask)
        draw.rounded_rectangle((x, pad, x + img.width - 1, pad + img.height - 1), 8, outline="#e4e7ec", width=2)
        f = font(24, bold=True)
        tw = draw.textlength(label, font=f)
        draw.text((x + (img.width - tw) / 2, pad + height + 22), label, fill="#344054", font=f)
        x += img.width + gap
    canvas.save(path, quality=88)


DEMO_COLLAGE = [("FC-A-00003-00004127.pdf", "PDF por mail"), ("IMG-20261103-WA0007.jpg", "Foto de WhatsApp"),
                ("Escaneo_0001.jpg", "Escaneo"), ("IMG-20261104-WA0003.jpg", "Foto de WhatsApp"),
                ("factura_metales_modelo.pdf", "PDF por mail"), ("IMG-20261105-WA0001.jpg", "Foto de WhatsApp")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--out", type=Path, default=Path("figures"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    base = json.loads((args.results / "metricas.json").read_text("utf-8"))
    runs = {"Reglas + QR": base}
    llm_path = args.results / "metricas_llm.json"
    if llm_path.exists():
        runs["Reglas + QR + Claude"] = json.loads(llm_path.read_text("utf-8"))
    outcomes(runs, args.out / "resultados.png")
    from .pipeline import process

    sample = Path("samples/factura_a_foto.jpg")
    showcase(sample, process(sample).model_dump(), args.out / "ejemplo_foto.png")
    demo = Path("demo/lote")
    if demo.exists():
        batch_collage(demo, DEMO_COLLAGE, args.out / "lote.jpg")
    print(f"Figuras en {args.out}/")


if __name__ == "__main__":
    main()
