"""Layer 2 OCR: PaddleOCR's PP-Structure engine.

Returns recognized text as per-line spans with normalized bounding boxes (fractional
[0,1], top-left origin — same convention as pipeline/text_layer_check.py's layer-1
spans) plus layout metadata (table regions, region count) so the Foundry agent
(pipeline/agent_extract.py, via the run_paddle_ocr tool in pipeline/agent_tools.py)
can decide whether to escalate to layer 3 (RapidOCR, via run_rapid_ocr) for complex
layouts, instead of hand-rolling that decision in Python.
"""
import base64

import numpy as np
import pypdfium2 as pdfium
from fastapi import FastAPI
from paddleocr import PPStructure
from pydantic import BaseModel

app = FastAPI()
engine = PPStructure(table=True, ocr=True, show_log=False)


class OcrRequest(BaseModel):
    pdf_base64: str


class OcrSpan(BaseModel):
    page: int
    text: str
    confidence: float
    bbox: list | None  # [x0, y0, x1, y1], normalized to [0, 1], top-left origin


class OcrResponse(BaseModel):
    avg_confidence: float
    region_count: int
    table_count: int
    spans: list[OcrSpan]


def _extract_bbox(line: dict, img_width: int, img_height: int) -> list | None:
    # ponytail: PaddleOCR/PP-Structure's per-line polygon key has drifted across
    # releases (`text_region` classically, `bbox`/`points` on some versions) —
    # verify against the pinned paddleocr version if bboxes come back null.
    polygon = line.get("text_region") or line.get("bbox") or line.get("points")
    if not polygon:
        return None
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return [min(xs) / img_width, min(ys) / img_height, max(xs) / img_width, max(ys) / img_height]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ocr", response_model=OcrResponse)
def ocr(req: OcrRequest):
    pdf_bytes = base64.b64decode(req.pdf_base64)
    doc = pdfium.PdfDocument(pdf_bytes)

    spans, confidences, region_count, table_count = [], [], 0, 0
    for page_index, page in enumerate(doc):
        bitmap = page.render(scale=200 / 72)
        pil_img = bitmap.to_pil().convert("RGB")
        img = np.asarray(pil_img)[:, :, ::-1]  # RGB -> BGR for cv2/PaddleOCR
        img_width, img_height = pil_img.size
        bitmap.close()
        page.close()

        regions = engine(img)
        region_count += len(regions)
        for region in regions:
            if region["type"] == "table":
                table_count += 1
            for line in region.get("res", []) or []:
                if isinstance(line, dict) and "text" in line:
                    confidence = line.get("confidence", 0.0)
                    confidences.append(confidence)
                    spans.append(
                        OcrSpan(
                            page=page_index,
                            text=line["text"],
                            confidence=confidence,
                            bbox=_extract_bbox(line, img_width, img_height),
                        )
                    )

    doc.close()
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResponse(
        avg_confidence=avg_confidence,
        region_count=region_count,
        table_count=table_count,
        spans=spans,
    )
