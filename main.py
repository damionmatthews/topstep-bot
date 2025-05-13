# main.py
import os
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from dotenv import load_dotenv
import logging

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()  # Load from .env file for local development

# --- Configuration ---
TOPSTEP_USERNAME = "dcminsf"
TOPSTEP_API_KEY = "hUcaJ5J88F92wlp2J0byZPRicRQwrW5EtPTNSkfZSac="
ACCOUNT_ID_STR = "dcminsf"
BASE_URL = "https://gateway-api-demo.s2f.projectx.com" # IMPORTANT: Use production URL eventually

# Trading Parameters (Load from Env or keep defaults)
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", -1200))
MAX_DAILY_PROFIT = float(os.getenv("MAX_DAILY_PROFIT", 2000))
MAX_TRADE_LOSS = float(os.getenv("MAX_TRADE_LOSS", -340))
MAX_TRADE_PROFIT = float(os.getenv("MAX_TRADE_PROFIT", 500))
CONTRACT_SYMBOL = os.getenv("CONTRACT_SYMBOL", "NQ") # e.g., NQ, MNQ, ES
TRADE_SIZE = int(os.getenv("TRADE_SIZE", 1))

# Validate essential configuration
if not TOPSTEP_USERNAME:
    logger.error("Missing required environment variable: TOPSTEP_USERNAME")
    exit(1)
if not TOPSTEP_API_KEY:
    logger.error("Missing required environment variable: TOPSTEP_API_KEY")
    exit(1)
if not ACCOUNT_ID_STR:
    logger.error("Missing required environment variable: ACCOUNT_ID")
    exit(1)

try:
    ACCOUNT_ID = int(ACCOUNT_ID_STR)
except ValueError:
    logger.error(f"Invalid ACCOUNT_ID: '{ACCOUNT_ID_STR}'. Must be an integer.")
    exit(1)

# --- Global State ---
# WARNING: In-memory state is lost on server restart. Consider Redis or DB for persistence.
session_token_info = {
    "token": None,
    "expiry": None
}
contract_details = {
    "id": None,
    "tickSize": None,
    "tickValue": None
}
trade_state = {
    "active": False,
    "signal": None, # 'long' or 'short'
    "entry_price": None, # NOTE: Will be set later via real-time fill data ideally
    "order_id": None,
    "entry_time": None,
    "current_trade_pnl": 0.0
}
daily_state = {
    "pnl": 0.0,
    "trading_allowed": True
}

# --- Pydantic Models ---
class SignalAlert(BaseModel):
    signal: str  # 'long' or 'short'
    ticker: str  # Should ideally match CONTRACT_SYMBOL
    time: str    # Example: "2024-08-15T10:30:00Z"

# --- API Client Setup ---
client = httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)

# --- Helper Functions ---

async def get_session_token():
    """Authenticates with ProjectX API and retrieves a session token."""
    global session_token_info
    logger.info("Attempting to authenticate and get session token...")
    try:
        response = await client.post(
            '/api/Auth/loginKey',
            json={
                "userName": TOPSTEP_USERNAME,
                "apiKey": TOPSTEP_API_KEY
            }
        )
        response.raise_for_status()  # Raise exception for bad status codes (4xx or 5xx)
        data = response.json()

        if data.get("success") and data.get("token"):
            session_token_info["token"] = data["token"]
            # Set expiry slightly before the actual 24 hours to be safe
            session_token_info["expiry"] = datetime.now(timezone.utc) + timedelta(hours=23, minutes=55)
            logger.info(f"Successfully obtained new session token. Expires around: {session_token_info['expiry']}")
            return session_token_info["token"]
        else:
            error_msg = data.get('errorMessage', 'Unknown authentication error')
            logger.error(f"Authentication failed: {error_msg} (ErrorCode: {data.get('errorCode')})")
            session_token_info["token"] = None
            session_token_info["expiry"] = None
            return None
    except httpx.RequestError as e:
        logger.error(f"Network error during authentication: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during authentication: {e}")
        return None

async def _get_valid_token():
    """Gets the current token, refreshing if necessary."""
    if session_token_info["token"] and session_token_info["expiry"] > datetime.now(timezone.utc):
        return session_token_info["token"]
    else:
        logger.info("Session token missing or expired. Refreshing...")
        return await get_session_token()

