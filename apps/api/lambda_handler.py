"""AWS Lambda adapter for the free-plan demonstration stack."""

from __future__ import annotations

from mangum import Mangum

from apps.api.main import app

handler = Mangum(app, lifespan="auto")
