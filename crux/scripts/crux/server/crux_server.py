#!/usr/bin/env python3
"""crux_server.py — crux FastAPI Server.

A small HTTP API for interacting with the crux toolkit. Intentionally
minimal: wraps the crux LLM caller (`crux.core.llm_caller`) and the
tracer (`crux.core.tracer`) and exposes `/health`, `/models`, and
`/chat`.

Run:
  uvicorn crux.server.crux_server:app --port 8000 --reload
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except Exception as e:  # pragma: no cover - import-time guard
    raise ImportError(
        "Crux HTTP service requires FastAPI dependencies.\n"
        "Launch with the dependencies in box/operator-services.md.\n"
        f"Import error: {e}"
    ) from e

from crux.core.llm_caller import (
    call_model,
    get_default_model,
    list_available_models,
)
from crux.core.tracer import Phase, get_tracer

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str = Field(..., description="user|assistant|system")
    content: str = Field(..., max_length=10000)


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    model: str = Field(
        default_factory=lambda: get_default_model("google_top"),
        description="Model key from crux/scripts/crux/_config/llm_router_config.json",
    )
    system: Optional[str] = Field(default=None, max_length=20000)
    history: Optional[List[ChatMessage]] = None
    session_id: Optional[str] = Field(
        default=None,
        description="Optional crux session_id for trace logging",
        max_length=100,
    )


class ChatResponse(BaseModel):
    reply: str
    model_used: str


app = FastAPI(title="crux Server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/models")
def models() -> Dict[str, Any]:
    try:
        return {"models": list_available_models()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        history_str = ""
        if request.history:
            lines = []
            for m in request.history[-20:]:
                lines.append(f"{m.role}: {m.content}")
            history_str = "\n".join(lines) + "\n\n"

        prompt = f"{history_str}user: {request.message}"
        reply = call_model(
            model=request.model,
            prompt=prompt,
            system=request.system,
        )

        if request.session_id:
            tracer = get_tracer(request.session_id)
            tracer.log(
                phase=Phase.EXECUTION,
                title="Server Chat",
                context=f"model={request.model}",
                reasoning=request.message[:2000],
                decision_action="Returned chat response",
                next_steps=[],
                metadata={"model": request.model},
            )

        return ChatResponse(reply=reply, model_used=request.model)
    except Exception as e:
        logger.error("Chat error", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