async def _make_api_request(method: str, endpoint: str, **kwargs):
    """Makes an authenticated API request, handling token refresh and basic error checking."""
    token = await _get_valid_token()
    if not token:
        raise HTTPException(status_code=503, detail="Could not obtain valid API session token.")

    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json", # Expect JSON response
        "Content-Type": "application/json"
    }
    if "headers" in kwargs: # Allow overriding/adding headers
        headers.update(kwargs.pop("headers"))

    try:
        response = await client.request(method, endpoint, headers=headers, **kwargs)
        response.raise_for_status()
        data = response.json()

        # Basic check for ProjectX API success indicator
        if not data.get("success"):
            error_msg = data.get('errorMessage', f'API call to {endpoint} failed')
            error_code = data.get('errorCode', 'N/A')
            logger.error(f"API Error ({endpoint}): {error_msg} (Code: {error_code}) Request: {kwargs.get('json')}")
            # Decide if this should be an HTTP exception or handled differently
            raise HTTPException(status_code=400, detail=f"API Error: {error_msg} (Code: {error_code})")

        return data # Return the successful response data

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error calling {e.request.url}: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"API HTTP Error: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Network error calling {e.request.url}: {e}")
        raise HTTPException(status_code=503, detail=f"API Network Error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error during API request to {endpoint}: {e}") # Log full traceback
        raise HTTPException(status_code=500, detail=f"Unexpected API Request Error: {e}")


async def fetch_contract_details(symbol: str):
    """Fetches contract details like ID, tick size, and value."""
    global contract_details
    logger.info(f"Fetching contract details for symbol: {symbol}")
    try:
        data = await _make_api_request(
            "POST",
            "/api/Contract/search",
            json={
                "searchText": symbol,
                "live": False  # Set to True if using live data subscription
            }
        )
        contracts = data.get("contracts", [])
        if not contracts:
            logger.error(f"No contract found for symbol: {symbol}")
            return False

        # Assuming the first match is the desired one, add more logic if needed
        # e.g., filter by description or activeContract status
        found_contract = None
        for c in contracts:
            # Basic check if symbol is in name (e.g. "MNQ..." for "NQ")
            # A more robust check might be needed depending on naming conventions
            if symbol in c.get("name", ""):
                 # Prioritize active contracts
                 if c.get("activeContract"):
                    found_contract = c
                    break
                 # Fallback to inactive if no active found yet
                 if not found_contract:
                    found_contract = c

        if not found_contract:
            logger.error(f"Could not find a suitable contract matching symbol {symbol} in results: {contracts}")
            return False

        contract_details["id"] = found_contract.get("id")
        contract_details["tickSize"] = found_contract.get("tickSize")
        contract_details["tickValue"] = found_contract.get("tickValue")

        if not all([contract_details["id"], contract_details["tickSize"], contract_details["tickValue"]]):
            logger.error(f"Incomplete contract details received for {symbol}: {contract_details}")
            return False

        logger.info(f"Contract details found: ID={contract_details['id']}, TickSize={contract_details['tickSize']}, TickValue={contract_details['tickValue']}")
        return True

    except Exception as e:
        logger.error(f"Failed to fetch contract details for {symbol}: {e}")
        return False

async def place_order(signal_direction: str):
    """Places a market order based on the signal."""
    if not contract_details["id"]:
        logger.error("Cannot place order: Contract ID not available.")
        raise HTTPException(status_code=500, detail="Contract details not loaded.")

    side_int = 0 if signal_direction == "long" else 1 # 0 = Bid (buy), 1 = Ask (sell)
    order_type = 2 # 2 = Market

    payload = {
        "accountId": ACCOUNT_ID,
        "contractId": contract_details["id"],
        "type": order_type,
        "side": side_int,
        "size": TRADE_SIZE
        # Market orders don't need limitPrice, stopPrice, trailPrice
    }
    logger.info(f"Placing {signal_direction} market order: {payload}")

    try:
        response_data = await _make_api_request("POST", "/api/Order/place", json=payload)
        order_id = response_data.get("orderId")
        if order_id:
            logger.info(f"Order placed successfully. Order ID: {order_id}")
            return order_id
        else:
            logger.error(f"Order placement API call succeeded but no orderId returned. Response: {response_data}")
            raise HTTPException(status_code=500, detail="Order placed but no Order ID received.")
    except Exception as e:
        logger.error(f"Failed to place {signal_direction} order: {e}")
        # Re-raise or handle more gracefully
        raise # Re-raise the exception caught by _make_api_request or this function

async def close_position():
    """Closes the entire position for the configured contract using the dedicated endpoint."""
    global trade_state, daily_state
    if not contract_details["id"]:
        logger.error("Cannot close position: Contract ID not available.")
        return False # Indicate failure

    if not trade_state["active"]:
        logger.warning("Close position called but no trade is active.")
        return True # Nothing to close

    logger.info(f"Attempting to close position for contract: {contract_details['id']}")
    payload = {
        "accountId": ACCOUNT_ID,
        "contractId": contract_details["id"]
        # Size is not needed for full close endpoint
    }

    try:
        await _make_api_request("POST", "/api/Position/closeContract", json=payload)
        logger.info(f"Position close request sent successfully for contract {contract_details['id']}.")

        # --- Update State ---
        # PNL calculation should happen when the fill confirmation is received
        # For now, let's assume the last calculated PNL is the final one
        final_trade_pnl = trade_state["current_trade_pnl"]
        daily_state["pnl"] += final_trade_pnl
        logger.info(f"Trade Closed. Approx PnL: {final_trade_pnl:.2f}. Daily PnL: {daily_state['pnl']:.2f}")

        # Reset trade state
        trade_state["active"] = False
        trade_state["signal"] = None
        trade_state["entry_price"] = None
        trade_state["order_id"] = None
        trade_state["entry_time"] = None
        trade_state["current_trade_pnl"] = 0.0

        # Check daily limits after closing
        check_daily_limits()

        return True # Indicate success

    except Exception as e:
        logger.error(f"Failed to send close position request: {e}")
        # Consider retry logic or manual intervention alert
        return False # Indicate failure

