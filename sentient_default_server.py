import json
import argparse

from sentient_agent_framework import AbstractAgent, DefaultServer, Session, Query, ResponseHandler
from agent_core import load_full_token_list
from main import stream_agent_response  # your async NDJSON generator

class CryptoSentientAgent(AbstractAgent):
    def __init__(self):
        super().__init__("Crypto Insights Agent")
        load_full_token_list()

    async def assist(self, session: Session, query: Query, response_handler: ResponseHandler):
        # Bridge NDJSON lines -> framework events
        async for line in stream_agent_response(query.prompt or ""):
            if not line:
                continue
            try:
                payload = json.loads(line.strip())
            except json.JSONDecodeError:
                await response_handler.emit_text_block("LOG", line.strip())
                continue

            event = payload.get("event") or "message"
            data = payload.get("data")
            if isinstance(data, (dict, list)):
                await response_handler.emit_json(event, data)
            else:
                await response_handler.emit_text_block(event, str(data))

        await response_handler.complete()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)  # run alongside shim on :8000
    args = parser.parse_args()

    server = DefaultServer(CryptoSentientAgent())

    # DefaultServer exposes the FastAPI app as _app (private attr)
    # Run uvicorn directly so we control the port.
    import uvicorn
    uvicorn.run(server._app, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
