"""3-layer OCR, exposed as Foundry agent function tools (see agent/setup_agent.py for
the registered schemas and pipeline/agent_extract.py for the call loop). All three
layers are deterministic OCR — no vision LLM step — so every span always has a real
bounding box.

Bounding boxes: tool outputs give the model text tagged with short span ids
("t0", "p3", "r1", ...) instead of raw bbox numbers — LLMs are unreliable at
emitting precise coordinates, but good at copying an id. The real bbox for each id
is kept server-side in _SPAN_REGISTRY; agent_extract.py resolves the ids the model
cites in its final answer back to real boxes.
"""
import base64

import httpx

from . import config
from .text_layer_check import check as _check_text_layer

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


def run_rapid_ocr(blob_name: str) -> dict:
    body = _call_ocr_service(config.RAPID_OCR_URL, blob_name)
    return {
        "avg_confidence": body["avg_confidence"],
        "spans": _tag_spans(blob_name, "r", body["spans"]),
    }


TOOL_IMPLEMENTATIONS = {
    "check_embedded_text_layer": check_embedded_text_layer,
    "run_paddle_ocr": run_paddle_ocr,
    "run_rapid_ocr": run_rapid_ocr,
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
            f"and table_count — treat table_count > 0, avg_confidence below {config.PADDLE_MIN_CONFIDENCE}, "
            f"or region_count above {config.PADDLE_MAX_REGIONS_BEFORE_ESCALATE} as signals the layout "
            "is too complex for Paddle's spans, and call run_rapid_ocr instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {"blob_name": {"type": "string"}},
            "required": ["blob_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_rapid_ocr",
        "description": (
            "Run RapidOCR (higher-accuracy model set) on a scanned/image PDF whose "
            "layout was too complex for PaddleOCR — tables, forms, dense multi-column "
            "pages. Returns id-tagged text spans plus avg_confidence. Use this instead "
            "of trusting a low-signal PaddleOCR result for that document."
        ),
        "parameters": {
            "type": "object",
            "properties": {"blob_name": {"type": "string"}},
            "required": ["blob_name"],
            "additionalProperties": False,
        },
    },
]
