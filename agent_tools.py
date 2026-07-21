"""Embedded-text-layer check and two selectable OCR engines (PaddleOCR, Document
Intelligence Layout), called directly and deterministically by agent_extract.py based
on the caller's ocr_engine choice — no longer exposed as Foundry agent function tools,
since OCR routing/selection is a Python decision, not something the model decides
mid-conversation. Kept selectable (not an escalation cascade) specifically so both
engines' table reconstruction can be compared side-by-side on the frontend.

Bounding boxes: preprocessing tags text with short span ids ("s0", "s1", ...) instead
of raw bbox numbers — LLMs are unreliable at emitting precise coordinates, but good at
copying an id. The real bbox for each id is kept server-side in _SPAN_REGISTRY;
agent_extract.py resolves the ids the model cites in its final answer back to real
boxes.
"""
import base64

import httpx
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import DefaultAzureCredential

from . import config
from .text_layer_check import check as _check_text_layer

_di_client = None

# Paragraph roles that carry no extractable field value — dropped before the text
# ever reaches the model, pure token savings.
_SKIP_ROLES = {"pageHeader", "pageFooter", "pageNumber"}


def _get_di_client() -> DocumentIntelligenceClient:
    global _di_client
    if _di_client is None:
        _di_client = DocumentIntelligenceClient(
            endpoint=config.DOCUMENT_INTELLIGENCE_ENDPOINT, credential=DefaultAzureCredential()
        )
    return _di_client


# Populated per-document by agent_extract.py before starting a conversation.
_DOCUMENT_CACHE: dict[str, bytes] = {}

# blob_name -> {span_id: {"page": int, "bbox": list}}
_SPAN_REGISTRY: dict[str, dict[str, dict]] = {}


def register_document(blob_name: str, pdf_bytes: bytes) -> None:
    _DOCUMENT_CACHE[blob_name] = pdf_bytes
    _SPAN_REGISTRY[blob_name] = {}


def unregister_document(blob_name: str) -> None:
    _DOCUMENT_CACHE.pop(blob_name, None)
    _SPAN_REGISTRY.pop(blob_name, None)


def resolve_span_ids(blob_name: str, span_ids: list) -> list:
    registry = _SPAN_REGISTRY.get(blob_name, {})
    return [registry[sid] for sid in span_ids if sid in registry]


def _register_span(blob_name: str, page: int, bbox: list) -> str:
    registry = _SPAN_REGISTRY.setdefault(blob_name, {})
    span_id = f"s{len(registry)}"
    registry[span_id] = {"page": page, "bbox": bbox}
    return span_id


def _polygon_bbox(polygon: list, width: float, height: float) -> list:
    # polygon: flat [x0, y0, x1, y1, ...] in the same physical unit as
    # page.width/height (inches or pixels) — normalize to [0, 1].
    # ponytail: verify against the pinned azure-ai-documentintelligence version if
    # bboxes come back wrong.
    xs, ys = polygon[0::2], polygon[1::2]
    return [min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height]


def _render_markdown_table(grid: list) -> str:
    if not grid:
        return ""
    md_rows = ["| " + " | ".join(row) + " |" for row in grid]
    md_rows.insert(1, "|" + "|".join(["---"] * len(grid[0])) + "|")
    return "\n".join(md_rows)


def check_embedded_text_layer(blob_name: str) -> dict:
    """Deterministic first step. Returns whether the PDF's own embedded text layer
    is good enough to use directly, plus already span-tagged text if so."""
    result = _check_text_layer(_DOCUMENT_CACHE[blob_name])
    lines = []
    for s in result.spans:
        span_id = _register_span(blob_name, s.page, s.bbox)
        lines.append(f"[{span_id}] {s.text}")
    return {
        "is_good_quality": result.is_good_quality,
        "char_count": result.char_count,
        "text": "\n".join(lines),
    }


