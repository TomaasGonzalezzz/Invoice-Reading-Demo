"""Benchmark on an independent dataset: "facturas_argentinas_2" (Hugging Face, mauroibz).

200 Argentine invoices made by someone else, in 5 layouts I never saw while writing the rules,
with labels and a working QR. The set is split once (split.json): rules were adjusted looking
only at "dev"; "test" is reported without looking at its documents first.

    uv run python -m facturas.external --data ../external-data/facturas_argentinas_2 --part test
    uv run python -m facturas.external --data ... --part test --photo     # same docs as phone photos
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from .afip import is_valid_cuit
from .evaluate import same

FIELDS = ["letra", "punto_venta", "numero", "fecha_emision", "emisor_cuit", "emisor_razon_social",
          "receptor_cuit", "receptor_razon_social", "moneda", "neto_gravado", "iva_total", "otros_tributos",
          "total"]
CURRENCY = {"ARS": "PES", "USD": "DOL", "EUR": "EUR"}


def gold_from_label(lab: dict) -> dict:
    rec = str(lab["cuit_receptor"])
    return {
        "letra": lab["tipo_de_comprobante"],
        "punto_venta": lab["punto_de_venta"],
        "numero": lab["numero_de_comprobante"],
        "fecha_emision": lab["fecha_factura"],
        "emisor_cuit": f"{int(lab['cuit_emisor']):011d}",
        "emisor_razon_social": lab["razon_social_emisor"],
        "receptor_cuit": rec if len(rec) == 11 else None,  # 7-8 digits is a DNI, not a CUIT
        "receptor_razon_social": lab["razon_social_receptor"],
        "moneda": CURRENCY.get(lab["moneda"], lab["moneda"]),
        "neto_gravado": lab["importe_neto"],
        "iva_total": lab["impuestos_iva"],
        "otros_tributos": lab["impuestos_otros"],
        "total": lab["importe_bruto"],
    }


def photo_version(src: Path, cache: Path) -> Path:
    from .synth import simulate_photo

    out = cache / f"{src.stem}.jpg"
    if not out.exists():
        seed = int(hashlib.md5(src.stem.encode()).hexdigest(), 16) % 2**32
        img = cv2.imdecode(np.fromfile(str(src), np.uint8), cv2.IMREAD_COLOR)
        photo = simulate_photo(cv2.resize(img, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA),
                               np.random.default_rng(seed))
        cv2.imwrite(str(out), photo, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return out


def _run(path: str) -> dict:
    from .pipeline import process

    return process(Path(path)).model_dump()


# Reasons that come from how this dataset was generated, not from reading errors: its QR points to
# a non-government domain, it prints "Comprobante No Autorizado", many of its CUITs have invalid
# check digits, and its Factura B lack the "IVA contenido" line. The system is right to flag them.
def dataset_artifact(reason: str, gold: dict) -> bool:
    if reason.startswith("qr_no_oficial") or reason == "leyenda_comprobante_no_autorizado":
        return True
    if reason == "cuit_emisor_invalido" and not is_valid_cuit(gold["emisor_cuit"]):
        return True
    if reason == "cuit_receptor_invalido" and gold["receptor_cuit"] and not is_valid_cuit(gold["receptor_cuit"]):
        return True
    return reason == "falta:iva_contenido" and gold["letra"] == "B"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--part", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--photo", action="store_true", help="evaluate simulated phone photos of the same documents")
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--show-errors", action="store_true")
    args = ap.parse_args()

    labels = {l["documento_id"]: l for l in json.loads((args.data / "facturas.json").read_text("utf-8"))}
    meta = {json.loads(x)["documento_id"]: json.loads(x) for x in (args.data / "dataset_metadata.jsonl").read_text().splitlines()}
    split_file = args.data / "split.json"
    if not split_file.exists():  # the fixed split ships with the repo
        split_file = Path(__file__).resolve().parents[2] / "results" / "external_split.json"
    split = json.loads(split_file.read_text())
    ids = split["dev"] + split["test"] if args.part == "all" else split[args.part]

    files = [args.data / "jpg" / f"{i}.jpg" for i in ids]
    cond = "foto" if args.photo else "imagen"
    if args.photo:
        cache = args.data / "foto"
        cache.mkdir(exist_ok=True)
        files = [photo_version(f, cache) for f in files]

    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(_run, map(str, files)))

    hits = {f: [0, 0] for f in FIELDS}
    by_template = collections.defaultdict(lambda: [0, 0])
    reasons = collections.Counter()
    real_reasons = collections.Counter()
    fully, auto_ok, auto_wrong, qr = 0, 0, 0, 0
    errors = []
    for doc_id, r in zip(ids, results):
        gold = gold_from_label(labels[doc_id])
        wrong = [f for f in FIELDS if not same(f, r["factura"].get(f), gold[f])]
        for f in FIELDS:
            hits[f][0] += f not in wrong
            hits[f][1] += 1
        t = meta[doc_id]["template"]
        by_template[t][0] += not wrong
        by_template[t][1] += 1
        fully += not wrong
        qr += r["qr_leido"]
        reasons.update(m.split(" (")[0] for m in r["motivos"])
        real = [m for m in r["motivos"] if not dataset_artifact(m, gold)]
        real_reasons.update(real)
        if not real:  # would have been approved if the dataset's invoices were genuine
            auto_ok += not wrong
            auto_wrong += bool(wrong)
        if wrong:
            errors.append({"id": doc_id, "template": t,
                           "campos": {f: [r["factura"].get(f), gold[f]] for f in wrong}})

    n = len(ids)
    report = {
        "dataset": "mauroibz/facturas_argentinas_2 (Hugging Face)",
        "parte": args.part, "condicion": cond, "documentos": n,
        "precision_por_campo": {f: round(100 * h / t, 1) for f, (h, t) in hits.items()},
        "precision_campos_global": round(100 * sum(h for h, _ in hits.values()) / sum(t for _, t in hits.values()), 1),
        "documentos_100_correctos_pct": round(100 * fully / n, 1),
        "por_diseno_100_correctos": {t: f"{a}/{b}" for t, (a, b) in sorted(by_template.items())},
        "qr_leido_pct": round(100 * qr / n, 1),
        "marcados_para_revision_pct": round(100 * sum(bool(r["motivos"]) for r in results) / n, 1),
        "motivos": dict(reasons.most_common()),
        "si_fueran_genuinas": {
            "aprobados_automaticamente_pct": round(100 * (auto_ok + auto_wrong) / n, 1),
            "aprobados_con_error": auto_wrong,
            "motivos_de_lectura": dict(real_reasons.most_common()),
        },
        "segundos_promedio": round(sum(r["segundos"] for r in results) / n, 2),
        "errores": errors,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"externo_{args.part}_{cond}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"{args.part}/{cond}: campos {report['precision_campos_global']}%  docs 100% {report['documentos_100_correctos_pct']}%  "
          f"QR {report['qr_leido_pct']}%  revision {report['marcados_para_revision_pct']}%  "
          f"(si genuinas: aprob {report['si_fueran_genuinas']['aprobados_automaticamente_pct']}%, "
          f"con error {auto_wrong})  {report['segundos_promedio']} s/doc")
    print("por campo:", {k: v for k, v in report["precision_por_campo"].items() if v < 100})
    print("por diseño:", report["por_diseno_100_correctos"])
    if args.show_errors:
        for e in errors[:40]:
            print(" ", e)


if __name__ == "__main__":
    main()
