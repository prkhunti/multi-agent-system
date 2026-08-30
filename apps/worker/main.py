"""Document worker boundary for the upcoming Docling ingestion pipeline."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Start the worker placeholder until the ingestion queue is implemented."""
    logger.info("worker.start", extra={"component": "document-ingestion"})


if __name__ == "__main__":
    main()
