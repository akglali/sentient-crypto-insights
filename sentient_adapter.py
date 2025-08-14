# sentient_adapter.py
import json
from typing import AsyncGenerator, Dict, Any, Optional

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ulid import ULID

from agent_core import load_full_token_list
from main import stream_agent_response  # your existing async NDJSON generator

app = FastAPI(title="Sentient Assist Shim")

# ---- flexible request parsing ----
class QueryModel(BaseModel):
    id: Optional[str] = None
    prompt: str

class SessionModel(BaseModel):
    processor_id: Optional[str] = None
    activity_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class AssistBody(BaseModel):
    # we accept either top-level prompt or wrapped query
    prompt: Optional[str] = None
    query: Optional[QueryModel] = None
    session: Optional[SessionModel] = None
    files: Optional[list] = None
    images: Optional[list] = None

def _extract_prompt_and_session(body: AssistBody) -> tuple[str, SessionModel]:
    # 1) prefer wrapped query.prompt
    if body.query and body.query.prompt:
        prompt = body.query.prompt
    elif body.prompt:
        prompt = body.prompt
    else:
        raise ValueError("Missing prompt (use 'prompt' or 'query.prompt').")

    # Ensure IDs are present so any upstream that expects them is satisfied
    if body.query and not body.query.id:
        body.query.id = str(ULID())

    session = body.session or SessionModel()
    if not session.processor_id:
        session.processor_id = str(ULID())
    if not session.activity_id:
        session.activity_id = str(ULID())
    if not session.request_id:
        session.request_id = str(ULID())
    if session.metadata is None:
        session.metadata = {}
    return prompt, session

async def _sse_adapter(prompt: str) -> AsyncGenerator[bytes, None]:
    """
    Adapt your NDJSON generator into SSE lines.
    Your generator yields lines like: {"event": "...", "data": ...}\n
    We convert them to:
        event: <event>
        data: <json>
        \n
    """
    try:
        async for line in stream_agent_response(prompt):
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # Treat raw line as a log message
                event, data = "LOG", {"message": line.strip()}
            else:
                event = payload.get("event") or "message"
                data = payload.get("data")
                # normalize primitives
                if not isinstance(data, (dict, list, str, int, float, bool)) and data is not None:
                    data = str(data)

            yield f"event: {event}\n".encode("utf-8")
            yield f"data: {json.dumps(data)}\n\n".encode("utf-8")

        # optional sentinel
        yield b"event: done\ndata: {}\n\n"
    except Exception as e:
        err = {"message": str(e)}
        yield b"event: error\n"
        yield f"data: {json.dumps(err)}\n\n".encode("utf-8")

@app.post("/assist")
async def assist(request: Request):
    try:
        body_json = await request.json()
    except Exception:
        return Response(
            content='{"detail":"invalid JSON body"}',
            media_type="application/json",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        body = AssistBody(**body_json)
        prompt, _session = _extract_prompt_and_session(body)
    except Exception as e:
        return Response(
            content=json.dumps({"detail": str(e)}),
            media_type="application/json",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    generator = _sse_adapter(prompt)
    return StreamingResponse(generator, media_type="text/event-stream")

if __name__ == "__main__":
    # Warm up token metadata just like before
    load_full_token_list()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
