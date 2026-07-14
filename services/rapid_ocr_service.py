"""Layer 3 OCR: RapidOCR at its higher-accuracy MEDIUM model size, used when Paddle's
own signals (table_count, avg_confidence, region_count — see run_paddle_ocr's tool
description) indicate a layout too complex to trust Paddle's default models on.

Deterministic detection+recognition, not a vision LLM — every span gets a real
bounding box, same normalized [0,1] top-left-origin convention as
pipeline/text_layer_check.py and paddle_service.py, so the agent never has to read
pixels or guess coordinates for any entity.
"""
import base64

import numpy as np
import pypdfium2 as pdfium
from fastapi import FastAPI
from pydantic import BaseModel
from rapidocr import ModelType, RapidOCR

app = FastAPI()
engine = RapidOCR(params={"Det.model_type": ModelType.MEDIUM, "Rec.model_type": ModelType.MEDIUM})


class OcrRequest(BaseModel):
    pdf_base64: str


class OcrSpan(BaseModel):
    page: int
    text: str
    confidence: float
    bbox: list  # [x0, y0, x1, y1], normalized to [0, 1], top-left origin


class OcrResponse(BaseModel):
    avg_confidence: float
    spans: list[OcrSpan]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ocr", response_model=OcrResponse)
def ocr(req: OcrRequest):
    pdf_bytes = base64.b64decode(req.pdf_base64)
    doc = pdfium.PdfDocument(pdf_bytes)

    spans, confidences = [], []
    for page_index, page in enumerate(doc):
        bitmap = page.render(scale=200 / 72)
        pil_img = bitmap.to_pil().convert("RGB")
        img = np.asarray(pil_img)
        img_width, img_height = pil_img.size
        bitmap.close()
        page.close()

        result = engine(img)
        if result.boxes is None:
            continue
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            xs, ys = box[:, 0], box[:, 1]
            confidences.append(score)
            spans.append(
                OcrSpan(
                    page=page_index,
                    text=text,
                    confidence=float(score),
                    bbox=[
                        float(xs.min()) / img_width,
                        float(ys.min()) / img_height,
                        float(xs.max()) / img_width,
                        float(ys.max()) / img_height,
                    ],
                )
            )

    doc.close()
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResponse(avg_confidence=avg_confidence, spans=spans)
