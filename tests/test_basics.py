from facturas.afip import decode_qr_url, encode_qr_url, is_valid_cuit
from facturas.rules import extract, parse_amount
from facturas.validate import check, edit_distance


def test_cuit_check_digit():
    assert is_valid_cuit("30-50001091-2")
    assert is_valid_cuit("20-12345678-6")
    assert not is_valid_cuit("30-50001091-3")
    assert not is_valid_cuit("12345")


def test_qr_roundtrip():
    payload = {"ver": 1, "fecha": "2026-05-14", "cuit": 30500010912, "ptoVta": 3, "tipoCmp": 1,
               "nroCmp": 4567, "importe": 1210.0, "moneda": "PES", "ctz": 1, "tipoDocRec": 99,
               "nroDocRec": 0, "tipoCodAut": "E", "codAut": 70417054367476}
    decoded = decode_qr_url(encode_qr_url(payload))
    assert {k: decoded[k] for k in payload} == payload and decoded["_oficial"]
    assert decode_qr_url("https://example.com/?p=abc") is None
    # a well-formed invoice QR on a non-government domain is kept, and marked as such
    fake = decode_qr_url(encode_qr_url(payload).replace("www.afip.gob.ar", "afip.gob.ar.example.com"))
    assert fake is not None and not fake["_oficial"]


def test_amount_formats():
    assert parse_amount("1.234.567,89") == 1234567.89
    assert parse_amount("1234,5") == 12345.0  # one decimal digit is not a decimal part
    assert parse_amount("1234.56") == 1234.56
    assert parse_amount("1,234.56") == 1234.56
    assert parse_amount("1.002.052.62") == 1002052.62  # OCR read the comma as a dot
    assert parse_amount("12.345") == 12345.0


def test_extract_and_validate_ocr_like_text():
    lines = [
        "Comercial Garin S.A.   FACTURAA",
        "Cod.01",
        "N°0003-00093282",
        "Fecha:21-07-2025",
        "Cliente:Agro Belgrano S.A.",
        "CUIT:33-07806671-2   Direccion:Tigre",
        "Subtotal   1.000,00",
        "IVA21%   210,00",
        "TOTAL   S1.210,00",
        "CAE:73180243325365 Vto.CAE:31-07-2025",
    ]
    inv = extract(lines)
    assert (inv.letra, inv.tipo, inv.punto_venta, inv.numero) == ("A", "Factura", 3, 93282)
    assert inv.fecha_emision == "2025-07-21" and inv.cae_vencimiento == "2025-07-31"
    assert inv.emisor_cuit is None and inv.receptor_cuit == "33078066712"
    assert (inv.neto_gravado, inv.iva_21, inv.total) == (1000.0, 210.0, 1210.0)
    issues = check(inv, has_qr=False)
    assert "falta:emisor_cuit" in issues and "sin_qr" in issues and "total_no_cierra" not in issues


def test_llm_fallback_is_revalidated(tmp_path):
    """A model reading that does not close the arithmetic must stay in review."""
    import cv2
    import numpy as np

    from facturas.pipeline import process
    from facturas.schema import Invoice

    img = np.full((400, 300, 3), 255, np.uint8)
    path = tmp_path / "blank.png"
    cv2.imwrite(str(path), img)

    class FakeLLM:
        def __init__(self, inv):
            self.inv = inv

        def extract(self, image):
            return self.inv.model_copy()

    good = Invoice(tipo="Factura", letra="A", punto_venta=1, numero=7, fecha_emision="2026-01-10",
                   emisor_cuit="30500010912", receptor_cuit="20123456786", neto_gravado=100.0,
                   iva_21=21.0, total=121.0, cae="70417054367476", cae_vencimiento="2026-01-20")
    r = process(path, llm=FakeLLM(good))
    assert r.motivos == ["sin_qr"] and r.fuentes["total"] == "llm"

    bad = good.model_copy(update={"total": 125.0})
    r = process(path, llm=FakeLLM(bad))
    assert "total_no_cierra" in r.motivos


def test_ocr_dollar_read_as_s_and_label_without_value():
    from facturas.rules import labels_without_value

    lines = ["Factura A", "Codigo: 001", "ImporteNeto(S)   2.005.500,00", "IVA(S)   541.485.00", "Total(S)   2.546.985,00"]
    inv = extract(lines)
    assert (inv.neto_gravado, inv.iva_total, inv.total) == (2005500.0, 541485.0, 2546985.0)
    lines[2] = "ImporteNeto(S)   2.0O5.5OO,OO"  # amount unreadable
    inv = extract(lines)
    assert inv.neto_gravado is None and labels_without_value(lines, inv) == ["neto_gravado"]


def test_edit_distance():
    assert edit_distance("30815908305", "30815908305") == 0
    assert edit_distance("30815908305", "30816908305") == 1


def test_clean_name_restores_lost_spaces():
    from facturas.rules import clean_name

    assert clean_name("ServiciosEjemplo S.R.L.") == "Servicios Ejemplo S.R.L."
    assert clean_name("Distribuidora Rio LujanS.A.s.") == "Distribuidora Rio Lujan S.A.S."
    assert clean_name("DISTRIBUIDORA DEL PLATA S.A.") == "DISTRIBUIDORA DEL PLATA S.A."
