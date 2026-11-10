"""Batch-level logic: duplicates, the review queue, plain-language checks, Excel and CSV export."""

import csv
import io

from openpyxl import load_workbook

from facturas.export import to_csv, to_xlsx
from facturas.reader import Token, group_lines, group_rows, row_text
from facturas.revision import batch_summary, checks, find_duplicates, review_batch
from facturas.schema import Invoice, Result

GOOD = dict(tipo="Factura", letra="A", punto_venta=3, numero=4127, fecha_emision="2026-10-12",
            emisor_cuit="30500010912", receptor_cuit="20123456786", neto_gravado=1000.0, iva_21=210.0,
            iva_total=210.0, total=1210.0, cae="70417054367476", cae_vencimiento="2026-10-22")


def result(name: str, motivos=(), qr=True, **changes) -> Result:
    inv = Invoice(**{**GOOD, **changes})
    return Result(archivo=name, estado="revisar" if motivos else "ok", motivos=list(motivos), factura=inv,
                  qr_leido=qr, qr_total=inv.total if qr else None)


def by_id(items):
    return {c.id: c for c in items}


def test_duplicates_flag_the_second_copy_only():
    rs = [result("a.pdf"), result("b.jpg", numero=99), result("foto_de_a.jpg")]
    assert find_duplicates(rs) == {2: 0}
    rows = review_batch(rs)
    assert [r.estado for r in rows] == ["ok", "ok", "revisar"]
    assert rows[2].motivo == "Factura repetida"
    assert "a.pdf" in by_id(rows[2].checks)["duplicada"].detalle


def test_same_number_from_another_issuer_is_not_a_duplicate():
    rs = [result("a.pdf"), result("b.pdf", emisor_cuit="30715012343")]
    assert find_duplicates(rs) == {}


def test_review_queue_reasons_in_plain_language():
    wrong_total = result("t.jpg", ["total_no_cierra"], total=2210.0)
    c = by_id(checks(wrong_total))
    assert c["cuentas"].estado == "error" and "sobran $ 1.000,00" in c["cuentas"].detalle
    assert c["cuit"].estado == "ok" and c["qr"].estado == "ok"

    qr = result("q.jpg", ["qr_no_coincide:total"])
    qr.qr_total = 1331.0
    assert "$ 1.331,00" in by_id(checks(qr))["qr"].detalle

    bad_cuit = result("c.pdf", ["qr_no_coincide:emisor_cuit", "cuit_emisor_invalido"], emisor_cuit="30500010913")
    bad_cuit.qr_cuit = "30500010912"
    rows = review_batch([bad_cuit])
    assert rows[0].motivo == "CUIT inválido (y 1 control más)"
    assert "30-50001091-3" in by_id(rows[0].checks)["cuit"].detalle
    assert "registra el CUIT 30-50001091-2" in by_id(rows[0].checks)["qr"].detalle

    blurry = Result(archivo="x.jpg", estado="revisar", factura=Invoice(),
                    motivos=["falta:tipo", "falta:punto_venta", "falta:numero", "falta:fecha_emision",
                             "falta:emisor_cuit", "falta:total", "falta:cae", "sin_qr"])
    rows = review_batch([blurry])
    assert rows[0].motivo == "No se pudo leer" and len(rows[0].checks) == 1

    expired = result("e.pdf", ["cae_vence_antes_de_emision"], cae_vencimiento="2026-10-01")
    assert "01/10/2026" in by_id(checks(expired))["cae"].detalle


def test_every_validator_code_maps_to_a_failed_check():
    codes = ["falta:cae", "etiqueta_sin_valor:neto_gravado", "cuit_emisor_invalido", "cuit_receptor_invalido",
             "cae_formato", "total_no_cierra", "iva_21_no_coincide", "iva_mayor_al_27", "factura_a_sin_iva",
             "factura_c_con_iva", "iva_contenido_fuera_de_rango", "leyenda_comprobante_no_autorizado",
             "fecha_fuera_de_rango", "cae_vence_antes_de_emision", "sin_qr", "qr_ilegible",
             "qr_no_oficial (example.com)", "qr_no_coincide:numero (posible error de lectura)"]
    for code in codes:
        row = review_batch([result("x.pdf", [code])])[0]
        assert row.estado == "revisar", code
        assert row.motivo, code


def test_summary_counts_and_credit_notes():
    rows = review_batch([result("a.pdf"), result("nc.pdf", tipo="Nota de Crédito", numero=5),
                         result("t.jpg", ["total_no_cierra"], numero=6, total=9999.0)])
    s = batch_summary(rows)
    assert (s["total"], s["ok"], s["revisar"]) == (3, 2, 1)
    assert s["importe_ok"] == 0.0 and s["iva_ok"] == 0.0  # the credit note cancels the invoice


def test_excel_export():
    rows = review_batch([result("a.pdf"), result("t.jpg", ["total_no_cierra"], numero=6, total=2210.0),
                         result("a_otra_vez.jpg")])
    wb = load_workbook(io.BytesIO(to_xlsx(rows)))
    assert wb.sheetnames == ["Comprobantes", "Para revisar", "Resumen"]
    ws = wb["Comprobantes"]
    header = [c.value for c in ws[1]]
    assert header[:3] == ["Estado", "Motivo", "Fecha"] and header[-1] == "Archivo"
    first = {h: c.value for h, c in zip(header, ws[2])}
    assert first["Estado"] == "Lista para cargar" and first["CUIT emisor"] == "30-50001091-2"
    assert first["Total"] == 1210.0 and first["Fecha"].strftime("%d/%m/%Y") == "12/10/2026"
    assert ws.cell(3, 1).value == "Para revisar"
    queue = wb["Para revisar"]
    assert [queue.cell(r, 4).value for r in (2, 3)] == ["Las cuentas no cierran", "Factura repetida"]


def test_csv_export_only_ready_invoices():
    rows = review_batch([result("a.pdf"), result("t.jpg", ["total_no_cierra"], numero=6, total=2210.0)])
    text = to_csv(rows).decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert len(lines) == 2
    rec = dict(zip(lines[0], lines[1]))
    assert rec["Fecha"] == "12/10/2026" and rec["Total"] == "1210,00" and rec["Número"] == "00004127"


def test_row_text_spans_match_group_lines():
    tokens = [Token("CUIT:", 10, 10, 50, 20), Token("30-50001091-2", 55, 10, 160, 20),
              Token("Total", 300, 11, 340, 21), Token("1.210,00", 400, 11, 470, 21)]
    rows = group_rows(tokens)
    text, spans = row_text(rows[0])
    assert group_lines(tokens) == [text]
    assert [text[a:b] for a, b in spans] == [t.text for t in rows[0]]
