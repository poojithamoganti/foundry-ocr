"""Embedded-text-layer check plus two mutually exclusive OCR engines, exposed as
Foundry agent function tools (see agent/setup_agent.py for the registered schemas and
pipeline/agent_extract.py for the call loop and ocr_engine selection). All are
deterministic OCR/extraction — no vision LLM step — so every span always has a real
bounding box.

Bounding boxes: tool outputs give the model text tagged with short span ids
("t0", "p3", "d1", ...) instead of raw bbox numbers — LLMs are unreliable at
emitting precise coordinates, but good at copying an id. The real bbox for each id
is kept server-side in _SPAN_REGISTRY; agent_extract.py resolves the ids the model
cites in its final answer back to real boxes.
"""
import base64

import httpx
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import DefaultAzureCredential

from . import config
from .text_layer_check import check as _check_text_layer

_di_client = None


def _get_di_client() -> DocumentIntelligenceClient:
    global _di_client
    if _di_client is None:
        _di_client = DocumentIntelligenceClient(endpoint=config.DOCUMENT_INTELLIGENCE_ENDPOINT, credential=DefaultAzureCredential())
    return _di_client

# Populated per-document by agent_extract.py before starting a conversation, keyed by
# blob_name — function tools only receive whatever arguments the model supplies (it
# never sees raw PDF bytes), so this is the least-code way to hand tools the bytes.
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


def _tag_spans(blob_name: str, prefix: str, spans: list) -> list:
    """Register each span's real bbox and return the id+text pairs the model sees."""
    registry = _SPAN_REGISTRY.setdefault(blob_name, {})
    tagged = []
    for i, span in enumerate(spans):
        span_id = f"{prefix}{i}"
        page = span.page if hasattr(span, "page") else span["page"]
        bbox = span.bbox if hasattr(span, "bbox") else span["bbox"]
        text = span.text if hasattr(span, "text") else span["text"]
        registry[span_id] = {"page": page, "bbox": bbox}
        tagged.append({"id": span_id, "text": text})
    return tagged


def _call_ocr_service(url: str, blob_name: str) -> dict:
    pdf_bytes = _DOCUMENT_CACHE[blob_name]
    resp = httpx.post(f"{url}/ocr", json={"pdf_base64": base64.b64encode(pdf_bytes).decode()}, timeout=120)
    resp.raise_for_status()
    return resp.json()


def check_embedded_text_layer(blob_name: str) -> dict:
    result = _check_text_layer(_DOCUMENT_CACHE[blob_name])
    return {
        "is_good_quality": result.is_good_quality,
        "char_count": result.char_count,
        "spans": _tag_spans(blob_name, "t", result.spans),
    }


def run_paddle_ocr(blob_name: str) -> dict:
    body = _call_ocr_service(config.PADDLE_OCR_URL, blob_name)
    return {
        "avg_confidence": body["avg_confidence"],
        "region_count": body["region_count"],
        "table_count": body["table_count"],
        "spans": _tag_spans(blob_name, "p", body["spans"]),
    }


def run_document_intelligence_layout(blob_name: str) -> dict:
    """Azure AI Document Intelligence's prebuilt-layout model — a fully-managed
    service call (no self-hosted container, unlike Paddle), used as an alternative
    OCR engine the caller can pick instead of PaddleOCR (see agent_extract.py's
    ocr_engine param — the two are mutually exclusive per request, not escalation
    tiers)."""
    poller = _get_di_client().begin_analyze_document(
        "prebuilt-layout", body=_DOCUMENT_CACHE[blob_name], content_type="application/pdf"
    )
    result = poller.result()
    spans = []
    for page in result.pages:
        for line in page.lines:
            # polygon: flat [x0, y0, x1, y1, ...] in the same physical unit as
            # page.width/height (inches or pixels) — divide through to normalize to
            # [0, 1], same convention as the Paddle/embedded-text-layer spans.
            # ponytail: verify against the pinned azure-ai-documentintelligence
            # version if bboxes come back wrong — see services/rapid_ocr_service.py
            # for the equivalent note on that service's polygon assumption.
            xs, ys = line.polygon[0::2], line.polygon[1::2]
            spans.append({
                "page": page.page_number - 1,
                "bbox": [min(xs) / page.width, min(ys) / page.height, max(xs) / page.width, max(ys) / page.height],
                "text": line.content,
            })
    return {"spans": _tag_spans(blob_name, "d", spans)}


TOOL_IMPLEMENTATIONS = {
    "check_embedded_text_layer": check_embedded_text_layer,
    "run_paddle_ocr": run_paddle_ocr,
    "run_document_intelligence_layout": run_document_intelligence_layout,
}

TOOL_SCHEMAS = [
    {
        "name": "check_embedded_text_layer",
        "description": (
            "Check the PDF's own embedded text layer (from a digitally-authored PDF). "
            "Returns id-tagged text spans and whether they're good enough quality to "
            "use directly, skipping OCR entirely. Always try this first."
        ),
        "parameters": {
            "type": "object",
            "properties": {"blob_name": {"type": "string"}},
            "required": ["blob_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_paddle_ocr",
        "description": (
            "Run PaddleOCR on a scanned/image PDF whose embedded text layer was bad or "
            "missing. Returns id-tagged text spans plus avg_confidence, region_count, "
            "and table_count. Only call this if the user's message says to use "
            "PaddleOCR as the OCR engine for this document — otherwise use "
            "run_document_intelligence_layout instead. Never call both."
        ),
        "parameters": {
            "type": "object",
            "properties": {"blob_name": {"type": "string"}},
            "required": ["blob_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_document_intelligence_layout",
        "description": (
            "Run Azure AI Document Intelligence's prebuilt-layout model on a "
            "scanned/image PDF. Returns id-tagged text spans. Only call this if the "
            "user's message says to use Document Intelligence as the OCR engine for "
            "this document — otherwise use run_paddle_ocr instead. Never call both."
        ),
        "parameters": {
            "type": "object",
            "properties": {"blob_name": {"type": "string"}},
            "required": ["blob_name"],
            "additionalProperties": False,
        },
    },
]
