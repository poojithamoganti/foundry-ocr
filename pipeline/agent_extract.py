"""Runs a document through the Foundry agent (gpt-5-mini + function tools) for OCR
routing and entity extraction in one agentic loop, instead of a hardcoded Python
cascade: the agent calls check_embedded_text_layer, escalates to run_paddle_ocr if
needed, and escalates further to run_rapid_ocr for tables/forms/complex layouts — see
pipeline/agent_tools.py for the tool implementations and agent/setup_agent.py for how
the agent itself is registered. All three tools are deterministic OCR (no vision
step), so every span the agent cites resolves to a real bounding box.
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


def _call(openai_client, input_, conversation_id: str):
    return openai_client.responses.create(
        input=input_,
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": config.AGENT_NAME, "type": "agent_reference"}},
    )


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def _resolve_entities(blob_name: str, raw_entities: dict) -> dict:
    """Turn the agent's {field: {"value": ..., "source_span_ids": [...]}} answer into
    {field: {"value": ..., "bounding_boxes": [{"page", "bbox"}, ...]}} by looking up
    each cited span id's real box. Tolerates the agent returning a bare value instead
    of the value/source_span_ids shape (bounding_boxes just comes back empty)."""
    resolved = {}
    for field, item in raw_entities.items():
        if isinstance(item, dict) and "value" in item:
            value, span_ids = item["value"], item.get("source_span_ids", [])
        else:
            value, span_ids = item, []
        resolved[field] = {
            "value": value,
            "bounding_boxes": agent_tools.resolve_span_ids(blob_name, span_ids),
        }
    return resolved


@dataclass
class ExtractionResult:
    entities: dict
    rounds: int
    guardrail: guardrails.GuardrailResult


def extract(
    blob_name: str, pdf_bytes: bytes, schema: dict, max_rounds: int = 6, force_ocr: bool = False
) -> ExtractionResult:
    agent_tools.register_document(blob_name, pdf_bytes)
    try:
        with telemetry.tracer.start_as_current_span("agent_extract.extract") as span:
            openai_client = _get_project().get_openai_client()
            conversation = openai_client.conversations.create()

            skip_layer1 = (
                " Ignore the embedded text layer entirely for this document — do not call "
                "check_embedded_text_layer, start directly with run_paddle_ocr."
                if force_ocr
                else ""
            )
            user_text = (
                f"Extract these fields from document '{blob_name}': {json.dumps(schema)}. Use "
                "your tools to get clean text first." + skip_layer1 + " For each field, respond with "
                '{"value": ..., "source_span_ids": [...]} citing the span id(s) you took the '
                "value from, as a single JSON object keyed by field name — ONLY that JSON "
                "object, no other text, once you have enough information."
            )
            response = _call(openai_client, user_text, conversation.id)

            rounds = 0
            for rounds in range(1, max_rounds + 1):
                function_calls = [item for item in response.output if item.type == "function_call"]
                if not function_calls:
                    break

                next_input = [
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(agent_tools.TOOL_IMPLEMENTATIONS[call.name](**json.loads(call.arguments or "{}"))),
                    }
                    for call in function_calls
                ]
                response = _call(openai_client, next_input, conversation.id)

            span.set_attribute("kie.rounds", rounds)
            raw_entities = _parse_json(response.output_text) or {}
            entities = _resolve_entities(blob_name, raw_entities)
            guardrail = guardrails.check_text(json.dumps(entities))
            return ExtractionResult(entities=entities, rounds=rounds, guardrail=guardrail)
    finally:
        agent_tools.unregister_document(blob_name)
