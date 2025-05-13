# main.py (Simplified)
import os
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks # Keep BackgroundTasks for potential future use
from pydantic import BaseModel
from dotenv import load_dotenv
import logging
import signal # Keep for potential future graceful shutdown needs

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration ---
TOPSTEP_USERNAME = os.getenv("TOPSTEP_USERNAME")
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")
ACCOUNT_ID_STR = os.getenv("ACCOUNT_ID")
BASE_URL = "https://gateway-api.projectx.com/api/Auth/loginKey"
AUTH_URL = "https://gateway-api.projectx.com/api/Auth/loginKey"
response = await client.post(
    AUTH_URL,
    json={"key": TOPSTEP_API_KEY},  # Or correct key name
    headers={"Content-Type": "application/json"}
)

# Trading Parameters
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", -1200))
MAX_DAILY_PROFIT = float(os.getenv("MAX_DAILY_PROFIT", 2000))
# Trade exit parameters are not usable without entry/current price
# MAX_TRADE_LOSS = float(os.getenv("MAX_TRADE_LOSS", -350))
# MAX_TRADE_PROFIT = float(os.getenv("MAX_TRADE_PROFIT", 450))
CONTRACT_SYMBOL = os.getenv("CONTRACT_SYMBOL", "NQ")
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
session_token_info = {
    "token": None,
    "expiry": None
}
contract_details = {
    "id": None,
    "tickSize": None, # Still useful info, but not used for PnL here
    "tickValue": None # Still useful info, but not used for PnL here
}
# Simplified trade state: only tracks if *an* order was placed recently
trade_state = {
    "active": False, # Means "an order was sent, waiting for manual intervention or external close"
    "signal": None,
    "order_id": None,
    "entry_time": None
    # Removed entry_price, current_trade_pnl
}
# Simplified daily state: cannot track PnL accurately
daily_state = {
    # "pnl": 0.0, # Cannot track without fills/prices
    "trading_allowed": True # Can only stop based on external factors now
}

# --- Pydantic Models ---
class SignalAlert(BaseModel):
    signal: str
    ticker: str
    time: str

# --- API Client Setup ---
client = httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)

# --- Helper Functions (Authentication, API Request, Contract Details) ---
# Keep get_session_token, _get_valid_token, _make_api_request, fetch_contract_details
# These are needed for REST API interaction

async def get_session_token():
    """Authenticates with ProjectX API and retrieves a session token."""
    global session_token_info
    logger.info("Attempting to authenticate and get session token...")
    try:
        # Added explicit accept header here
        response = await client.post(
            '/api/Auth/loginKey',
            headers={"accept": "application/json", "Content-Type": "application/json"},
            json={
                "userName": TOPSTEP_USERNAME,
                "apiKey": TOPSTEP_API_KEY
            }
        )
        response.raise_for_status()
        data = response.json()

        if data.get("success") and data.get("token"):
            session_token_info["token"] = data["token"]
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
    except httpx.HTTPStatusError as e:
         logger.error(f"HTTP error during authentication: {e.response.status_code} - {e.response.text}")
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
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))

    try:
        logger.debug(f"API Request: {method} {endpoint} Payload: {kwargs.get('json')}")
        response = await client.request(method, endpoint, headers=headers, **kwargs)
        logger.debug(f"API Response Status: {response.status_code}")
        # Don't raise for status immediately, check content first for API errors
        # response.raise_for_status()
        data = response.json()
        logger.debug(f"API Response Data: {data}")

        # Check for ProjectX API success indicator OR basic HTTP success
        if response.is_success and data.get("success"):
             return data # Return the successful response data
        elif response.is_success and not data.get("success", True): # Success=False or missing
             error_msg = data.get('errorMessage', f'API call to {endpoint} failed')
             error_code = data.get('errorCode', 'N/A')
             logger.error(f"API Error ({endpoint}): {error_msg} (Code: {error_code}) Request: {kwargs.get('json')}")
             raise HTTPException(status_code=400, detail=f"API Error: {error_msg} (Code: {error_code})")
        else: # Handle HTTP errors
             logger.error(f"HTTP error calling {endpoint}: {response.status_code} - {response.text}")
             response.raise_for_status() # Raise HTTPStatusError here

    except httpx.HTTPStatusError as e:
        # Logged above or will be logged if raise_for_status is hit
        raise HTTPException(status_code=e.response.status_code, detail=f"API HTTP Error: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Network error calling {e.request.url}: {e}")
        raise HTTPException(status_code=503, detail=f"API Network Error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error during API request to {endpoint}: {e}")
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
                "live": False
            }
        )
        contracts = data.get("contracts", [])
        if not contracts:
            logger.error(f"No contract found for symbol: {symbol}")
            return False

        found_contract = None
        for c in contracts:
             if symbol in c.get("name", ""):
                 if c.get("activeContract"):
                    found_contract = c
                    break
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
            # Don't return False, some details might still be useful (like ID)
            # return False
        logger.info(f"Contract details found: ID={contract_details['id']}, TickSize={contract_details['tickSize']}, TickValue={contract_details['tickValue']}")
        return True

    except Exception as e:
        # Error already logged in _make_api_request if it was an API/HTTP error
        logger.error(f"Failed to fetch contract details for {symbol} due to exception: {e}")
        return False

