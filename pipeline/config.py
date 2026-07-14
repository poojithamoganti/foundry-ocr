"""All tunables in one place, sourced from env vars set by the container app bicep
(see infra/bicep/main.bicep `env` blocks). No config framework — os.environ is enough."""
import os

SERVICEBUS_NAMESPACE = os.environ.get("SERVICEBUS_NAMESPACE", "")
STORAGE_ACCOUNT = os.environ.get("STORAGE_ACCOUNT", "")

PADDLE_OCR_URL = os.environ.get("PADDLE_OCR_URL", "http://paddle-ocr")
RAPID_OCR_URL = os.environ.get("RAPID_OCR_URL", "http://rapid-ocr")

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

# Layer 1 (embedded text) quality thresholds — below either, the agent's
# check_embedded_text_layer tool reports bad quality so it escalates to Paddle OCR.
MIN_CHARS_PER_PAGE = 20
MIN_PRINTABLE_RATIO = 0.9

# Layer 2 -> layer 3 escalation thresholds, surfaced to the agent in the
# run_paddle_ocr tool description (pipeline/agent_tools.py) so it knows when to
# call run_rapid_ocr instead of trusting Paddle's spans.
PADDLE_MIN_CONFIDENCE = 0.80
PADDLE_MAX_REGIONS_BEFORE_ESCALATE = 15  # dense/multi-column pages
