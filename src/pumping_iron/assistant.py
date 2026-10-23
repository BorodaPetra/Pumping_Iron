"""AI trainer: the /assistant page and a streaming chat endpoint with two interchangeable models.

The user picks the model on the page, and each chat request carries the choice:
  "local"   a local LM Studio server (OpenAI-compatible API, written with httpx);
  "claude"  the Anthropic API (official SDK), needs a key in the environment.
Addresses, keys and model names come from the environment so nothing is hard-coded:
  LMSTUDIO_URL       base URL, default http://localhost:1234/v1
  LMSTUDIO_MODEL     model identifier as shown in LM Studio, default "local-model"
  ANTHROPIC_API_KEY  key for the Claude option (ANTHROPIC_AUTH_TOKEN also works)
  ANTHROPIC_MODEL    Claude model id, default "claude-opus-5-5"

Either model can change the user's data through tools (see `trainer_tools`): when it
answers with tool calls, the server runs them, tells the user what was done, feeds the
results back to the model and lets it continue.
"""

import json
import os
import sqlite3
from collections.abc import AsyncIterator
from typing import Literal

import anthropic
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from pumping_iron.db import connect
from pumping_iron.trainer_tools import TOOLS, build_context, run_tool
from pumping_iron.web import page

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "local-model")
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
CLAUDE_MAX_TOKENS = 16000

