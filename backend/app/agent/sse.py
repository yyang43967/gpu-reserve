"""Server-sent events for assistant runs: `data: {json}` frames, one per event."""

import json
from typing import Any, Final

# Disables proxy buffering (nginx honors the header; Caddy is configured to flush).
SSE_HEADERS: Final = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
SSE_MEDIA_TYPE: Final = "text/event-stream"


def event(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, **data}


def encode(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'), default=str)}\n\n".encode()
