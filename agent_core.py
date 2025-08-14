import os
import requests
import psycopg2
import json 
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import re 


FULL_TOKEN_LIST_DETAILED  = []


load_dotenv()

def get_token_price_from_api(token_id: str):
    """(Helper Function) Fetches price data directly from the CoinGecko API."""
    print(f"FETCHING FROM API: Getting fresh price for '{token_id}'.")
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={token_id}&vs_currencies=usd&include_market_cap=true&include_24hr_vol=true"
        response = requests.get(url)
        response.raise_for_status() 
        data = response.json()
        if not data or token_id not in data:
            return {"error": f"Could not find price data for '{token_id}'."}
        price_data = data[token_id]
        return {
            "price": price_data.get('usd'),
            "market_cap": price_data.get('usd_market_cap'),
            "volume_24h": price_data.get('usd_24h_vol')
        }
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}"}

def get_smart_token_price(token_id: str):
    """
    Fetches token price, first checking the cache, then falling back to the API.
    """
    conn = get_db_connection()
    if not conn:
        return {"error": "Database connection failed."}

    price_data = None
    cur = conn.cursor()

    try:
        # 1. Check the cache first
        five_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
        cur.execute(
            "SELECT price, market_cap, volume_24h FROM price_history WHERE token_id = %s AND fetched_at >= %s ORDER BY fetched_at DESC LIMIT 1",
            (token_id, five_minutes_ago)
        )
        cached_result = cur.fetchone()

        if cached_result:
            # CACHE HIT
            print(f"CACHE HIT: Found recent price for '{token_id}' in DB.")
            price_data = {
                "price": float(cached_result[0]),
                "market_cap": float(cached_result[1]),
                "volume_24h": float(cached_result[2])
            }
        else:
            # CACHE MISS
            print(f"CACHE MISS: No recent data for '{token_id}'. Fetching from API.")
            api_data = get_token_price_from_api(token_id)
            if 'error' in api_data:
                return api_data # Pass the error through

            # 2. Save the new data to the cache
            cur.execute(
                "INSERT INTO price_history (token_id, price, market_cap, volume_24h) VALUES (%s, %s, %s, %s)",
                (token_id, api_data['price'], api_data['market_cap'], api_data['volume_24h'])
            )
            conn.commit()
            print(f"CACHE WRITE: Saved new price for '{token_id}' to DB.")
            price_data = api_data

    except Exception as e:
        print(f"An error occurred in get_smart_token_price: {e}")
        conn.rollback()
        return {"error": "An internal database error occurred."}
    finally:
        cur.close()
        conn.close()

    return price_data

def get_news(query:str):
     """Fetches recent news articles for a given query from NewsAPI."""
     api_key = os.getenv("NEWS_API_KEY")
     if not api_key:
          return{"error": "NewsAPI key not found. Please set NEWS_API_KEY in your .env file."}
     
     try:
          search_query = f'"{query}" cryptocurrency'
          url = f"https://newsapi.org/v2/everything?q={search_query}&sortBy=publishedAt&pageSize=5&apiKey={api_key}"
          response = requests.get(url)
          response.raise_for_status()
          data = response.json()

          #Extract headlines for demo
          articles = [
            {"title": article['title'], "url": article['url']} for article in data.get('articles', [])]
        
          return {"articles": articles}
     
     except requests.exceptions.RequestException as e:
          return {"error": f"API request failed:{e}"}
     
