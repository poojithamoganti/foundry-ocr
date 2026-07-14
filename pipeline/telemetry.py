"""Audit trail: every OCR/KIE call is a traced span in the App Insights instance
linked to the Foundry hub (infra/bicep/shared.bicep + foundry.bicep), so document
processing history is queryable from the same place as the rest of the project's
governance data — no bespoke logging store.
"""
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace

from . import config

# Guarded: an empty connection string (local dev, tests, or a deploy-ordering gap
# before App Insights exists) would otherwise crash the whole app at import time.
if config.APPLICATIONINSIGHTS_CONNECTION_STRING:
    configure_azure_monitor(connection_string=config.APPLICATIONINSIGHTS_CONNECTION_STRING)

tracer = trace.get_tracer("foundry_ocr.pipeline")
