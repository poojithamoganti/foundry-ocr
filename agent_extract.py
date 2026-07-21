"""Runs a document through OCR preprocessing (deterministic, plain Python — engine
picked by the caller via ocr_engine, not decided by the model) followed by exactly
one Foundry model call for entity extraction. See pipeline/agent_tools.py for the
preprocessing implementations and table reconstruction for both engines.
"""
import json
from dataclasses import dataclass

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from . import agent_tools, config, guardrails, telemetry

_project = None


def _get_project() -> AIProjectClient:
    global _project
    if _project is None:
        _project = AIProjectClient(endpoint=config.FOUNDRY_PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    return _project


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def _compile_schema(schema: dict) -> dict:
    properties = {
        field: {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string"},
                    "source_span_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["value", "source_span_ids"],
            },
        }
        for field in schema
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(schema),
    }


def _resolve_entities(blob_name: str, raw_entities: dict) -> dict:
    resolved = {}
    for field, occurrences in raw_entities.items():
        if not isinstance(occurrences, list):
            occurrences = [occurrences]
        resolved[field] = []
        for item in occurrences:
            if isinstance(item, dict) and "value" in item:
                value, span_ids = item["value"], item.get("source_span_ids", [])
            else:
                value, span_ids = item, []
            resolved[field].append({
                "value": value,
                "bounding_boxes": agent_tools.resolve_span_ids(blob_name, span_ids),
            })
    return resolved


@dataclass
class ExtractionResult:
    entities: dict
    ocr_source: str
    document_text: str
    guardrail: guardrails.GuardrailResult


def extract(
    blob_name: str, pdf_bytes: bytes, schema: dict, force_ocr: bool = False, ocr_engine: str = "document_intelligence_layout",
) -> ExtractionResult:
    if ocr_engine not in agent_tools.OCR_ENGINES:
        raise ValueError(f"Unknown ocr_engine '{ocr_engine}', must be one of {list(agent_tools.OCR_ENGINES)}")

    agent_tools.register_document(blob_name, pdf_bytes)
    try:
        with telemetry.tracer.start_as_current_span("agent_extract.extract") as span:
            document_text = None
            ocr_source = "embedded_text_layer"
            if not force_ocr:
                layer = agent_tools.check_embedded_text_layer(blob_name)
                if layer["is_good_quality"]:
                    document_text = layer["text"]

            if document_text is None:
                ocr_result = agent_tools.OCR_ENGINES[ocr_engine](blob_name)
                document_text = ocr_result["text"]
                ocr_source = ocr_engine

            span.set_attribute("kie.ocr_source", ocr_source)

            openai_client = _get_project().get_openai_client()
            conversation = openai_client.conversations.create()
            user_text = (
                f"Extract these fields from the document below: {json.dumps(list(schema))}. "
                "Each line/cell is tagged with a span id in brackets, e.g. [s7]. The same "
                "field can legitimately appear more than once — report every occurrence you "
                "find. For each occurrence, cite the span id(s) it was read from in "
                "source_span_ids. If a field never appears, return an empty array for it.\n\n"
                + document_text
            )
            response = openai_client.responses.create(
                input=user_text,
                conversation=conversation.id,
                extra_body={"agent_reference": {"name": config.AGENT_NAME, "type": "agent_reference"}},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "extraction",
                        "strict": True,
                        "schema": _compile_schema(schema),
                    }
                },
            )

            raw_entities = _parse_json(response.output_text) or {}
            entities = _resolve_entities(blob_name, raw_entities)
            guardrail = guardrails.check_text(json.dumps(entities))
            return ExtractionResult(entities=entities, ocr_source=ocr_source, document_text=document_text, guardrail=guardrail)
    finally:
        agent_tools.unregister_document(blob_name)