def run_document_intelligence_layout(blob_name: str) -> dict:
    """Reconstructs result.tables[] as markdown (row/column structure preserved,
    every cell span-tagged) and merges them with non-table paragraphs in original
    reading order, skipping header/footer/page-number boilerplate."""
    poller = _get_di_client().begin_analyze_document(
        "prebuilt-layout", body=_DOCUMENT_CACHE[blob_name], content_type="application/pdf"
    )
    result = poller.result()

    table_span_ranges = [
        (span.offset, span.offset + span.length)
        for table in (result.tables or [])
        for cell in table.cells
        for span in cell.spans
    ]

    def _in_table(paragraph) -> bool:
        return any(
            start < t_end and (start + length) > t_start
            for span in paragraph.spans
            for start, length in [(span.offset, span.length)]
            for t_start, t_end in table_span_ranges
        )

    blocks = []  # (sort_key, text)

    for paragraph in result.paragraphs or []:
        if paragraph.role in _SKIP_ROLES or not paragraph.bounding_regions:
            continue
        if _in_table(paragraph):
            continue
        region = paragraph.bounding_regions[0]
        page = result.pages[region.page_number - 1]
        bbox = _polygon_bbox(region.polygon, page.width, page.height)
        span_id = _register_span(blob_name, region.page_number - 1, bbox)
        blocks.append(((region.page_number, bbox[1]), f"[{span_id}] {paragraph.content}"))

    for table in result.tables or []:
        if not table.bounding_regions:
            continue
        region = table.bounding_regions[0]
        page = result.pages[region.page_number - 1]
        sort_key = (region.page_number, _polygon_bbox(region.polygon, page.width, page.height)[1])

        grid = [["" for _ in range(table.column_count)] for _ in range(table.row_count)]
        for cell in table.cells:
            cell_region = cell.bounding_regions[0] if cell.bounding_regions else region
            cell_page = result.pages[cell_region.page_number - 1]
            bbox = _polygon_bbox(cell_region.polygon, cell_page.width, cell_page.height)
            span_id = _register_span(blob_name, cell_region.page_number - 1, bbox)
            grid[cell.row_index][cell.column_index] = f"[{span_id}] {cell.content}"

        blocks.append((sort_key, _render_markdown_table(grid)))

    blocks.sort(key=lambda b: b[0])
    return {"text": "\n\n".join(text for _, text in blocks), "engine": "document_intelligence_layout"}


def run_paddle_ocr(blob_name: str) -> dict:
    """Mirrors run_document_intelligence_layout's reconstruction so both engines'
    output is directly comparable on the frontend. Table reconstruction requires the
    paddle-ocr service to return cell-level structure (rows/cells with row_index,
    column_index, bbox, text) — same shape DI gives us via PP-StructureV3. If the
    service response doesn't include a "tables" key yet, falls back to flat
    span-tagged text with table_count still reported so the gap is visible rather
    than silently producing an unstructured comparison."""
    pdf_bytes = _DOCUMENT_CACHE[blob_name]
    resp = httpx.post(
        f"{config.PADDLE_OCR_URL}/ocr", json={"pdf_base64": base64.b64encode(pdf_bytes).decode()}, timeout=120
    )
    resp.raise_for_status()
    body = resp.json()

    table_cell_ids = set()
    blocks = []  # (sort_key, text)

    for table in body.get("tables", []):
        page = table["page"]
        grid = [["" for _ in range(table["column_count"])] for _ in range(table["row_count"])]
        for cell in table["cells"]:
            span_id = _register_span(blob_name, page, cell["bbox"])
            grid[cell["row_index"]][cell["column_index"]] = f"[{span_id}] {cell['text']}"
            table_cell_ids.add(id(cell))
        blocks.append(((page, table["bbox"][1]), _render_markdown_table(grid)))

    for span in body["spans"]:
        if span.get("in_table"):  # only present if the service marks table membership
            continue
        span_id = _register_span(blob_name, span["page"], span["bbox"])
        blocks.append(((span["page"], span["bbox"][1]), f"[{span_id}] {span['text']}"))

    blocks.sort(key=lambda b: b[0])
    return {
        "text": "\n\n".join(text for _, text in blocks),
        "engine": "paddle_ocr",
        "avg_confidence": body.get("avg_confidence"),
        "table_count": body.get("table_count"),
        "tables_structured": bool(body.get("tables")),  # False = service hasn't been extended yet
    }


OCR_ENGINES = {
    "paddle_ocr": run_paddle_ocr,
    "document_intelligence_layout": run_document_intelligence_layout,
}