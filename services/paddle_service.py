"""Layer 2 OCR: PaddleOCR's PP-Structure engine.

Returns recognized text as per-line spans with normalized bounding boxes (fractional
[0,1], top-left origin — same convention as pipeline/text_layer_check.py's layer-1
spans) for non-table regions, plus cell-level structure (row_index, column_index,
bbox, text per cell) for table regions, so pipeline/agent_tools.py's
run_paddle_ocr can reconstruct markdown tables the same way it does for Document
Intelligence Layout output — see that file for how both engines' output is unified
before reaching the model.
"""
import base64
from html.parser import HTMLParser

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


class OcrCell(BaseModel):
    row_index: int
    column_index: int
    text: str
    bbox: list | None  # normalized to [0, 1], same convention as OcrSpan


class OcrTable(BaseModel):
    page: int
    bbox: list | None
    row_count: int
    column_count: int
    cells: list[OcrCell]


class OcrResponse(BaseModel):
    avg_confidence: float
    region_count: int
    table_count: int
    spans: list[OcrSpan]
    tables: list[OcrTable]


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


class _TableGridParser(HTMLParser):
    """Parses PP-Structure's res["html"] table markup into a (row_index,
    column_index) grid, honoring rowspan/colspan. Cells are recorded in document
    order (td_index) so they can be zipped against res["cell_bbox"], which
    PP-Structure emits in the same left-to-right, top-to-bottom order as the html.

    ponytail: this order-matching between html <td> sequence and cell_bbox is a
    documented convention, not something enforced by a schema — if bboxes come
    back misaligned with cell text, verify against the pinned paddleocr version.
    """

    def __init__(self):
        super().__init__()
        self.cells = []  # [{"row_index", "column_index", "text"}], td_index == list index
        self._row = -1
        self._col = 0
        self._occupied = {}  # (row, col) -> True, reserved by an earlier rowspan
        self._in_cell = False
        self._cell_text = []
        self._pending_span = (1, 1)
        self.row_count = 0
        self.column_count = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in ("td", "th"):
            while self._occupied.get((self._row, self._col)):
                self._col += 1
            self._in_cell = True
            self._cell_text = []
            colspan = int(attrs.get("colspan", 1))
            rowspan = int(attrs.get("rowspan", 1))
            self._pending_span = (rowspan, colspan)

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            rowspan, colspan = self._pending_span
            self.cells.append({
                "row_index": self._row,
                "column_index": self._col,
                "text": "".join(self._cell_text).strip(),
            })
            for r in range(self._row, self._row + rowspan):
                for c in range(self._col, self._col + colspan):
                    self._occupied[(r, c)] = True
            self.row_count = max(self.row_count, self._row + rowspan)
            self.column_count = max(self.column_count, self._col + colspan)
            self._col += colspan
            self._in_cell = False


def _extract_table(region: dict, img_width: int, img_height: int, page_index: int) -> OcrTable | None:
    res = region.get("res") or {}
    html = res.get("html")
    if not html:
        return None  # region flagged as table but structure recognition produced nothing usable

    parser = _TableGridParser()
    parser.feed(html)

    cell_bboxes = res.get("cell_bbox") or []
    cells = []
    for i, cell in enumerate(parser.cells):
        bbox = None
        if i < len(cell_bboxes):
            polygon = cell_bboxes[i]
            xs = polygon[0::2] if len(polygon) > 4 else [polygon[0], polygon[2]]
            ys = polygon[1::2] if len(polygon) > 4 else [polygon[1], polygon[3]]
            bbox = [min(xs) / img_width, min(ys) / img_height, max(xs) / img_width, max(ys) / img_height]
        cells.append(OcrCell(row_index=cell["row_index"], column_index=cell["column_index"], text=cell["text"], bbox=bbox))

    return OcrTable(
        page=page_index,
        bbox=_extract_bbox(region, img_width, img_height),
        row_count=parser.row_count,
        column_count=parser.column_count,
        cells=cells,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ocr", response_model=OcrResponse)
def ocr(req: OcrRequest):
    pdf_bytes = base64.b64decode(req.pdf_base64)
    doc = pdfium.PdfDocument(pdf_bytes)

    spans, tables, confidences, region_count, table_count = [], [], [], 0, 0
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
                table = _extract_table(region, img_width, img_height, page_index)
                if table is not None:
                    tables.append(table)
                continue  # table content goes into `tables`, not `spans`

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
        tables=tables,
    )