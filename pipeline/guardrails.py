"""Guardrails via Azure AI Content Safety — the native platform primitive, since the
self-hosted OSS models (GLM-OCR, NuExtract) don't get Foundry's built-in content
filter the way a Foundry-catalog serverless deployment would. Wrap every OCR output
before it reaches the KIE step, and every KIE output before it's persisted/returned.
"""
from azure.ai.contentsafety import ContentSafetyClient
from azure.ai.contentsafety.models import AnalyzeTextOptions
from azure.identity import DefaultAzureCredential
from dataclasses import dataclass

from . import config

# Reject if any category's severity (0-6 scale) is at or above this.
_BLOCK_SEVERITY = 4

_client = None


def _get_client() -> ContentSafetyClient:
    # Managed identity (Cognitive Services User role, granted in main.bicep) —
    # same "no keys to leak or rotate" approach as storage/Service Bus access.
    global _client
    if _client is None:
        _client = ContentSafetyClient(config.CONTENT_SAFETY_ENDPOINT, DefaultAzureCredential())
    return _client


@dataclass
class GuardrailResult:
    allowed: bool
    max_severity: int
    flagged_categories: list[str]


def check_text(text: str) -> GuardrailResult:
    if not text.strip():
        return GuardrailResult(allowed=True, max_severity=0, flagged_categories=[])

    # Content Safety caps request size; extracted documents can run long, so only
    # the leading slice is screened — enough to catch injected/adversarial content
    # without shipping the whole document through the API on every call.
    result = _get_client().analyze_text(AnalyzeTextOptions(text=text[:10000]))

    flagged = [c.category for c in result.categories_analysis if c.severity >= _BLOCK_SEVERITY]
    max_severity = max((c.severity for c in result.categories_analysis), default=0)
    return GuardrailResult(allowed=not flagged, max_severity=max_severity, flagged_categories=flagged)