def get_smart_news(query: str):
    """Fetches news (with links), using the cache first."""
    conn = get_db_connection()
    if not conn: return {"error": "Database connection failed."}
    news_data = None
    cur = conn.cursor()
    try:
        sixty_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=60)
        # CORRECTED: Selects from the 'articles' column
        cur.execute("SELECT articles FROM news_cache WHERE query = %s AND fetched_at >= %s ORDER BY fetched_at DESC LIMIT 1", (query, sixty_minutes_ago))
        cached_result = cur.fetchone()

        if cached_result:
            print(f"CACHE HIT: Found recent news for '{query}' in DB.")
            news_data = {"articles": cached_result[0]}
        else:
            print(f"CACHE MISS: No recent news for '{query}'. Fetching from API.")
            api_data = get_news(query)
            if 'error' in api_data: return api_data

            # CORRECTED: Inserts into the 'articles' column
            articles_json = json.dumps(api_data['articles'])
            cur.execute("INSERT INTO news_cache (query, articles) VALUES (%s, %s)", (query, articles_json))
            conn.commit()
            print(f"CACHE WRITE: Saved new news for '{query}' to DB.")
            news_data = api_data
    except Exception as e:
        conn.rollback()
        # This will now correctly show the underlying error if one occurs
        return {"error": f"An internal database error occurred: {e}"}
    finally:
        cur.close()
        conn.close()
    return news_data
def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
       
        conn = psycopg2.connect(
            dbname="crypto_insights_agent",
            user="postgres",
            password="143256",
            host="localhost", # or your db host
            port="5432"
        )
        return conn
    except psycopg2.OperationalError as e:
        print(f"🔴 Could not connect to the database: {e}")
        return None

def parse_intent(question: str):
    """A simple keyword-based intent parser."""
    question = question.lower() # Convert to lowercase for easier matching

    if "price" in question or "how much" in question or "cost" in question:
        return "GET_PRICE"
    elif "news" in question or "headlines" in question or "latest" in question or "what's new" in question:
        return "GET_NEWS"
    else:
        # Default action if no specific keywords are found
        return "GET_OVERVIEW"
    
def get_token_list():
    """
    Fetches a list of the top 250 cryptocurrencies from CoinGecko.
    """
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
        response = requests.get(url)
        response.raise_for_status()
        
        # We only need the id, symbol, and name for the dropdown
        token_list = [
            {"id": token['id'], "text": f"{token['name']} ({token['symbol'].upper()})"}
            for token in response.json()
        ]
        return token_list
    except requests.exceptions.RequestException as e:
        # In a real app, you'd want more robust error handling
        return [{"id": "", "text": "Error fetching tokens"}]
    
def get_token_list_simple():
    """ A simplified, hardcoded list for local parsing. """
    # You can expand this list for better recognition
    return ["bitcoin", "ethereum", "solana", "ripple", "dogecoin", "cardano", "tether", "litecoin"]

def parse_natural_language_query(text: str):
    """
    Extracts intent and token/wallet address from a natural language query.
    """
    text_lower = text.lower().strip()
    
    # re.search finds the pattern anywhere in the string
    wallet_match = re.search(r'0x[a-f0-9]{40}', text_lower)
    if wallet_match:
        # Extract the address found in the text
        address = wallet_match.group(0)
        return {"intent": "GET_WALLET_INFO", "token_id": address}
    # --- END OF MODIFICATION ---

    if "list" in text_lower or ("show" in text_lower and "token" in text_lower):
        return {"intent": "LIST_TOKENS", "token_id": None}
    
    intent = "GET_OVERVIEW"
    if "price" in text_lower or "how much" in text_lower or "cost" in text_lower:
        intent = "GET_PRICE"
    elif "news" in text_lower or "headlines" in text_lower or "latest" in text_lower:
        intent = "GET_NEWS"
        
    token_found = None
    for token_info in FULL_TOKEN_LIST_DETAILED:
        if text_lower == token_info['id'] or text_lower == token_info['name'] or text_lower == token_info['symbol']:
            token_found = token_info['id']
            break
        if (f" {token_info['name']} " in f" {text_lower} " or
            f" {token_info['symbol']} " in f" {text_lower} " or
            f" {token_info['id']} " in f" {text_lower} "):
            token_found = token_info['id']
            break

    return {"intent": intent, "token_id": token_found}

