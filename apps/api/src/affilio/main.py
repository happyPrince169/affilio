"""ASGI entrypoint: `uvicorn affilio.main:app`."""

from affilio.app import create_app

app = create_app()
