# foundry_ocr

Smart entity extraction: 3-layer OCR + a Foundry agent (gpt-5-mini) that drives the
OCR tools and does the extraction, deployed on Azure Container Apps + Microsoft
Foundry, East US 2 only. Standalone project — no dependency on the sibling
`foundry_agent` repo.

## Architecture

```
 PDF --> samples/ --> orchestrator downloads it, starts a Foundry agent conversation
                                        │
                        ┌───────────────┴───────────────┐
                        │   Foundry agent (gpt-5-mini)   │
                        │   agent/setup_agent.py           │
                        └───────────────┬───────────────┘
                                        │ tool calls (pipeline/agent_tools.py)
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
   check_embedded_text_layer   run_paddle_ocr (ACA, CPU)   run_rapid_ocr (ACA, CPU)
   (layer 1, in-process,        (layer 2 — table_count,     (layer 3 — RapidOCR at
    pypdfium2)                   avg_confidence, region      its higher-accuracy
                                  signals tell the agent      MEDIUM model size, for
                                  whether to escalate)        tables/forms/complex
                                                               layouts)
              └─────────────────────────┬─────────────────────────┘
                                        ▼
                     agent responds with ONLY a JSON object
                     matching the requested extraction schema
                                        ▼
                      Content Safety guardrail check on the
                              extracted entities
                                        ▼
                                results/*.json
```

The agent — not hardcoded Python if/else — decides whether embedded text is good
enough, whether Paddle's output needs escalating, and does the final extraction. The
routing *thresholds* still live in one place (`pipeline/config.py`) and are fed into
the `run_paddle_ocr` tool's description so the agent applies them consistently.

All three OCR tools are deterministic detection+recognition — no vision LLM step —
so gpt-5-mini never reads pixels; it only ever reasons over already-OCR'd, id-tagged
text spans. RapidOCR isn't a document-understanding model the way GLM-OCR is (it's
architecturally in the same tier as PaddleOCR — classic detection+recognition, much
of it derived from the same PP-OCR model family), so on genuinely pathological
layouts (handwriting, unusual structure) it won't match a real document-VLM's
semantic understanding. What it reliably gives instead: every extracted entity gets
a real bounding box, with no vision-reasoning gap.

### Bounding boxes

`check_embedded_text_layer`, `run_paddle_ocr`, and `run_rapid_ocr` don't hand the
model raw text — they tag every text span with a short id (`t0`, `p3`, `r1`, ...) and
keep the real bbox server-side (`pipeline/agent_tools.py::_SPAN_REGISTRY`). The model
is bad at emitting precise coordinates but fine at copying an id, so its final answer
cites `source_span_ids` per field instead of numbers; `agent_extract.py::_resolve_
entities` looks those ids up and the result JSON ends up as:

```json
{
  "entities": {
    "invoice_number": {
      "value": "INV-2024-001",
      "bounding_boxes": [{"page": 0, "bbox": [0.12, 0.08, 0.29, 0.09]}]
    }
  }
}
```

`bbox` is `[x0, y0, x1, y1]`, normalized to `[0, 1]`, top-left origin — divide by 1 to
get fractional page position, or multiply by the rendered page's pixel size to overlay
on an image. Because every layer is deterministic OCR now, `bounding_boxes` is
populated for every field the model successfully grounds in a span — there's no more
"read off an image, no box" gap.

Orchestration runs on Service Bus (`ingest_queue` -> `dead_letter_queue` on any
guardrail block or error), consumed by the `orchestrator` container app.

## Why everything routes through Foundry

- **Guardrails** — Azure AI Content Safety (`pipeline/guardrails.py`) checks the
  agent's final extraction before it's persisted, on top of gpt-5-mini's own
  built-in content filtering as a Foundry-hosted deployment.
- **Audit logs** — every storage/Service Bus operation and every agent call is
  traced (`pipeline/telemetry.py`, diagnostic settings in `storage.bicep` /
  `servicebus.bicep`) into the App Insights instance linked via
  `APPLICATIONINSIGHTS_CONNECTION_STRING`; agent tool invocations are also traced
  natively in the Foundry portal.