# The tools in the Anthropic format (the same definitions `TOOLS` holds in the OpenAI format).
CLAUDE_TOOLS = [
    {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
    for t in TOOLS
]

# Safety stop for the model -> tools -> model loop.
MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = (
    "Ты персональный тренер и нутрициолог в тренажёрном зале. Отвечай по-русски, кратко и по делу. "
    "Помогай с программой тренировок (группы мышц, агонисты и антагонисты, объём, "
    "прогрессия нагрузок), техникой упражнений, восстановлением и питанием. "
    "Если данных не хватает, задай один уточняющий вопрос. При боли, травмах и "
    "заболеваниях советуй обратиться к врачу, не ставь диагнозов.\n\n"
    "У тебя есть инструменты. Когда пользователь просит составить или дополнить программу тренировок, "
    "добавляй упражнения сам через add_to_program: по одному вызову на упражнение (можно несколько вызовов в одном ответе), с днём недели, "
    "подходами и повторениями. Рабочий вес передавай, только если пользователь его назвал; иначе оставь 0 и в итоге "
    "скажи, что вес нужно задать. Не выдумывай вес. Когда он просит составить или дополнить рацион, "
    "добавляй продукты сам через add_to_diet. Не дублируй то, что уже есть в программе или рационе. "
    "Для обычных вопросов и советов инструменты не нужны. После вызовов инструментов обязательно напиши "
    "короткий итог текстом: что добавлено и чего не хватило. "
    "Калории и БЖУ это оценки по справочным значениям, говори об этом.\n\n"
    "Ниже актуальные данные пользователя."
)

router = APIRouter()


def claude_ready() -> bool:
    """True when the environment holds Anthropic credentials, so the Claude option can be used."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class Message(BaseModel):
    """One chat turn; the system prompt is added by the server, never by the client."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """Whole conversation so far plus the chosen model; the server keeps no chat state."""

    messages: list[Message]
    provider: Literal["local", "claude"] = "local"


def event(kind: str, value: str) -> str:
    """Encode one stream event as an NDJSON line.

    `kind` is "text" (model output, goes into the chat history), "notice" (what the server
    changed, shown to the user only) or "error" (shown only, nothing is saved).
    """
    return json.dumps({"type": kind, "value": value}, ensure_ascii=False) + "\n"


def parse_sse_line(line: str) -> dict | None:
    """Return the JSON object of an SSE `data:` line, or None for anything else.

    Skips non-data lines, the `[DONE]` marker, empty payloads and lines that are not
    valid JSON (for example a truncated chunk), so one bad line cannot kill the stream.
    """
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _stream_local(db: sqlite3.Connection, system: str, messages: list[Message]) -> AsyncIterator[str]:
    """Yield the reply of the local LM Studio model as NDJSON events, running tool calls along the way.

    Takes the connection, the full system prompt and the conversation history. Each round
    calls LM Studio with `stream=true` and forwards the text of every SSE chunk; tool-call
    fragments are collected per call index. If the round ended with tool calls, they are
    executed against the database, a "notice" event per successful change is yielded, and
    the results are sent back to the model for the next round. The last allowed round is
    sent without tools, so the model has to finish with a text answer. An error reported
    by LM Studio, an unreachable server or an HTTP failure becomes a single "error" event.
    """
    try:
        payload_messages = [{"role": "system", "content": system}]
        payload_messages += [m.model_dump() for m in messages]
        # PERF: one shared AsyncClient (created at app startup) reuses the TCP connection
        # to LM Studio instead of opening a new one per message.
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
            for round_number in range(MAX_TOOL_ROUNDS):
                payload = {"model": LMSTUDIO_MODEL, "stream": True, "messages": payload_messages}
                if round_number < MAX_TOOL_ROUNDS - 1:
                    payload["tools"] = TOOLS
                text, calls = "", {}
                async with client.stream("POST", f"{LMSTUDIO_URL}/chat/completions", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        chunk = parse_sse_line(line)
                        if chunk is None:
                            continue
                        if "error" in chunk:
                            error = chunk["error"]
                            message = error.get("message") if isinstance(error, dict) else error
                            yield event("error", f"Ошибка LM Studio: {message}")
                            return
                        choices = chunk.get("choices")
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            text += delta["content"]
                            yield event("text", delta["content"])
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
                        yield event("notice", notice)
                    payload_messages.append(
                        {"role": "tool", "tool_call_id": call["id"] or f"call_{index}", "content": result}
                    )
            # Every round ended with tool calls, even the one sent without tools.
            yield event("error", "Достигнут лимит шагов, модель так и не дала итогового ответа.")
    except httpx.ConnectError:
        yield event("error", f"LM Studio недоступен по адресу {LMSTUDIO_URL}. Запусти локальный сервер в LM Studio.")
    except httpx.HTTPError as exc:
        yield event("error", f"Ошибка LM Studio: {exc}")


async def _stream_claude(db: sqlite3.Connection, system: str, messages: list[Message]) -> AsyncIterator[str]:
    """Yield the reply of Claude (Anthropic API) as NDJSON events, running tool calls along the way.

    Same contract as `_stream_local`. Each round streams one Messages request and forwards the
    text deltas; when the finished message asks for tools they are executed, a "notice" event
    is yielded per successful change, and the whole assistant message (thinking blocks
    included, the API needs them back) plus the tool results go into the next round. The
    last allowed round is sent without tools. Missing credentials, a refusal, rate limits and
    network or API failures become a single "error" event.
    """
    if not claude_ready():
        yield event("error", "Для Claude нужен ключ: задай переменную окружения ANTHROPIC_API_KEY и перезапусти сервер.")
        return
    # PERF: one shared AsyncAnthropic created at app startup would reuse the connection pool across messages.
    client = anthropic.AsyncAnthropic()
    api_messages = [m.model_dump() for m in messages]
    try:
        for round_number in range(MAX_TOOL_ROUNDS):
            tools = CLAUDE_TOOLS if round_number < MAX_TOOL_ROUNDS - 1 else anthropic.omit
            async with client.messages.stream(
                model=CLAUDE_MODEL, max_tokens=CLAUDE_MAX_TOKENS, system=system, tools=tools, messages=api_messages
            ) as stream:
                async for text in stream.text_stream:
                    yield event("text", text)
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield event("error", "Claude отказался отвечать на этот запрос.")
                return
            calls = [block for block in final.content if block.type == "tool_use"]
            if not calls:
                return

            api_messages.append({"role": "assistant", "content": final.content})
            results = []
            for call in calls:
                # PERF: sqlite3 calls block the event loop; asyncio.to_thread(run_tool, ...) would keep it free.
                result, notice = run_tool(db, call.name, json.dumps(call.input))
                if notice:
                    yield event("notice", notice)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": result})
            api_messages.append({"role": "user", "content": results})
        yield event("error", "Достигнут лимит шагов, Claude так и не дал итогового ответа.")
    except anthropic.AuthenticationError:
        yield event("error", "Anthropic отклонил ключ: проверь ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        yield event("error", "Anthropic: превышен лимит запросов, попробуй через минуту.")
    except anthropic.APIConnectionError:
        yield event("error", "Не удалось связаться с Anthropic API: проверь интернет.")
    except anthropic.APIStatusError as exc:
        yield event("error", f"Ошибка Anthropic API ({exc.status_code}): {exc.message}")


async def stream_reply(messages: list[Message], provider: str) -> AsyncIterator[str]:
    """Yield the trainer's reply as NDJSON events from the chosen model ("local" or "claude").

    Builds the system prompt with the live data once per request (it reflects the data at the
    start of the chat turn) and closes the database connection when the stream ends.
    """
    db = connect()
    try:
        system = f"{SYSTEM_PROMPT}\n\n{build_context(db)}"
        backend = _stream_claude if provider == "claude" else _stream_local
        async for item in backend(db, system, messages):
            yield item
    finally:
        db.close()


@router.get("/assistant", response_class=HTMLResponse)
async def assistant_page(request: Request) -> HTMLResponse:
    """The chat page; tells the template whether the Claude option can be used and which model it is."""
    return page(request, "assistant.html", "yellow", claude_ready=claude_ready(), claude_model=CLAUDE_MODEL)


@router.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream the trainer's answer as NDJSON events (one JSON object per line, see `event`)."""
    return StreamingResponse(
        stream_reply(request.messages, request.provider), media_type="application/x-ndjson; charset=utf-8"
    )
