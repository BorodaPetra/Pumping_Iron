"""AI trainer: streaming chat endpoint that proxies to a local LM Studio server.

LM Studio exposes an OpenAI-compatible API. Its address and the model name come
from the environment so nothing is hard-coded to one machine:
  LMSTUDIO_URL    base URL, default http://localhost:1234/v1
  LMSTUDIO_MODEL  model identifier as shown in LM Studio, default "local-model"
"""

import json
import os
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "local-model")

SYSTEM_PROMPT = (
    "Ты персональный тренер в тренажёрном зале. Отвечай по-русски, кратко и по делу. "
    "Помогай с программой тренировок (группы мышц, агонисты и антагонисты, объём, "
    "прогрессия нагрузок), техникой упражнений, восстановлением и питанием. "
    "Если данных не хватает, задай один уточняющий вопрос. При боли, травмах и "
    "заболеваниях советуй обратиться к врачу, не ставь диагнозов."
)

router = APIRouter(prefix="/api")


class Message(BaseModel):
    """One chat turn; the system prompt is added by the server, never by the client."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """Whole conversation so far; the server keeps no chat state."""

    messages: list[Message]


async def stream_reply(messages: list[Message]) -> AsyncIterator[str]:
    """Yield the model's reply token by token.

    Takes the conversation history, calls LM Studio with `stream=true` and
    forwards the text of every SSE chunk. If LM Studio is unreachable or fails,
    yields a single human-readable error line instead, so the page can show it.
    """
    payload = {
        "model": LMSTUDIO_MODEL,
        "stream": True,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
        + [m.model_dump() for m in messages],
    }
    try:
        # PERF: one shared AsyncClient (created at app startup) reuses the TCP connection
        # to LM Studio instead of opening a new one per message.
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
            async with client.stream("POST", f"{LMSTUDIO_URL}/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    # SSE lines look like `data: {...}`; the stream ends with `data: [DONE]`.
                    if not line.startswith("data: ") or line.endswith("[DONE]"):
                        continue
                    choices = json.loads(line[6:]).get("choices")
                    if choices:
                        yield choices[0]["delta"].get("content") or ""
    except httpx.ConnectError:
        yield f"[LM Studio недоступен по адресу {LMSTUDIO_URL}. Запусти локальный сервер в LM Studio.]"
    except httpx.HTTPError as exc:
        yield f"[Ошибка LM Studio: {exc}]"


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream the trainer's answer to the conversation as plain UTF-8 text."""
    return StreamingResponse(stream_reply(request.messages), media_type="text/plain; charset=utf-8")