def check_daily_limits():
    """Checks if daily profit or loss limits have been hit."""
    global daily_state
    if not daily_state["trading_allowed"]:
        return # Already stopped

    if daily_state["pnl"] >= MAX_DAILY_PROFIT:
        logger.warning(f"Daily profit limit reached ({daily_state['pnl']:.2f} >= {MAX_DAILY_PROFIT}). Halting trading.")
        daily_state["trading_allowed"] = False
    elif daily_state["pnl"] <= MAX_DAILY_LOSS:
        logger.warning(f"Daily loss limit reached ({daily_state['pnl']:.2f} <= {MAX_DAILY_LOSS}). Halting trading.")
        daily_state["trading_allowed"] = False

def calculate_pnl(current_price: float):
    """Calculates PnL based on entry price and current price."""
    if not trade_state["active"] or trade_state["entry_price"] is None or current_price is None:
        return 0.0
    if contract_details["tickSize"] is None or contract_details["tickValue"] is None:
        logger.warning("Cannot calculate PnL: Missing tick size/value.")
        return 0.0

    price_diff = current_price - trade_state["entry_price"]
    tick_diff = price_diff / contract_details["tickSize"]

    pnl = tick_diff * contract_details["tickValue"] * TRADE_SIZE

    if trade_state["signal"] == "short":
        pnl *= -1 # Invert PnL for short trades

    return pnl

async def check_trade_pnl_and_exit(current_price: float):
    """Checks trade PnL against limits and closes position if necessary."""
    global trade_state # Allow modification

    if not trade_state["active"] or trade_state["entry_price"] is None:
        return # No active trade or entry price not yet known

    trade_pnl = calculate_pnl(current_price)
    trade_state["current_trade_pnl"] = trade_pnl # Update current PNL state
    logger.debug(f"Checking trade PNL: Current={trade_pnl:.2f}, Entry={trade_state['entry_price']}, Last={current_price}")


    # Check for trade exit conditions
    should_close = False
    reason = ""
    if trade_pnl >= MAX_TRADE_PROFIT:
        should_close = True
        reason = f"Profit target hit ({trade_pnl:.2f} >= {MAX_TRADE_PROFIT})"
    elif trade_pnl <= MAX_TRADE_LOSS:
        should_close = True
        reason = f"Stop loss hit ({trade_pnl:.2f} <= {MAX_TRADE_LOSS})"

    if should_close:
        logger.info(f"Exiting trade: {reason}")
        await close_position() # This function will update daily PNL and reset trade state


# --- FastAPI Application ---
app = FastAPI(title="Topstep Trading Bot", version="0.1.0")

@app.on_event("startup")
async def startup_event():
    """Initializes the bot on startup."""
    logger.info("Starting up trading bot...")
    # Initial authentication
    if not await _get_valid_token():
        logger.error("Failed to get initial session token. Bot may not function.")
        # Depending on requirements, might want to exit or prevent trading
    # Fetch contract details
    if not await fetch_contract_details(CONTRACT_SYMBOL):
        logger.error(f"Failed to get initial contract details for {CONTRACT_SYMBOL}. Bot may not function.")
        # Depending on requirements, might want to exit or prevent trading
    logger.info("Startup complete.")


