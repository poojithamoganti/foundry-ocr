"""Ties the pipeline together two ways:

1. Batch: pulls a document off ingest_queue (background thread), runs it through the
   Foundry agent, writes the result to blob storage, dead-letters anything a
   guardrail blocks.
2. Interactive: POST /api/extract lets the frontend/ (single static page) upload a
   PDF and get entities + bounding boxes + page preview images back synchronously,
   for the "upload and see it work" demo flow — bypasses blob storage/Service Bus
   entirely since there's nothing to decouple for a one-shot request.

Both paths call the same pipeline/agent_extract.py. See infra/bicep/containerapp.bicep
for why this app's ingress is external (it now serves a browser-facing UI) while
paddle-ocr/rapid-ocr stay internal-only.
"""
import base64
import io
import json
import threading
import uuid
from pathlib import Path

import pypdfium2 as pdfium
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.staticfiles import StaticFiles

from . import agent_extract, config, telemetry

app = FastAPI()
_credential = DefaultAzureCredential()

# Placeholder schema for the POC; real deployments would look this up per document
# type (e.g. from a `doc_type` field on the ingest message) rather than hardcode one.
DEFAULT_SCHEMA = {
    "document_type": "string",
    "date": "string",
    "parties": "string",
    "amounts": "string",
    "key_entities": "string",
}


@app.get("/health")
def health():
    return {"status": "ok"}


def _render_preview_pages(pdf_bytes: bytes, dpi: int = 150) -> list[dict]:
    """Page images for the frontend to overlay bounding boxes on — rendered at the
    same top-left-origin convention as the normalized bboxes in agent_extract results,
    so `bbox[i] * image_pixel_size` lines up directly, no coordinate conversion needed."""
    doc = pdfium.PdfDocument(pdf_bytes)
    pages = []
    for page_index, page in enumerate(doc):
        bitmap = page.render(scale=dpi / 72)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="PNG")
        pages.append({"page": page_index, "image_base64": base64.b64encode(buf.getvalue()).decode()})
        bitmap.close()
        page.close()
    doc.close()
    return pages


@app.post("/api/extract")
def api_extract(file: UploadFile = File(...), force_ocr: bool = Form(False)):
    pdf_bytes = file.file.read()
    request_id = f"upload-{uuid.uuid4().hex[:8]}"

    with telemetry.tracer.start_as_current_span("orchestrator.api_extract") as span:
        span.set_attribute("document.blob_name", request_id)
        extraction = agent_extract.extract(request_id, pdf_bytes, DEFAULT_SCHEMA, force_ocr=force_ocr)
        return {
            "entities": extraction.entities,
            "agent_rounds": extraction.rounds,
            "guardrail_allowed": extraction.guardrail.allowed,
            "pages": _render_preview_pages(pdf_bytes),
        }


def _blob_client() -> BlobServiceClient:
    return BlobServiceClient(
        f"https://{config.STORAGE_ACCOUNT}.blob.core.windows.net", credential=_credential
    )


def _process_message(body: bytes, sb_client: ServiceBusClient, blob_client: BlobServiceClient) -> None:
    payload = json.loads(body)
    blob_name = payload["blob_name"]

    with telemetry.tracer.start_as_current_span("orchestrator.process_document") as span:
        span.set_attribute("document.blob_name", blob_name)

        pdf_bytes = (
            blob_client.get_container_client(config.CONTAINER_SAMPLES)
            .download_blob(blob_name)
            .readall()
        )

        extraction = agent_extract.extract(blob_name, pdf_bytes, DEFAULT_SCHEMA)
        if not extraction.guardrail.allowed:
            _dead_letter(sb_client, blob_name, "kie_guardrail_blocked", extraction.guardrail.flagged_categories)
            return

        result = {
            "blob_name": blob_name,
            "agent_rounds": extraction.rounds,
            "entities": extraction.entities,
        }
        blob_client.get_container_client(config.CONTAINER_RESULTS).upload_blob(
            f"{blob_name}.json", json.dumps(result), overwrite=True
        )
        span.set_attribute("document.status", "completed")


def _dead_letter(sb_client: ServiceBusClient, blob_name: str, reason: str, categories: list[str]) -> None:
    with sb_client.get_queue_sender(config.QUEUE_DEAD_LETTER) as sender:
        sender.send_messages(
            ServiceBusMessage(json.dumps({"blob_name": blob_name, "reason": reason, "categories": categories}))
        )


def _consume_loop() -> None:
    sb_client = ServiceBusClient(f"{config.SERVICEBUS_NAMESPACE}.servicebus.windows.net", credential=_credential)
    blob_client = _blob_client()
    with sb_client:
        with sb_client.get_queue_receiver(config.QUEUE_INGEST) as receiver:
            for msg in receiver:
                try:
                    _process_message(b"".join(msg.body), sb_client, blob_client)
                    receiver.complete_message(msg)
                except Exception:
                    receiver.dead_letter_message(msg)


@app.on_event("startup")
def _start_consumer() -> None:
    threading.Thread(target=_consume_loop, daemon=True).start()


# Declared last so it doesn't shadow /health and /api/extract above — Starlette
# matches routes in registration order and this mount catches everything else.
app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "frontend"), html=True), name="frontend")
