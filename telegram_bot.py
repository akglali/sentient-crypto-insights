import os
import json
import html
import asyncio
from typing import Optional, List, Dict, Any

import httpx
from ulid import ULID
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

# ----------------- Env -----------------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://127.0.0.1:8000/assist")

# ----------------- Simple session store (memory + autosave) -----------------
SESSIONS_PATH = "sessions.json"
SESSIONS: Dict[int, Dict[str, Any]] = {}  # chat_id -> session data

def h(x: str) -> str:
    """HTML-escape text for safe insertion into Telegram messages."""
    return html.escape(x or "", quote=True)

def _new_session() -> Dict[str, Any]:
    """Create a fresh session payload for the agent plus our own memory fields."""
    return {
        # What we send to the agent (/assist)
        "processor_id": str(ULID()),
        "activity_id": str(ULID()),
        "request_id": str(ULID()),
        "metadata": {},

        # Our own memory fields
        "last_token": None,
        "last_intent": None,
        "history": [],  # optional: we keep short history (user messages)
    }

def _load_sessions():
    global SESSIONS
    if os.path.exists(SESSIONS_PATH):
        try:
            with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # keys are strings in json; convert to ints
                SESSIONS = {int(k): v for k, v in data.items()}
        except Exception:
            SESSIONS = {}

def _save_sessions():
    try:
        with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(SESSIONS, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # no-op if we can't save; memory store keeps running

# Load sessions at import time
_load_sessions()

# ----------------- SSE client -----------------
class SSEEvent:
    def __init__(self, event: str, data: str):
        self.event = event or "message"
        self.data = data

async def sse_events(client: httpx.AsyncClient, url: str, payload: dict):
    """
    Single-request SSE reader. Posts JSON to /assist and yields SSE events.
    """
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }

    async with client.stream("POST", url, json=payload, headers=headers, timeout=None) as resp:
        if resp.status_code != 200:
            text = await resp.aread()
            raise httpx.HTTPStatusError(
                f"POST {url} -> {resp.status_code}\n{(text.decode('utf-8','ignore')[:1000] if text else '')}",
                request=resp.request,
                response=resp,
            )

        event_name: Optional[str] = None
        data_lines: List[str] = []

        async for raw_line in resp.aiter_lines():
            if raw_line is None:
                continue
            line = raw_line.rstrip("\n")

            if not line:
                if data_lines:
                    yield SSEEvent(event_name or "message", "\n".join(data_lines))
                    data_lines.clear()
                event_name = None
                continue

            if line.startswith(":"):
                continue

            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            else:
                data_lines.append(line)

# ----------------- Helpers: context-aware prompt -----------------
TRIGGER_WORDS = {"price", "news", "overview", "now", "price now", "news now", "check price", "check news"}

def build_contextual_prompt(raw: str, session: Dict[str, Any]) -> str:
    """
    If the user message is vague (e.g., 'price now'), attach the last token.
    Otherwise, just return the raw message.
    """
    msg = (raw or "").strip()
    # Simple heuristic: if message contains a token-like word, don't alter
    # Else, if it's a pure trigger, prefix last token
    if session.get("last_token"):
        lower = msg.lower()
        if lower in TRIGGER_WORDS:
            return f"{session['last_token']} {msg}"
        # Also handle one-word triggers
        if lower in {"price", "news", "overview", "now"}:
            return f"{session['last_token']} {msg}"
    return msg

