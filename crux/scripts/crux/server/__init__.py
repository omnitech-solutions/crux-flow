"""crux Server Module.

FastAPI server for LLM chat and other services.

Components:
- crux_server: Main FastAPI application with chat endpoints.
"""

from .crux_server import app

__all__ = ["app"]
