"""Procesar una carpeta de facturas y exportar JSON + Excel.

    uv run python -m facturas entrada/ --out salida/ [--llm] [--model claude-haiku-5-5]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .export import to_csv, to_xlsx
from .pipeline import process
from .reader import IMAGE_EXT
from .revision import review_batch


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("entrada", type=Path, nargs="+", help="archivos o carpetas con PDF/JPG/PNG")
    ap.add_argument("--out", type=Path, default=Path("salida"))
    ap.add_argument("--llm", action="store_true", help="usar Claude para los documentos que quedan en revisión")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    files = []
    for p in args.entrada:
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT | {".pdf"})
        else:
            files.append(p)
    llm = None
    if args.llm:
        from .llm import LLMExtractor

        llm = LLMExtractor(args.model) if args.model else LLMExtractor()

    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        r = process(f, llm=llm)
        results.append(r)
        (args.out / f"{f.stem}.json").write_text(r.model_dump_json(indent=2), "utf-8")
    rows = review_batch(results)  # adds the checks that need the whole batch (duplicates)
    for row in rows:
        flag = "OK     " if row.estado == "ok" else "REVISAR"
        print(f"{flag} {row.result.archivo}  {row.motivo or ''}".rstrip())
    to_xlsx(rows, args.out / "comprobantes.xlsx")
    (args.out / "comprobantes_para_importar.csv").write_bytes(to_csv(rows))
    ok = sum(r.estado == "ok" for r in rows)
    print(f"\n{ok}/{len(rows)} listos para cargar, {len(rows) - ok} para revisar -> {args.out / 'comprobantes.xlsx'}")
    if llm:
        print(f"Tokens Claude: {llm.input_tokens} entrada, {llm.output_tokens} salida")


if __name__ == "__main__":
    main()