# ----------------- Bot commands -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome/instructions with safe HTML formatting."""
    welcome_text = (
        "Hello! I'm your Crypto Insights Agent. I now remember context per chat 👇\n\n"
        "<b>1. Full Overview</b>\n"
        "Just send a coin name.\n"
        "<i>Example:</i> <code>bitcoin</code>\n\n"
        "<b>2. Specific Info</b>\n"
        "<i>Example:</i> <code>ethereum news</code>\n"
        "<i>Example:</i> <code>solana price</code>\n\n"
        "<b>3. Follow-ups (context-aware)</b>\n"
        "After asking about a coin, you can say: <code>price now</code>, <code>news</code>, or <code>overview</code>.\n\n"
        "<b>/reset</b> to clear memory for this chat."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    SESSIONS[chat_id] = _new_session()
    _save_sessions()
    await update.message.reply_text("✅ Memory cleared for this chat.", parse_mode=ParseMode.HTML)

# “Show More” pagination for tokens
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split('_')
    next_start_index_str = parts[-1]

    try:
        next_start_index = int(next_start_index_str)
    except (ValueError, IndexError):
        await query.edit_message_text(text="Invalid button data.")
        return

    full_token_list = context.bot_data.get('full_token_list', [])
    if not full_token_list:
        await query.edit_message_text(text="Sorry, the token list has expired. Please ask again.")
        return

    end_index = next_start_index + 20
    tokens_to_show = [f"• {h(token['text'])}" for token in full_token_list[next_start_index:end_index]]
    response_text = "✅ <b>Here are the top tokens available:</b>\n" + "\n".join(tokens_to_show)

    keyboard = []
    if end_index < len(full_token_list):
        new_button = InlineKeyboardButton("Show More ➡️", callback_data=f"more_tokens_{end_index}")
        keyboard.append([new_button])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=response_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ----------------- Core message handler (SSE + memory) -----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    # Ensure session exists
    session = SESSIONS.get(chat_id)
    if not session:
        session = _new_session()
        SESSIONS[chat_id] = session

    # Build contextual prompt (may auto-attach last token)
    user_message_raw = update.message.text
    user_message = build_contextual_prompt(user_message_raw, session)

    # Append user message to short history (cap to last 20)
    session["history"].append({"role": "user", "content": user_message_raw})
    session["history"] = session["history"][-20:]
    _save_sessions()

    status_message = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Processing: {h(user_message)}...",
        parse_mode=ParseMode.HTML
    )

    # Build the Sentient /assist payload (schema-friendly)
    payload = {
        "query": {
            "id": str(ULID()),
            "prompt": user_message,
            "metadata": {
                # Send last_token so your agent could use it later if needed
                "last_token": session.get("last_token"),
                "last_intent": session.get("last_intent"),
            }
        },
        "session": {
            "processor_id": session["processor_id"],
            "activity_id": session["activity_id"],
            "request_id": str(ULID()),  # new request id per message
            "metadata": {}
        },
        "files": [],
        "images": []
    }

    full_response = ""
    last_sent_text = ""

    try:
        async with httpx.AsyncClient() as client:
            async for sse in sse_events(client, AGENT_API_URL, payload):
                # Each SSEEvent has .event and .data (string). The agent sends JSON in data.
                try:
                    event_data = json.loads(sse.data) if sse.data else {}
                except json.JSONDecodeError:
                    event_data = {"message": sse.data}

                event = sse.event
                data = event_data

                # ---- memory updates from agent events ----
                # Your agent emits: intent_recognized { intent, token }
                if event == "intent_recognized":
                    intent = data.get("intent")
                    token = data.get("token")
                    if intent:
                        session["last_intent"] = intent
                    if token:
                        session["last_token"] = token
                    _save_sessions()

                # ---- normal rendering ----
                if event == "price_result":
                    price = data.get('price', 0)
                    market_cap = data.get('market_cap', 0)
                    block = (
                        f"💵 <b>Price:</b> ${price:,.2f}\n"
                        f"🏦 <b>Market Cap:</b> ${market_cap:,.0f}\n"
                    )
                    if full_response and not full_response.endswith("\n"):
                        full_response += "\n"
                    full_response += block + "\n"

                elif event == "news_result":
                    articles = data.get("articles", [])
                    headlines = [
                        f'• <a href="{h(a["url"])}">{h(a["title"])}</a>'
                        for a in articles if a.get("url") and a.get("title")
                    ]
                    if headlines:
                        if full_response and not full_response.endswith("\n"):
                            full_response += "\n"
                        full_response += "📰 <b>Recent News:</b>\n" + "\n".join(headlines) + "\n"

                elif event == "token_list_result":
                    tokens = data.get('tokens', [])
                    context.bot_data['full_token_list'] = tokens  # cache for pagination

                    tokens_to_show = [f"• {h(t['text'])}" for t in tokens[:20]]
                    block = "✅ <b>Here are the top tokens available:</b>\n" + "\n".join(tokens_to_show)

                    # “Show More” button if needed
                    keyboard = []
                    if len(tokens) > 20:
                        keyboard.append([InlineKeyboardButton("Show More ➡️", callback_data="more_tokens_20")])
                    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

                    full_response = block  # replace body for this branch so button attaches to current message
                    await status_message.edit_text(full_response, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                    last_sent_text = full_response
                    continue  # skip generic updater

                elif event == "wallet_info_result":
                    address = h(data.get('address', 'N/A'))
                    balance = h(data.get('eth_balance', 'N/A'))
                    normal_tx_count = h(str(data.get('normal_transaction_count', 'N/A')))
                    token_tx_count = h(str(data.get('token_transaction_count', 'N/A')))
                    first_tx = h(data.get('first_transaction', 'N/A'))
                    last_tx = h(data.get('last_transaction', 'N/A'))
                    url = h(data.get('etherscan_url', '#'))

                    block = (
                        f"📋 <b>Wallet Info for <code>{address}</code></b>\n\n"
                        f"💰 <b>Balance:</b> {balance}\n"
                        f"↔️ <b>ETH Transactions:</b> {normal_tx_count}\n"
                        f"🔄 <b>Token Transactions:</b> {token_tx_count}\n"
                        f"🗓️ <b>First Tx:</b> {first_tx}\n"
                        f"🗓️ <b>Last Tx:</b> {last_tx}\n\n"
                        f'<a href="{url}">View on Etherscan</a>'
                    )
                    if full_response and not full_response.endswith("\n"):
                        full_response += "\n"
                    full_response += block + "\n"

                elif event == "error":
                    msg = data.get("message") or str(data)
                    if full_response and not full_response.endswith("\n"):
                        full_response += "\n"
                    full_response += f"⚠️ <b>Error:</b> {h(msg)}\n"

                elif event in ("status_update", "LOG"):
                    msg = data if isinstance(data, str) else json.dumps(data)
                    if msg:
                        if full_response and not full_response.endswith("\n"):
                            full_response += "\n"
                        full_response += f"<i>({h(event)})</i> {h(msg)}\n"

                elif event == "done":
                    # no-op
                    pass

                # Throttle edits: only update when content changed
                if full_response and full_response != last_sent_text:
                    await status_message.edit_text(full_response, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    last_sent_text = full_response

    except httpx.HTTPStatusError as e:
        await status_message.edit_text(f"<b>Error:</b> {h(str(e))}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except httpx.RequestError as e:
        await status_message.edit_text(f"<b>Network error connecting to agent:</b> {h(str(e))}", parse_mode=ParseMode.HTML)
    except Exception as e:
        await status_message.edit_text(f"<b>Unexpected error:</b> {h(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        # Persist any session updates
        _save_sessions()

# ----------------- Main -----------------
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found.")
        return

    print(f"🚀 Starting Telegram bot (AGENT_API_URL={AGENT_API_URL})...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("✅ Bot is polling for messages...")
    app.run_polling()

if __name__ == '__main__':
    main()
