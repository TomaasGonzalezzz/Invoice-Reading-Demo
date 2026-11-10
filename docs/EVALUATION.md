# How the extractor was evaluated

Two test sets, measured with the same rules and no Claude fallback. All numbers come from the JSON files in [`results/`](../results/).

## Metrics

| Metric | Definition |
| --- | --- |
| Fields correct | Share of (document, field) pairs where the output equals the label. Money: ±0.01, and empty = 0. Names: compared without accents, case, spaces or punctuation. |
| Every field correct | Share of documents with no wrong field. |
| Approved automatically | Share of regular documents (no deliberate errors) that passed every check. |
| Approved with a wrong field | Documents that passed every check but have at least one wrong field. These would enter the books unseen, so this is the number to keep at zero. |
| Traps caught | Documents with deliberate errors that were sent to review. |

## 1. Own test set

- **50 fictitious invoices** with fixed seed 7: letters A (50 %), B (30 %), C (20 %), 10 % credit notes, 1 to 5 items, 21 % and 10.5 % VAT, IIBB perceptions on 30 % of A invoices. Fictitious issuers and customers with valid CUIT check digits.
- **3 layouts**: one modeled after the government's free invoicing tool, one after commercial invoicing software, one older style with dotted abbreviations and dot decimals.
- **3 captures of each invoice**: digital PDF; scan (150 dpi, grayscale, rotation up to ±2°, Gaussian noise, JPEG quality 70); phone photo (150 dpi, page on a colored background, perspective up to 5 %, lighting gradient, blur, 1600 px long side, JPEG quality 75).
- **5 traps**, spread across layouts: 2 totals that don't add up, 1 printed CUIT with a wrong check digit, 2 QR codes whose total disagrees with the printed one. 2 invoices have no QR.

| | PDF | Scan | Photo |
| --- | ---: | ---: | ---: |
| Fields correct | 100 % | 99.8 % | 99.9 % |
| Every field correct | 100 % | 95.6 % | 97.8 % |
| Regular invoices approved automatically | 95.6 % | 91.1 % | 93.3 % |
| Approved with a wrong field | 0 | 0 | 0 |
| Traps caught | 5 of 5 | 5 of 5 | 5 of 5 |
| QR read (48 invoices have one) | 48 of 48 | 48 of 48 | 48 of 48 |
| Seconds per document (laptop CPU, 2 in parallel) | 0.24 | 13.9 | 12.22 |

Fields below 100 %:

- PDF: none
- scan: `otros_tributos` 95.6 %
- photo: `otros_tributos` 97.8 %

Review reasons:

- `total_no_cierra`: 9
- `sin_qr`: 6
- `qr_no_coincide:total`: 6
- `qr_no_coincide:emisor_cuit (posible error de lectura)`: 2
- `qr_no_coincide:emisor_cuit`: 1
- `cuit_emisor_invalido`: 1

## 2. Independent benchmark

