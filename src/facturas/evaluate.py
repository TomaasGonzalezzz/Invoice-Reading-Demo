"""Run the pipeline on the synthetic set and compare against ground truth.

    uv run python -m facturas.evaluate --data data --out results [--llm] [--model claude-haiku-5-5]
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .rules import strip_accents
from .schema import MONEY_FIELDS

CONDITIONS = {"pdf": "PDF digital", "scan": "Escaneo", "foto": "Foto de celular"}
EVAL_FIELDS = ["tipo", "letra", "punto_venta", "numero", "fecha_emision", "emisor_cuit", "emisor_razon_social",
               "receptor_cuit", "receptor_razon_social", "neto_gravado", "iva_21", "iva_10_5", "iva_total",
               "otros_tributos",
               "iva_contenido", "total", "cae", "cae_vencimiento"]
NAME_FIELDS = {"emisor_razon_social", "receptor_razon_social"}


def same(field: str, pred, gold) -> bool:
    if field in MONEY_FIELDS:
        p, g = pred or 0.0, gold or 0.0
        return abs(float(p) - float(g)) <= 0.01
    if field in NAME_FIELDS:
        key = lambda s: re.sub(r"[^a-z0-9]", "", strip_accents(s or "").lower())  # noqa: E731
        return key(pred) == key(gold)
    return (None if pred in ("", None) else str(pred)) == (None if gold is None else str(gold))


_LLM = None


def _init(use_llm: bool, model: str | None) -> None:
    global _LLM
    if use_llm:
        from .llm import LLMExtractor

        _LLM = LLMExtractor(model) if model else LLMExtractor()


def _run(path: str) -> dict:
    from .pipeline import process

    r = process(Path(path), llm=_LLM)
    out = r.model_dump()
    if _LLM is not None:
        out["_tokens"] = [_LLM.input_tokens, _LLM.output_tokens]
    return out


def evaluate(data: Path, out: Path, use_llm: bool, model: str | None, workers: int) -> dict:
    manifest = {m["id"]: m for m in json.loads((data / "manifest.json").read_text("utf-8"))}
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"conditions": {}}
    for cond in CONDITIONS:
        files = sorted((data / cond).glob("*.*"))
        with ProcessPoolExecutor(workers, initializer=_init, initargs=(use_llm, model)) as pool:
            results = list(pool.map(_run, map(str, files)))
        (out / f"predicciones_{cond}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")

        field_hits = {f: [0, 0] for f in EVAL_FIELDS}
        docs = {"total": 0, "ok_correct": 0, "ok_wrong": 0, "review": 0, "fully_correct": 0}
        traps, errors_in_ok, qr_read = [], [], 0
        for r in results:
            doc_id = Path(r["archivo"]).stem
            meta = manifest[doc_id]
            gold = json.loads((data / "gt" / f"{doc_id}.json").read_text("utf-8"))["factura"]
            qr_read += r["qr_leido"]
            if meta["trap"]:
                traps.append({"id": doc_id, "trap": meta["trap"], "detectada": r["estado"] == "revisar",
                              "motivos": r["motivos"]})
                continue
            wrong = [f for f in EVAL_FIELDS if not same(f, r["factura"].get(f), gold.get(f))]
            for f in EVAL_FIELDS:
                field_hits[f][0] += f not in wrong
                field_hits[f][1] += 1
            docs["total"] += 1
            docs["fully_correct"] += not wrong
            if r["estado"] == "revisar":
                docs["review"] += 1
            elif wrong:
                docs["ok_wrong"] += 1
                errors_in_ok.append({"id": doc_id, "campos": {f: [r["factura"].get(f), gold.get(f)] for f in wrong}})
            else:
                docs["ok_correct"] += 1

        n = docs["total"]
        report["conditions"][cond] = {
            "documentos": len(results),
            "qr_leido_pct": round(100 * qr_read / len(results), 1),
            "precision_por_campo": {f: round(100 * h / t, 1) for f, (h, t) in field_hits.items()},
            "precision_campos_global": round(100 * sum(h for h, _ in field_hits.values())
                                             / sum(t for _, t in field_hits.values()), 1),
            "documentos_100_correctos_pct": round(100 * docs["fully_correct"] / n, 1),
            "aprobados_automaticamente_pct": round(100 * (docs["ok_correct"] + docs["ok_wrong"]) / n, 1),
            "aprobados_con_error": docs["ok_wrong"],
            "a_revision_pct": round(100 * docs["review"] / n, 1),
            "conteo": docs,
            "trampas_detectadas": f"{sum(t['detectada'] for t in traps)}/{len(traps)}",
            "trampas": traps,
            "errores_en_aprobados": errors_in_ok,
            "segundos_promedio": round(sum(r["segundos"] for r in results) / len(results), 2),
        }
        if use_llm:
            tokens = [r.get("_tokens", [0, 0]) for r in results]
            report["conditions"][cond]["tokens_llm"] = tokens
        c = report["conditions"][cond]
        print(f"{CONDITIONS[cond]:16s} campos {c['precision_campos_global']:5.1f}%  "
              f"aprobadas {c['aprobados_automaticamente_pct']:5.1f}%  con error {c['aprobados_con_error']}  "
              f"revisión {c['a_revision_pct']:5.1f}%  trampas {c['trampas_detectadas']}  "
              f"{c['segundos_promedio']} s/doc")

    report["llm"] = model if use_llm else None
    name = "metricas_llm.json" if use_llm else "metricas.json"
    (out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--llm", action="store_true", help="usar Claude cuando las reglas dejan el documento en revisión")
    ap.add_argument("--model", default=None)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    evaluate(args.data, args.out, args.llm, args.model, args.workers)


if __name__ == "__main__":
    main()
