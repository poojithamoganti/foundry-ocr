"""Upload a PDF to blob storage and enqueue it for processing.
Usage: python scripts/submit_document.py path/to/document.pdf
"""
import json
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.storage.blob import BlobServiceClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config


def main(pdf_path: str) -> None:
    path = Path(pdf_path)
    credential = DefaultAzureCredential()

    blob_client = BlobServiceClient(
        f"https://{config.STORAGE_ACCOUNT}.blob.core.windows.net", credential=credential
    )
    blob_client.get_container_client(config.CONTAINER_SAMPLES).upload_blob(
        path.name, path.read_bytes(), overwrite=True
    )

    sb_client = ServiceBusClient(
        f"{config.SERVICEBUS_NAMESPACE}.servicebus.windows.net", credential=credential
    )
    with sb_client, sb_client.get_queue_sender(config.QUEUE_INGEST) as sender:
        sender.send_messages(ServiceBusMessage(json.dumps({"blob_name": path.name})))

    print(f"Submitted {path.name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
