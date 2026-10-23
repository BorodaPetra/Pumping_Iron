"""AI trainer: streaming chat endpoint that proxies to a local LM Studio server.

LM Studio exposes an OpenAI-compatible API. Its address and the model name come
from the environment so nothing is hard-coded to one machine:
  LMSTUDIO_URL    base URL, default http://localhost:1234/v1
  LMSTUDIO_MODEL  model identifier as shown in LM Studio, default "local-model"

The model can change the user's data through tools (see `trainer_tools`): when it
answers with tool calls, the server runs them, tells the user what was done, feeds the
results back to the model and lets it continue.
"""

import json
import os
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pumping_iron.db import connect
from pumping_iron.trainer_tools import TOOLS, build_context, run_tool

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "local-model")

# Safety stop for the model -> tools -> model loop.
MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = (
    "Ты персональный тренер и нутрициолог в тренажёрном зале. Отвечай по-русски, кратко и по делу. "
    "Помогай с программой тренировок (группы мышц, агонисты и антагонисты, объём, "
    "прогрессия нагрузок), техникой упражнений, восстановлением и питанием. "
    "Если данных не хватает, задай один уточняющий вопрос. При боли, травмах и "
    "заболеваниях советуй обратиться к врачу, не ставь диагнозов.\n\n"
    "У тебя есть инструменты. Когда пользователь просит составить или дополнить программу тренировок, "
    "добавляй упражнения сам через add_to_program: по одному вызову на упражнение, с днём недели, "
    "подходами, повторениями и рабочим весом. Когда он просит составить или дополнить рацион, "
    "добавляй продукты сам через add_to_diet. Не дублируй то, что уже есть в программе или рационе. "
    "Для обычных вопросов и советов инструменты не нужны. После вызовов коротко перечисли, что добавлено. "
    "Калории и БЖУ это оценки по справочным значениям, говори об этом.\n\n"
    "Ниже актуальные данные пользователя."
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
    """Yield the trainer's reply as text chunks, running tool calls along the way.

    Takes the conversation history. Each round calls LM Studio with `stream=true` and
    forwards the text of every SSE chunk; tool-call fragments are collected per call
    index. If the round ended with tool calls, they are executed against the database,
    a notice line per successful change is yielded to the user, and the results are sent
    back to the model for the next round. If LM Studio is unreachable or fails, a single
    human-readable error line is yielded so the page can show it.
    """
    db = connect()
    try:
        # The context is built once per request: it reflects the data at the start of the chat turn.
        payload_messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{build_context(db)}"}]
        payload_messages += [m.model_dump() for m in messages]
        # PERF: one shared AsyncClient (created at app startup) reuses the TCP connection
        # to LM Studio instead of opening a new one per message.
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
            for _ in range(MAX_TOOL_ROUNDS):
                payload = {"model": LMSTUDIO_MODEL, "stream": True, "tools": TOOLS, "messages": payload_messages}
                text, calls = "", {}
                async with client.stream("POST", f"{LMSTUDIO_URL}/chat/completions", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        # SSE lines look like `data: {...}`; the stream ends with `data: [DONE]`.
                        if not line.startswith("data: ") or line.endswith("[DONE]"):
                            continue
                        choices = json.loads(line[6:]).get("choices")
                        if not choices:
                            continue
                        delta = choices[0]["delta"]
                        if delta.get("content"):
                            text += delta["content"]
                            yield delta["content"]
                        # A tool call arrives in fragments: the name once, the JSON arguments in pieces.
                        for fragment in delta.get("tool_calls") or []:
                            call = calls.setdefault(fragment.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            call["id"] = fragment.get("id") or call["id"]
                            function = fragment.get("function") or {}
                            call["name"] = function.get("name") or call["name"]
                            call["arguments"] += function.get("arguments") or ""
                if not calls:
                    return

                payload_messages.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {"id": call["id"] or f"call_{index}", "type": "function",
                         "function": {"name": call["name"], "arguments": call["arguments"]}}
                        for index, call in calls.items()
                    ],
                })
                for index, call in calls.items():
                    # PERF: sqlite3 calls block the event loop; they are tiny here, but
                    # asyncio.to_thread(run_tool, ...) would keep it free under load.
                    result, notice = run_tool(db, call["name"], call["arguments"])
                    if notice:
                        yield f"\n{notice}\n"
                    payload_messages.append(
                        {"role": "tool", "tool_call_id": call["id"] or f"call_{index}", "content": result}
                    )
    except httpx.ConnectError:
        yield f"[LM Studio недоступен по адресу {LMSTUDIO_URL}. Запусти локальный сервер в LM Studio.]"
    except httpx.HTTPError as exc:
        yield f"[Ошибка LM Studio: {exc}]"
    finally:
        db.close()


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream the trainer's answer to the conversation as plain UTF-8 text."""
    return StreamingResponse(stream_reply(request.messages), media_type="text/plain; charset=utf-8")