async def place_order(signal_direction: str):
    """Places a market order based on the signal."""
    if not contract_details["id"]:
        logger.error("Cannot place order: Contract ID not available.")
        raise HTTPException(status_code=500, detail="Contract details not loaded.")

    side_int = 0 if signal_direction == "long" else 1
    order_type = 2 # Market

    payload = {
        "accountId": ACCOUNT_ID,
        "contractId": contract_details["id"],
        "type": order_type,
        "side": side_int,
        "size": TRADE_SIZE
    }
    logger.info(f"Placing {signal_direction} market order: {payload}")

    try:
        response_data = await _make_api_request("POST", "/api/Order/place", json=payload)
        order_id = response_data.get("orderId")
        if order_id:
            logger.info(f"Order placement request sent successfully. Order ID: {order_id}")
            return order_id
        else:
            # This case should be caught by the success check in _make_api_request
            logger.error(f"Order placement API call reported success but no orderId returned. Response: {response_data}")
            raise HTTPException(status_code=500, detail="Order placed but no Order ID received.")
    except Exception as e:
        # Error already logged in _make_api_request if it was an API/HTTP error
        logger.error(f"Order placement failed: {e}")
        # Re-raise the exception caught by _make_api_request or this function
        if isinstance(e, HTTPException):
            raise e # Keep original status code and detail
        else:
            raise HTTPException(status_code=500, detail=f"Unexpected error during order placement: {e}")


# --- FastAPI Application ---
app = FastAPI(title="Topstep Trading Bot (Webhook Only)", version="0.2.0")

@app.on_event("startup")
async def startup_event():
    """Initializes the bot on startup."""
    logger.info("Starting up trading bot (Webhook Only Mode)...")
    # Initial authentication
    if not await _get_valid_token():
        logger.error("Failed to get initial session token. Bot cannot place orders.")
    # Fetch contract details
    if not await fetch_contract_details(CONTRACT_SYMBOL):
        logger.error(f"Failed to get initial contract details for {CONTRACT_SYMBOL}. Bot cannot place orders accurately.")
    logger.info("Startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleans up resources on shutdown."""
    logger.info("Shutting down trading bot...")
    await client.aclose()
    logger.info("Shutdown complete.")

# --- FastAPI Endpoints ---

@app.post("/webhook")
async def receive_alert(alert: SignalAlert):
    """Receives trading signals and places a market order."""
    logger.info(f"Received webhook: Signal={alert.signal}, Ticker={alert.ticker}, Time={alert.time}")

    # Input Validation & State Checks
    if alert.ticker != CONTRACT_SYMBOL:
        logger.warning(f"Ignoring signal for wrong ticker: {alert.ticker} (Expected: {CONTRACT_SYMBOL})")
        return {"status": "ignored", "reason": "wrong ticker"}
    if alert.signal not in ["long", "short"]:
        logger.warning(f"Ignoring invalid signal type: {alert.signal}")
        return {"status": "ignored", "reason": "invalid signal type"}
    # Cannot check daily PnL limits accurately
    # if not daily_state["trading_allowed"]:
    #     logger.info("Trading halted. Ignoring signal.") # Need manual/external halt mechanism
    #     return {"status": "halted", "reason": "trading halted"}
    if trade_state["active"]:
        logger.info("An order was already placed based on a previous signal. Ignoring new signal.")
        # In this simplified version, "active" means an order was sent out.
        # It doesn't know if a position exists or not.
        return {"status": "ignored", "reason": "previous order sent, state not tracked"}

    # Place Order
    try:
        order_id = await place_order(alert.signal)

        # Update simplified state
        trade_state["active"] = True
        trade_state["signal"] = alert.signal
        trade_state["order_id"] = order_id
        trade_state["entry_time"] = datetime.now(timezone.utc)

        logger.info(f"Trade initiated via REST API: Signal={alert.signal}, OrderID={order_id}. Fill price/status NOT tracked by bot.")

        return {"status": "trade initiated via REST", "order_id": order_id, "message": "Order sent. Bot does not track fill price or manage position."}

    except HTTPException as e:
        logger.error(f"HTTPException during order placement: {e.detail} (Status Code: {e.status_code})")
        # Reset active state if order failed definitively
        trade_state["active"] = False
        return {"status": "error", "reason": f"Order placement failed: {e.detail}"}
    except Exception as e:
        logger.exception("Unexpected error during order placement.")
        trade_state["active"] = False
        return {"status": "error", "reason": f"Unexpected error: {e}"}


@app.get("/status")
async def get_status():
    """Returns the current simplified internal state of the bot."""
    token_status = "Valid" if session_token_info["token"] and session_token_info["expiry"] > datetime.now(timezone.utc) else "Expired/Missing"
    return {
        "trading_allowed": daily_state["trading_allowed"], # Note: Only reflects initial state, not PnL based
        "trade_state": trade_state, # Note: 'active' means order sent, not position open
        "contract_details": contract_details,
        "session_token_status": token_status,
        "session_token_expiry": session_token_info["expiry"].isoformat() if session_token_info["expiry"] else None,
        "mode": "Webhook Only (No Real-time Data or Position Management)"
    }

@app.post("/reset_trade_state")
async def reset_trade_state():
     """Manually resets the 'active' flag to allow new signals."""
     logger.warning("Manual trade state reset triggered.")
     trade_state["active"] = False
     trade_state["signal"] = None
     trade_state["order_id"] = None
     trade_state["entry_time"] = None
     return {"status": "trade state reset", "message": "Bot will now accept the next signal."}


# --- Entry Point for Uvicorn (if running directly) ---
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server directly...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