**Data.** [mauroibz/facturas_argentinas_2](https://huggingface.co/datasets/mauroibz/facturas_argentinas_2) on Hugging Face: 200 labeled invoices (A, B and C; ARS, USD and EUR) in 5 layouts (`baseline`, `cards`, `columns`, `columns_notitles`, `sidebar`) and 3 typefaces, rendered as 2481×3508 JPG images with a working QR. It was made by a third party with its own generator, so it tests whether the rules generalize beyond the layouts they were written for. The dataset has no license file, so it is not redistributed here.

**Protocol.** A fixed split (hash of the document id): 55 documents for tuning and 145 for testing. Rules were only adjusted while looking at the tuning set. Five test documents had been opened before the split was made; they were moved to the tuning set. The test set was run as images and also as simulated phone photos, using the same degradation as the own test set.

**Two runs, reported honestly.** The image condition was run blind (99.7 % of fields, 96.6 % of invoices fully correct). The first blind run as photos got 91.6 % of fields and 29 % of invoices: OCR read `($)` as `(S)`, so net and VAT were missed, and a missing optional amount did not stop approval. After fixing the pattern and adding the `etiqueta_sin_valor` check (a printed label with an unreadable amount goes to review), both conditions were run again. **The tables below come from that second run.** For photos they are no longer a blind test, because the bug was found by looking at test results. First runs: `results/externo_test_*_primera_corrida.json`.

**Labels compared**: letter, point of sale, number, date, issuer and customer CUIT (a 7–8 digit customer document is a DNI, so the expected CUIT is empty), issuer and customer names, currency, net, total VAT, other taxes, total. The labels have no CAE.

| | Image | Photo |
| --- | ---: | ---: |
| Fields correct | 99.7 % | 99.3 % |
| Every field correct | 96.6 % | 91 % |
| QR read | 100 % | 100 % |
| Flagged for review | 100 % | 100 % |
| Seconds per document (2 in parallel) | 16.66 | 15.38 |

Every field correct, by layout:

| Layout | Image | Photo |
| --- | :---: | :---: |
| `baseline` | 29/31 | 28/31 |
| `cards` | 30/31 | 29/31 |
| `columns` | 23/24 | 22/24 |
| `columns_notitles` | 23/23 | 20/23 |
| `sidebar` | 35/36 | 33/36 |

Fields below 100 %: image: `punto_venta` 99.3 %, `emisor_razon_social` 99.3 %, `receptor_razon_social` 98.6 %, `total` 99.3 %; photo: `emisor_cuit` 99.3 %, `emisor_razon_social` 98.6 %, `receptor_cuit` 99.3 %, `receptor_razon_social` 95.2 %, `neto_gravado` 97.9 %.

**Why every document was flagged.** The dataset's invoices print *"Comprobante No Autorizado"*, their QR points to a non-government domain, many of their CUITs have invalid check digits and their Factura B lack the *IVA contenido* line. Those are properties of how the dataset was generated, and flagging them is the correct behavior. To estimate what would happen with genuine invoices, the evaluation also reports the result ignoring only those dataset-specific reasons (`si_fueran_genuinas`): 98.6 % (image) and 97.9 % (photo) would have been approved, with 3 and 10 approved documents carrying a wrong field. In images, all 3 are misread names. In photos, 9 are misread names and 1 is a net amount left empty because OCR mangled its label too. No approved document had a wrong amount, date, CUIT or invoice number: those are cross-checked against the QR and the arithmetic.

**Known next improvement.** Most name errors come from OCR reading accented letters as digits (`Restauraci6n`, `C6rdoba`, `Disenio`). A post-correction for digits inside words would fix most of them. It was found on the tuning set and not applied, so these results stay comparable.

Wrong fields on the test set:

- image · 01554-00000186 (columns): `emisor_razon_social` read `MueblesyDisenioPalermo S.R.L.`, label `Muebles y Diseño Palermo S.R.L.`
- image · 04225-00000133 (cards): `receptor_razon_social` read `StudioVerdeDisenio`, label `Studio Verde Diseño`
- image · 07178-00000145 (sidebar): `total` read `89.0`, label `9.68`
- image · 07278-00000091 (baseline): `receptor_razon_social` read `TallerEIHerrero`, label `Taller El Herrero`
- image · 09999-00000010 (baseline): `punto_venta` read `66660`, label `9999`
- photo · 00609-00000111 (columns_notitles): `receptor_razon_social` read `Lucas Femandez`, label `Lucas Fernández`
- photo · 00984-00000317 (cards): `receptor_razon_social` read `Cafe del Rfo`, label `Café del Río`
- photo · 02300-00000230 (cards): `receptor_razon_social` read `Panaderfa del Pueblo`, label `Panadería del Pueblo`
- photo · 02443-00000225 (columns_notitles): `emisor_cuit` read `30313589648`, label `30387286248`; `receptor_cuit` read `30387286248`, label `30313589648`
- photo · 03152-00000300 (sidebar): `receptor_razon_social` read `Lavadero Ecolbgico`, label `Lavadero Ecológico`
- photo · 03363-00000008 (columns): `neto_gravado` read `None`, label `1203300`
- photo · 05708-00000328 (sidebar): `emisor_razon_social` read `VideoProducciones C6rdoba`, label `VideoProducciones Córdoba`
- photo · 06127-00000193 (baseline): `receptor_razon_social` read `Clinica Odontol6gica Rivera`, label `Clínica Odontológica Rivera`
- photo · 06542-00000140 (sidebar): `emisor_razon_social` read `ServiclosAudiovisualesStudioOne`, label `Servicios Audiovisuales StudioOne`
- photo · 07636-00000325 (columns_notitles): `neto_gravado` read `None`, label `107361100`
- photo · 08192-00000078 (baseline): `neto_gravado` read `None`, label `577584`
- photo · 08391-00000263 (columns): `receptor_razon_social` read `None`, label `Ingeniería Solar Patagonia`
- photo · 09148-00000195 (baseline): `receptor_razon_social` read `Repuestos La Estaci6n`, label `Repuestos La Estación`

## 3. Demo batch (`demo/lote/`)

The batch used in the demo app, the video and the README figures: 20 fictitious purchase invoices addressed to one fictitious company, as 8 digital PDFs, 3 scans and 9 phone photos (sheet on a desk, rotated 2.5–7°, tilted, uneven light and a soft shadow, 2000 px). Five carry a problem on purpose: a total that is $ 100 off the sum, a printed CUIT with a wrong check digit, a QR that registers a different amount than the printed one, a photo blurred by motion in a dim room, and the same invoice twice (the e-mailed PDF and a photo of it).

| | Result |
| --- | --- |
| Regular invoices approved automatically | 15 of 15, every field equal to the ground truth |
| Problems sent to review, each with the right reason | 5 of 5 |

This batch was built to show the system, not to measure it, and it was adjusted after looking at the results:

- In the first version, one scan read "Percepción IIBB" as "Percepcion lIIBB" and lost that amount; the arithmetic check then sent a correct invoice to review (no wrong data was approved). The rules were not changed, so the numbers above stay comparable; that invoice was generated again without a perception. A more tolerant label pattern is a known next improvement.
- Supplier names were changed to names without accents, because OCR sometimes drops them ("Ferreteria") and the QR can't confirm a name. The same limit shows in the independent benchmark above.
