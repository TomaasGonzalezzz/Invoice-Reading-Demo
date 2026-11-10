"""Optional fallback: ask Claude to read the page image when the rules leave the document for review.

Needs an Anthropic API key (ANTHROPIC_API_KEY) or an `ant auth login` profile.
The QR and the validator still run on the model's answer, so a wrong reading
is caught the same way as an OCR error.
"""

from __future__ import annotations

import base64
import os

import cv2
import numpy as np

from .schema import Invoice

DEFAULT_MODEL = os.environ.get("FACTURAS_MODEL", "claude-opus-5-5")
# server-side fallback on refusals is available for these models (not for Haiku)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}

SYSTEM = """You read Argentine invoices (facturas, notas de crédito y débito) and copy the printed values.
Rules:
- Copy what is printed. Never compute, guess or correct a value. If a field is not printed or not legible, return null.
- CUIT and CAE: digits only, no dashes or spaces.
- Dates as YYYY-MM-DD. Argentine dates are printed day first.
- Amounts as plain numbers: "1.234,56" is 1234.56.
- neto_gravado, iva_21 and iva_10_5 only exist when IVA is itemized (letters A and M).
- iva_contenido is the "IVA contenido" line printed on Factura B (Ley 27.743).
- emisor is the business that issued the document; receptor is the customer.
- receptor_cuit is null for "Consumidor Final"."""


def _image_block(image: np.ndarray) -> dict:
    h, w = image.shape[:2]
    scale = min(1.0, 1568 / max(h, w))
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("No se pudo codificar la imagen")
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(buf).decode()}}


class LLMExtractor:
    def __init__(self, model: str = DEFAULT_MODEL):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0

    def extract(self, image: np.ndarray) -> Invoice | None:
        kwargs = {}
        if self.model in FALLBACK_MODELS:
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM,
                output_config={"effort": "medium"},
                messages=[{"role": "user", "content": [
                    _image_block(image),
                    {"type": "text", "text": "Extract the invoice fields."},
                ]}],
                output_format=Invoice,
                **kwargs,
            )
        except self.anthropic.RateLimitError:
            raise
        except self.anthropic.APIStatusError as e:
            print(f"  [llm] error {e.status_code}: {e.message}")
            return None
        except self.anthropic.APIConnectionError as e:
            print(f"  [llm] sin conexión: {e}")
            return None
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            return None
        return response.parsed_output
