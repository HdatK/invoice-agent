"""Turn an uploaded file into model-ready images, an optional PDF text layer and quality notes."""
from __future__ import annotations
from dataclasses import dataclass, field
import io
import cv2
import numpy as np
from PIL import Image, ImageOps
from ..core.config import settings

MAX_SIDE = 2200


class UnreadableFile(Exception):
    pass


@dataclass
class Doc:
    processed: list[bytes] = field(default_factory=list)   # enhanced + deskewed JPEGs (variant 1)
    raw: list[bytes] = field(default_factory=list)         # lightly resized JPEGs (variant 2: independent view)
    text: str | None = None                                  # PDF text layer, if any
    notes: list[str] = field(default_factory=list)
    pages: int = 1


def _jpeg(bgr: np.ndarray, q: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes()


def _resize(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    s = MAX_SIDE / max(h, w)
    return cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC) if s != 1 else bgr


def estimate_skew(gray: np.ndarray) -> float:
    """Skew angle in degrees (positive = text rotated counter-clockwise), 0 when unsure."""
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(bw, 1, np.pi / 720, threshold=120, minLineLength=small.shape[1] // 4, maxLineGap=12)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = np.degrees(np.arctan2(y1 - y2, x2 - x1))
        if abs(a) <= 20:
            angles.append(a)
    return float(np.median(angles)) if len(angles) >= 3 else 0.0


def enhance(bgr: np.ndarray, notes: list[str]) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.Laplacian(cv2.resize(gray, (1200, int(1200 * gray.shape[0] / gray.shape[1]))), cv2.CV_64F).var()
    if blur < 60:
        notes.append("blurry")
    if gray.mean() < 90:
        notes.append("low_light")
    ang = estimate_skew(gray)
    if 0.7 <= abs(ang) <= 20:
        h, w = bgr.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), -ang, 1.0)
        bgr = cv2.warpAffine(bgr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        notes.append(f"deskewed:{ang:.1f}")
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    if "blurry" in notes:
        out = cv2.addWeighted(out, 1.6, cv2.GaussianBlur(out, (0, 0), 3), -0.6, 0)
    return out


def _from_bytes(data: bytes) -> np.ndarray:
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:
        raise UnreadableFile("تصویر قابل خواندن نیست") from e
    if max(im.size) > 12000 or min(im.size) < 200:
        raise UnreadableFile("ابعاد تصویر مجاز نیست")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def load_document(path: str, mime: str) -> Doc:
    doc = Doc()
    data = open(path, "rb").read()
    pages: list[np.ndarray] = []
    if mime == "application/pdf":
        import pymupdf
        try:
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            raise UnreadableFile("فایل PDF قابل باز شدن نیست") from e
        if pdf.needs_pass:
            raise UnreadableFile("PDF رمزدار است")
        doc.pages = len(pdf)
        if doc.pages > settings.max_pdf_pages:
            doc.notes.append(f"only_first_{settings.max_pdf_pages}_pages")
        txt = []
        for pg in list(pdf)[: settings.max_pdf_pages]:
            txt.append(pg.get_text("text"))
            pix = pg.get_pixmap(dpi=170)
            arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
            pages.append(cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR))
        t = "\n".join(txt).strip()
        doc.text = t if len(t) >= 40 else None
        if doc.text is None:
            doc.notes.append("scanned_pdf")
    else:
        pages.append(_from_bytes(data))
    for bgr in pages:
        bgr = _resize(bgr)
        doc.raw.append(_jpeg(bgr, 82))
        doc.processed.append(_jpeg(enhance(bgr, doc.notes)))
    return doc
