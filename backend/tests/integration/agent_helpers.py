"""A scripted assistant for API-level agent tests: no network, no API keys."""

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient, Response
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.agent.runner import Assistant
from app.core.config import Settings
from app.core.security import create_access_token
from app.llm.scripted import ScriptedChatModel
from app.models import User

Step = AIMessage | Callable[[list[BaseMessage]], AIMessage]


def call(name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None) -> AIMessage:
    return AIMessage(
        "",
        tool_calls=[
            {"name": name, "args": args or {}, "id": call_id or f"call_{uuid.uuid4().hex}"}
        ],
    )


def say(text: str) -> AIMessage:
    return AIMessage(text)


@dataclass
class Script:
    """Answers each model call with the next step. A step may wait on `gate`, to hold a
    run in the middle while a test does something else."""

    steps: list[Step] = field(default_factory=list)
    gate: threading.Event | None = None

    def add(self, *steps: Step) -> None:
        self.steps.extend(steps)

    def __call__(self, messages: list[BaseMessage]) -> AIMessage:
        if self.gate is not None:
            assert self.gate.wait(10), "the test never opened the gate"
        assert self.steps, "the model was called more often than scripted"
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


@dataclass
class Harness:
    app: FastAPI
    client: AsyncClient
    model: ScriptedChatModel
    script: Script
    settings: Settings

    @property
    def assistant(self) -> Assistant:
        assistant: Assistant = self.app.state.assistant
        return assistant

    def headers(self, user: User) -> dict[str, str]:
        token, _ = create_access_token(
            user.id,
            secret=self.settings.jwt_secret.get_secret_value(),
            ttl=timedelta(minutes=15),
        )
        return {"Authorization": f"Bearer {token}"}

    async def new_thread(self, user: User) -> str:
        response = await self.client.post("/api/v1/assistant/threads", headers=self.headers(user))
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    async def send(self, user: User, thread_id: str, text: str) -> Response:
        return await self.client.post(
            f"/api/v1/assistant/threads/{thread_id}/messages",
            json={"content": text},
            headers=self.headers(user),
        )

    async def resume(self, user: User, thread_id: str, decision: str) -> Response:
        return await self.client.post(
            f"/api/v1/assistant/threads/{thread_id}/resume",
            json={"decision": decision},
            headers=self.headers(user),
        )


def events(response: Response) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in response.text.split("\n\n") if f.strip()]
    parsed = []
    for frame in frames:
        assert frame.startswith("data: "), frame
        parsed.append(json.loads(frame.removeprefix("data: ")))
    return parsed


def types(stream: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in stream]


def tool_results(messages: list[BaseMessage]) -> list[tuple[str, dict[str, Any]]]:
    """(tool name, parsed result) for every tool message the model was shown."""
    return [
        (str(m.name), json.loads(str(m.content))) for m in messages if isinstance(m, ToolMessage)
    ]


def unanswered_calls(messages: list[BaseMessage]) -> list[str]:
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [
        call["id"] or ""
        for m in messages
        if isinstance(m, AIMessage)
        for call in m.tool_calls
        if call["id"] not in answered
    ]
