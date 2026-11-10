"""Output schema: one Invoice per document, plus the review result."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MONEY_FIELDS = ("neto_gravado", "iva_21", "iva_10_5", "iva_total", "otros_tributos", "iva_contenido", "total")


class Invoice(BaseModel):
    """Fields printed on an Argentine invoice. Amounts in the invoice currency, dates as YYYY-MM-DD."""

    tipo: Literal["Factura", "Nota de Crédito", "Nota de Débito"] | None = None
    letra: Literal["A", "B", "C", "M"] | None = None
    punto_venta: int | None = None
    numero: int | None = None
    fecha_emision: str | None = Field(None, description="YYYY-MM-DD")
    emisor_cuit: str | None = Field(None, description="11 digits, no dashes")
    emisor_razon_social: str | None = None
    receptor_cuit: str | None = Field(None, description="11 digits, no dashes; null for consumidor final")
    receptor_razon_social: str | None = None
    moneda: Literal["PES", "DOL", "EUR"] | None = None
    neto_gravado: float | None = Field(None, description="Net amount, when printed (always on Factura A/M)")
    iva_21: float | None = None
    iva_10_5: float | None = None
    iva_total: float | None = Field(None, description="All IVA itemized on the invoice (sum of every rate)")
    otros_tributos: float | None = Field(None, description="Percepciones and other taxes")
    iva_contenido: float | None = Field(None, description="Factura B: IVA included in the total (Ley 27.743)")
    total: float | None = None
    cae: str | None = Field(None, description="14 digits")
    cae_vencimiento: str | None = Field(None, description="YYYY-MM-DD")


class Result(BaseModel):
    archivo: str
    estado: Literal["ok", "revisar"]
    motivos: list[str] = []
    factura: Invoice
    fuentes: dict[str, str] = {}  # field -> "qr" | "texto" | "ocr" | "llm"
    qr_leido: bool = False
    qr_total: float | None = None  # the amount ARCA registered, as read from the QR
    qr_cuit: str | None = None  # the issuer CUIT ARCA registered, as read from the QR
    texto_origen: Literal["pdf", "ocr"] = "pdf"
    segundos: float = 0.0