def get_wallet_info(address: str):
    """
    Fetches detailed information for a given Ethereum wallet address.
    """
    api_key = os.getenv("ETHERSCAN_API_KEY")
    if not api_key:
        return {"error": "Etherscan API key not found."}
    
    try:
        # --- Get ETH Balance (same as before) ---
        balance_url = f"https://api.etherscan.io/api?module=account&action=balance&address={address}&tag=latest&apikey={api_key}"
        balance_response = requests.get(balance_url)
        balance_response.raise_for_status()
        balance_data = balance_response.json()
        eth_balance = 0
        if balance_data.get('status') == '1':
            eth_balance = int(balance_data.get('result', 0)) / 10**18
        else:
            return {"error": balance_data.get('message', 'Failed to fetch balance')}

        # --- Get Normal Transaction List ---
        normal_tx_url = f"https://api.etherscan.io/api?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&sort=asc&apikey={api_key}"
        normal_tx_response = requests.get(normal_tx_url)
        normal_tx_response.raise_for_status()
        normal_tx_data = normal_tx_response.json()
        
        normal_tx_list = []
        if normal_tx_data.get('status') == '1' and normal_tx_data.get('result') is not None:
            normal_tx_list = normal_tx_data.get('result', [])

        # --- Get ERC-20 Token Transaction List (same as before) ---
        token_tx_url = f"https://api.etherscan.io/api?module=account&action=tokentx&address={address}&startblock=0&endblock=99999999&sort=asc&apikey={api_key}"
        token_tx_response = requests.get(token_tx_url)
        token_tx_response.raise_for_status()
        token_tx_data = token_tx_response.json()
        token_tx_count = 0
        if token_tx_data.get('status') == '1' and token_tx_data.get('result') is not None:
            token_tx_count = len(token_tx_data.get('result', []))

        # --- Calculate New Data Points ---
        first_tx_date = "N/A"
        last_tx_date = "N/A"
        if normal_tx_list:
            first_tx_timestamp = int(normal_tx_list[0]['timeStamp'])
            first_tx_date = datetime.utcfromtimestamp(first_tx_timestamp).strftime('%Y-%m-%d')
            last_tx_timestamp = int(normal_tx_list[-1]['timeStamp'])
            last_tx_date = datetime.utcfromtimestamp(last_tx_timestamp).strftime('%Y-%m-%d')

        return {
            "address": address,
            "etherscan_url": f"https://etherscan.io/address/{address}",
            "eth_balance": f"{eth_balance:.4f} ETH",
            "normal_transaction_count": len(normal_tx_list),
            "token_transaction_count": token_tx_count,
            "first_transaction": first_tx_date,
            "last_transaction": last_tx_date
        }

    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}"}
def load_full_token_list():
    """
    Fetches the full list of tokens and caches their id, name, and symbol.
    """
    global FULL_TOKEN_LIST_DETAILED
    print("Caching detailed token list from CoinGecko...")
    try:
        # get_token_list() already fetches the full market data
        token_data = get_token_list()
        # Store a dictionary for each token with all relevant names
        for token in token_data:
            # The 'text' field is like "Pi Network (PI)"
            # We extract "Pi Network" and "pi" from it
            name_part = token['text'].split(' (')[0]
            FULL_TOKEN_LIST_DETAILED.append({
                "id": token['id'].lower(),
                "name": name_part.lower(),
                "symbol": token['text'].split('(')[-1][:-1].lower() # Extracts the symbol e.g. "pi"
            })
        print(f"✅ Success! Cached {len(FULL_TOKEN_LIST_DETAILED)} detailed tokens.")
    except Exception as e:
        print(f"⚠️ Error caching detailed token list: {e}.")
        # Fallback with detailed info
        FULL_TOKEN_LIST_DETAILED = [
            {'id': 'bitcoin', 'name': 'bitcoin', 'symbol': 'btc'},
            {'id': 'ethereum', 'name': 'ethereum', 'symbol': 'eth'},
            {'id': 'pi-network', 'name': 'pi network', 'symbol': 'pi'}, # Example for Pi
        ]

