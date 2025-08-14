import asyncio
import json
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from agent_core import get_smart_token_price, get_smart_news, parse_natural_language_query, get_token_list,load_full_token_list,get_wallet_info
from contextlib import asynccontextmanager

# The server will only accept a single 'question' field.
class QueryRequest(BaseModel):
    question: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This code runs when the server starts up
    load_full_token_list()
    yield

app = FastAPI(title="Crypto Insights Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



async def stream_agent_response(question: str):
    def format_event(event_name: str, data: dict | list | str) -> str:
        return f"{json.dumps({'event': event_name, 'data': data})}\n"
    
    # The server now does the parsing internally.
    parsed_query = parse_natural_language_query(question)
    intent = parsed_query["intent"]
    token = parsed_query["token_id"]

    yield format_event("intent_recognized", {"intent": intent, "token": token})
    await asyncio.sleep(0.1)

    if intent == "GET_WALLET_INFO":
        address = token # We stored the address in the 'token' variable
        yield format_event("status_update", f"Fetching info for wallet {address[:6]}...{address[-4:]}")
        wallet_data = get_wallet_info(address)
        yield format_event("wallet_info_result", wallet_data)
        yield format_event("done", "Stream finished.")
        return
    
    if intent == "LIST_TOKENS":
        yield format_event("status_update", "Fetching full token list...")
        token_list = get_token_list() # This calls our existing function
        yield format_event("token_list_result", {"tokens": token_list})
        yield format_event("done", "Stream finished.")
        return

    if not token:
        yield format_event("error", {"message": "Sorry, I couldn't identify a cryptocurrency in your question."})
        yield format_event("done", "Stream finished.")
        return

    if intent in ["GET_PRICE", "GET_OVERVIEW"]:
        yield format_event("status_update", f"Fetching price for {token}...")
        price_data = get_smart_token_price(token)
        if "error" in price_data: yield format_event("error", price_data)
        else: yield format_event("price_result", price_data)
        await asyncio.sleep(0.1)

    if intent in ["GET_NEWS", "GET_OVERVIEW"]:
        yield format_event("status_update", f"Fetching news for {token}...")
        news_data = get_smart_news(token)
        if "error" in news_data: yield format_event("error", news_data)
        else: yield format_event("news_result", news_data)
        await asyncio.sleep(0.1)
    
    yield format_event("done", "Stream finished.")

@app.post("/query")
async def handle_agent_query(request: QueryRequest):
    # It receives the request and passes the question to the streamer.
    return StreamingResponse(
        stream_agent_response(request.question),
        media_type="application/x-ndjson"
    )

@app.get("/get_tokens")
def get_tokens_endpoint():
    return get_token_list()

@app.get("/")
def read_root():
    return {"status": "Crypto Insights Agent is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)