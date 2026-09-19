"""Virtual You local ingestion package."""

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.service import IngestionService

__all__ = ["ActivityRecord", "IngestionService"]
