"""Turn a PDF or image into (page image, text lines, QR payloads)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import zxingcpp

from .afip import decode_qr_url

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class Token:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    image: np.ndarray  # BGR
    lines: list[str]
    text_source: str  # "pdf" or "ocr"
    qr: dict | None
    # Geometry, used to draw where each field was read. rows[i] holds the tokens of lines[i] in the
    # coordinates of the image the text was read from; to_image maps those coordinates to `image`.
    rows: list[list[Token]] = field(default_factory=list)
    to_image: np.ndarray | None = None
    flattened: np.ndarray | None = None  # the straightened page the OCR read (photos and scans)
    page_found: bool = False  # a sheet of paper was found and flattened (typical of phone photos)


def group_rows(tokens: list[Token]) -> list[list[Token]]:
    """Group tokens into text rows by vertical overlap, each row sorted left to right."""
    tokens = sorted(tokens, key=lambda t: (t.y0 + t.y1) / 2)
    rows: list[list[Token]] = []
    for t in tokens:
        cy, h = (t.y0 + t.y1) / 2, max(t.y1 - t.y0, 1)
        if rows:
            last = rows[-1]
            ly = sum((r.y0 + r.y1) / 2 for r in last) / len(last)
            lh = sum(r.y1 - r.y0 for r in last) / len(last)
            if abs(cy - ly) < 0.5 * min(h, lh) + 1:
                last.append(t)
                continue
        rows.append([t])
    for row in rows:
        row.sort(key=lambda t: t.x0)
    return rows


def row_text(row: list[Token], gap_factor: float = 1.6) -> tuple[str, list[tuple[int, int]]]:
    """Join a row into one line (large horizontal gaps become '   ') and the span of each token in it."""
    avg_char = sum((t.x1 - t.x0) / max(len(t.text), 1) for t in row) / len(row)
    parts, spans, pos = [], [], 0
    for k, cur in enumerate(row):
        if k:
            gap = cur.x0 - row[k - 1].x1
            sep = "   " if gap > gap_factor * avg_char * 1.5 else " "
            parts.append(sep)
            pos += len(sep)
        parts.append(cur.text)
        spans.append((pos, pos + len(cur.text)))
        pos += len(cur.text)
    joined = "".join(parts)
    lead = len(joined) - len(joined.lstrip())
    return joined.strip(), [(a - lead, b - lead) for a, b in spans]


def group_lines(tokens: list[Token], gap_factor: float = 1.6) -> list[str]:
    """Group tokens into text lines by vertical overlap; large horizontal gaps become '   '."""
    return [row_text(row, gap_factor)[0] for row in group_rows(tokens)]


def _scale(f: float) -> np.ndarray:
    return np.diag([f, f, 1.0])


def map_points(m: np.ndarray, pts) -> list[list[float]]:
    """Apply a 3x3 homography to a list of (x, y) points."""
    arr = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(arr, m).reshape(-1, 2).round(1).tolist()


def read_qr(image: np.ndarray) -> dict | None:
    """Try several preprocessings; return the first AFIP payload found.

    The payload gets a "_box" key: the QR corners in `image` coordinates.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    sharpen = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])

    def candidates():  # cheapest first; photos usually need the last ones. (image, map back to `image`)
        eye = np.eye(3)
        yield gray, eye
        yield cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], eye
        if max(gray.shape) < 2500:
            yield cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC), _scale(0.5)
            flat, m = _rectify(gray)
            yield (cv2.filter2D(cv2.resize(flat, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC), -1, sharpen),
                   np.linalg.inv(m) @ _scale(1 / 3))

    for img, back in candidates():
        for res in zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode, try_rotate=True):
            payload = decode_qr_url(res.text)
            if payload:
                p = res.position
                corners = [(c.x, c.y) for c in (p.top_left, p.top_right, p.bottom_right, p.bottom_left)]
                payload["_box"] = map_points(back, corners)
                return payload
    return None


_local = threading.local()
_PDFIUM = threading.Lock()  # pdfium is not thread-safe


def _ocr_engine():
    """One OCR engine per thread, so several documents can be read at once."""
    engine = getattr(_local, "ocr", None)
    if engine is None:
        from rapidocr_onnxruntime import RapidOCR

        engine = _local.ocr = RapidOCR()
    return engine


def ocr_tokens(image: np.ndarray) -> list[Token]:
    result, _ = _ocr_engine()(image)
    tokens = []
    for box, text, score in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        tokens.append(Token(text, min(xs), min(ys), max(xs), max(ys)))
    return tokens


