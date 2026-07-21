"""All tunables in one place, sourced from env vars set by the container app bicep
(see infra/bicep/main.bicep `env` blocks). No config framework — os.environ is enough."""
import os

SERVICEBUS_NAMESPACE = os.environ.get("SERVICEBUS_NAMESPACE", "")
STORAGE_ACCOUNT = os.environ.get("STORAGE_ACCOUNT", "")

PADDLE_OCR_URL = os.environ.get("PADDLE_OCR_URL", "http://paddle-ocr")
DOCUMENT_INTELLIGENCE_ENDPOINT = os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT", "")

CONTENT_SAFETY_ENDPOINT = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")

FOUNDRY_PROJECT_ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
AGENT_MODEL_DEPLOYMENT = os.environ.get("AGENT_MODEL_DEPLOYMENT", "gpt-5-mini")
AGENT_NAME = os.environ.get("AGENT_NAME", "doc-extraction-agent")

APPLICATIONINSIGHTS_CONNECTION_STRING = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

QUEUE_INGEST = "ingest_queue"
QUEUE_DEAD_LETTER = "dead_letter_queue"

CONTAINER_SAMPLES = "samples"
CONTAINER_RESULTS = "results"
CONTAINER_CORRECTIONS = "corrections"
CONTAINER_SCHEMAS = "schemas"

# Layer 1 (embedded text) quality thresholds — below either, the agent's
# check_embedded_text_layer tool reports bad quality so it falls through to
# whichever OCR engine the request specified (see agent_extract.py's ocr_engine param).
MIN_CHARS_PER_PAGE = 20
MIN_PRINTABLE_RATIO = 0.9