- **AI policy** — Azure Policy locks every resource in the RG to East US 2
  (`main.bicep`); RBAC (`Foundry User`, `Cognitive Services User`, etc.) controls who
  and what can call the project.
- **Retraining** — `ml/trigger_finetune.py` submits an Azure OpenAI fine-tuning job
  against the Foundry project on human-corrected extractions; the job and resulting
  model show up in the project's Fine-tuning tab.
- **No self-hosted GPU** — Azure Container Apps serverless GPU isn't available in
  East US 2 (checked against the current supported-regions list). gpt-5-mini only
  does reasoning (never vision/OCR), and RapidOCR/PaddleOCR are both CPU-only, so
  nothing in this system needs GPU — `paddle-ocr`, `rapid-ocr`, and `orchestrator`
  all run as plain CPU container apps.

## Deploy

```powershell
az deployment sub create --location eastus2 --template-file infra/bicep/main.bicep

# Build and push the 3 images (paddle-ocr, rapid-ocr, orchestrator)
./scripts/build_and_push.ps1 -AcrName <acrLoginServer output, without .azurecr.io>

# Re-run the deployment so the container apps pick up the now-existing images
az deployment sub create --location eastus2 --template-file infra/bicep/main.bicep

# Register the agent definition (run once, and again whenever agent_tools.py's
# TOOL_SCHEMAS or agent/setup_agent.py's INSTRUCTIONS change)
$env:FOUNDRY_PROJECT_ENDPOINT = "<foundryProjectEndpoint output>"
pip install -r requirements.txt
python agent/setup_agent.py
```

The deployment's `frontendUrl` output is the orchestrator's public FQDN — open it in
a browser for the upload UI (see "Use" below).

## Use

**Interactive** — browse to `<frontendUrl output>` (the orchestrator's public FQDN):
upload a PDF, optionally toggle "Force OCR" to ignore the embedded text layer even if
it's good (useful for comparing layers or testing scanned-doc handling on a
digitally-authored PDF), and see extracted entities with bounding boxes overlaid on
the rendered page. This calls `POST /api/extract` synchronously — no blob storage or
Service Bus involved, results aren't persisted anywhere.

⚠️ No auth in front of this endpoint yet — it's reachable by anyone with the URL. Fine
for a trusted demo; add Container Apps built-in auth (Easy Auth) before sharing it
more broadly, since it accepts arbitrary file uploads and calls billed Foundry/OCR
resources on every request.

**Batch** — for production ingestion via the queue (persists to `results/*.json`):

```powershell
python scripts/submit_document.py path/to/document.pdf
# result lands at results/document.pdf.json once the orchestrator processes it
```

## Retrain on corrections

Drop corrected extractions into the `corrections` blob container (one JSON file per
document: `{"text", "schema", "output"}`), sync them locally, then:

```powershell
az storage blob download-batch -d corrections/ -s corrections --account-name <storageAccountName>
python ml/prepare_training_data.py corrections/ training_data.jsonl
python ml/trigger_finetune.py training_data.jsonl
```

## Tests

```powershell
python tests/test_text_layer_check.py
```

## What's deliberately not here

- No AKS, no self-hosted GPU containers, no vision LLM in the OCR path — see "No
  self-hosted GPU" above.
- No structured-output JSON schema enforcement on the agent's final response — it's
  instructed to respond with only JSON and the response is parsed leniently
  (`pipeline/agent_extract.py::_parse_json`). Add Responses API structured outputs if
  malformed JSON turns out to be a real problem in practice.
- The KIE extraction schema is hardcoded (`DEFAULT_SCHEMA` in
  `pipeline/orchestrator.py`) — add a `doc_type` field on the ingest message and a
  schema lookup once more than one document type is in play.
- `services/rapid_ocr_service.py`'s polygon-to-bbox math assumes `result.boxes` is a
  Nx4x2 array (per the current `rapidocr` package docs) — verify against the pinned
  version if bboxes come back wrong.