def _order_corners(pts: np.ndarray) -> np.ndarray:
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.float32([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]])


def rectify_page(image: np.ndarray) -> np.ndarray:
    """Find the sheet of paper in a photo and warp it to a flat rectangle."""
    return _rectify(image)[0]


def _rectify(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """rectify_page, plus the 3x3 matrix from `image` to the flattened page (identity if no page)."""
    eye = np.eye(3)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small_scale = 800 / max(gray.shape)
    small = cv2.resize(gray, None, fx=small_scale, fy=small_scale)
    small = cv2.GaussianBlur(small, (5, 5), 0)
    _, mask = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image, eye
    page = max(contours, key=cv2.contourArea)
    area_ratio = cv2.contourArea(page) / (small.shape[0] * small.shape[1])
    if not 0.3 < area_ratio < 0.97:
        return image, eye  # no visible page border (scans, screenshots)
    approx = cv2.approxPolyDP(page, 0.02 * cv2.arcLength(page, True), True)
    if len(approx) != 4:
        return image, eye
    src = _order_corners(approx.reshape(4, 2).astype(np.float32) / small_scale)
    width = int(max(np.linalg.norm(src[0] - src[1]), np.linalg.norm(src[3] - src[2])))
    height = int(max(np.linalg.norm(src[0] - src[3]), np.linalg.norm(src[1] - src[2])))
    dst = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, m, (width, height)), m


def deskew(image: np.ndarray) -> np.ndarray:
    """Correct small rotations (scanner feed) using the dominant angle of text lines."""
    return _deskew(image)[0]


def _deskew(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    eye = np.eye(3)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    # merge characters into horizontal blobs, then read each blob's angle
    blobs = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1)))
    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for c in contours:
        (_, _), (w, h), a = cv2.minAreaRect(c)
        if w < h:
            w, h, a = h, w, a - 90
        if w > 80 and h < 40:
            angles.append(a)
    if len(angles) < 5:
        return image, eye
    angle = float(np.median(angles))
    if abs(angle) < 0.15 or abs(angle) > 8:
        return image, eye
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(image, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return out, np.vstack([m, [0, 0, 1]])


def prepare_for_ocr(image: np.ndarray) -> np.ndarray:
    return _prepare(image)[0]


def _prepare(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """prepare_for_ocr, plus the matrix from the input to the prepared image and whether a page was found."""
    flat, m_page = _rectify(image)
    image, m_skew = _deskew(flat)
    m = m_skew @ m_page
    h, w = image.shape[:2]
    if max(h, w) > 2600:  # 300 dpi scans: OCR is as good at ~200 dpi and twice as fast
        s = 2400 / max(h, w)
        image = cv2.resize(image, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        m = _scale(s) @ m
        h, w = image.shape[:2]
    if max(h, w) < 1500:  # OCR on ~1300 px photos misses small print
        image = cv2.resize(image, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
        m = _scale(1.6) @ m
    return image, m, not np.allclose(m_page, np.eye(3))


def _ocr_page(image: np.ndarray, qr: dict | None) -> Page:
    prepared, m, page_found = _prepare(image)
    rows = group_rows(ocr_tokens(prepared))
    return Page(image, [row_text(r)[0] for r in rows], "ocr", qr, rows=rows,
                to_image=np.linalg.inv(m), flattened=prepared, page_found=page_found)


def load(path: Path, force_ocr: bool = False) -> Page:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        with _PDFIUM:
            pdf = pdfium.PdfDocument(str(path))
            image = cv2.cvtColor(np.array(pdf[0].render(scale=200 / 72).to_pil().convert("RGB")), cv2.COLOR_RGB2BGR)
            pdf.close()
        tokens: list[Token] = []
        if not force_ocr:
            with pdfplumber.open(str(path)) as doc:
                words = doc.pages[0].extract_words(keep_blank_chars=False, use_text_flow=False)
                tokens = [Token(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]) for w in words]
        if len(tokens) > 20:
            rows = group_rows(tokens)
            return Page(image, [row_text(r)[0] for r in rows], "pdf", read_qr(image), rows=rows,
                        to_image=_scale(200 / 72))
        # scanned PDF without a text layer
        return _ocr_page(image, read_qr(image))

    if path.suffix.lower() not in IMAGE_EXT:
        raise ValueError(f"Formato no soportado: {path.suffix}")
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"No se pudo abrir {path}")
    return _ocr_page(image, read_qr(image))