@app.post("/webhook")
async def receive_alert(alert: SignalAlert, background_tasks: BackgroundTasks):
    """Receives trading signals from TradingView (or other source)."""
    logger.info(f"Received webhook: Signal={alert.signal}, Ticker={alert.ticker}, Time={alert.time}")

    # --- Input Validation & State Checks ---
    if alert.ticker != CONTRACT_SYMBOL:
        logger.warning(f"Ignoring signal for wrong ticker: {alert.ticker} (Expected: {CONTRACT_SYMBOL})")
        return {"status": "ignored", "reason": "wrong ticker"}

    if alert.signal not in ["long", "short"]:
        logger.warning(f"Ignoring invalid signal type: {alert.signal}")
        return {"status": "ignored", "reason": "invalid signal type"}

    if not daily_state["trading_allowed"]:
        logger.info("Trading halted due to daily limits. Ignoring signal.")
        return {"status": "halted", "reason": "daily limit reached"}

    if trade_state["active"]:
        logger.info("Trade already active. Ignoring signal.")
        return {"status": "ignored", "reason": "trade already active"}

    # --- Place Order ---
    try:
        order_id = await place_order(alert.signal)

        # Update trade state immediately (Entry price TBD)
        trade_state["active"] = True
        trade_state["signal"] = alert.signal
        trade_state["order_id"] = order_id
        trade_state["entry_time"] = datetime.now(timezone.utc) # Or use alert.time if accurate/parsed
        trade_state["entry_price"] = None # IMPORTANT: Fill price must be obtained later
        trade_state["current_trade_pnl"] = 0.0

        logger.info(f"Trade initiated: Signal={alert.signal}, OrderID={order_id}. Waiting for fill confirmation.")

        # --- !!! PLACEHOLDER / FUTURE WORK !!! ---
        # Here you would ideally start listening on SignalR for the fill confirmation
        # for this order_id to get the actual entry_price.
        # For simulation, you might start a background task to poll or just wait.
        # background_tasks.add_task(monitor_fill, order_id)

        return {"status": "trade initiated", "order_id": order_id, "message": "Waiting for fill"}

    except HTTPException as e:
        # API request failed, potentially due to token issues, network, or bad params
        logger.error(f"HTTPException during order placement: {e.detail}")
        return {"status": "error", "reason": f"Order placement failed: {e.detail}"}
    except Exception as e:
        logger.exception("Unexpected error during order placement.") # Log full traceback
        return {"status": "error", "reason": f"Unexpected error: {e}"}


@app.get("/check")
async def check_price_and_manage_trade():
    """
    --- SIMULATION ENDPOINT ---
    Periodically checks a simulated price and manages the active trade based on PnL.
    Replace this with real-time price updates and PnL checks driven by SignalR.
    """
    if not trade_state["active"]:
        return {"status": "no active trade"}

    if trade_state["entry_price"] is None:
         # --- !!! PLACEHOLDER / SIMULATION !!! ---
        # In a real scenario, you MUST get the fill price via SignalR.
        # For simulation purposes ONLY, let's pretend we got a fill price.
        # DO NOT USE THIS IN PRODUCTION.
        simulated_fill_offset = 0.25 if trade_state["signal"] == "long" else -0.25
        if contract_details["id"]: # Rough guess based on last known NQ price area
             base_price = 18000
             trade_state["entry_price"] = base_price + simulated_fill_offset
             logger.warning(f"SIMULATING entry price set to: {trade_state['entry_price']}")
        else:
             logger.warning("Cannot simulate entry price without contract details.")
             return {"status": "active", "message": "Trade active, waiting for fill (simulation blocked)"}


    # --- !!! SIMULATED PRICE !!! ---
    # Replace with actual real-time market price from SignalR feed
    price_movement = (20 / contract_details["tickSize"]) * contract_details["tickSize"] # Simulate a $20 move in correct ticks
    if trade_state["signal"] == "short":
        price_movement *= -1
    # Add some randomness maybe
    simulated_current_price = trade_state["entry_price"] + price_movement
    logger.info(f"SIMULATION: Current Price = {simulated_current_price}")

    # Check PnL and exit if limits hit
    await check_trade_pnl_and_exit(simulated_current_price)

    return {
        "status": "checked",
        "trade_active": trade_state["active"],
        "current_pnl": f"{trade_state['current_trade_pnl']:.2f}",
        "daily_pnl": f"{daily_state['pnl']:.2f}",
        "simulated_price": simulated_current_price
    }

@app.get("/status")
async def get_status():
    """Returns the current internal state of the bot."""
    return {
        "trading_allowed": daily_state["trading_allowed"],
        "daily_pnl": f"{daily_state['pnl']:.2f}",
        "trade_state": trade_state,
        "contract_details": contract_details,
        "token_expiry": session_token_info["expiry"].isoformat() if session_token_info["expiry"] else None
    }

@app.post("/force_close")
async def force_close():
    """Manually triggers a position close if a trade is active."""
    if not trade_state["active"]:
        return {"status": "ignored", "reason": "no active trade"}

    logger.warning("Force close endpoint triggered.")
    success = await close_position()
    if success:
        return {"status": "close request sent"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send close position request.")


# --- Entry Point for Uvicorn (if running directly) ---
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server directly...")
    # Ensure startup tasks run when started this way too
    # Note: Uvicorn handles startup events automatically when run via command line
    # Running programmatically might require manual trigger or different setup if needed before server starts accepting requests.
    # However, FastAPI's @app.on_event("startup") should handle this correctly.
    uvicorn.run(app, host="0.0.0.0", port=8000) # Use port 8000 for local dev typically
