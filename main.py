from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from datetime import datetime
import httpx
import os
import json
import csv
from signalRClient import setupSignalRConnection, closeSignalRConnection, get_event_data
import logging
import os

app = FastAPI()

# --- ENVIRONMENT CONFIG ---
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
CONTRACT_ID = os.getenv("CONTRACT_ID", "CON.F.US.EP.M25")
SESSION_TOKEN = None

# Strategy storage path
STRATEGY_PATH = "strategies.json"
TRADE_LOG_PATH = "trade_log.json"
ALERT_LOG_PATH = "alert_log.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load or initialize strategies
if os.path.exists(STRATEGY_PATH):
    with open(STRATEGY_PATH, "r") as f:
        strategies = json.load(f)
else:
    strategies = {
        "default": {
            "MAX_DAILY_LOSS": -1200,
            "MAX_DAILY_PROFIT": 2000,
            "MAX_TRADE_LOSS": -350,
            "MAX_TRADE_PROFIT": 450,
            "CONTRACT_SYMBOL": "NQ",
            "TRADE_SIZE": 1
        }
    }
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)

current_strategy_name = "default"
active_strategy_config = strategies[current_strategy_name]
daily_pnl = 0.0
trade_active = False
entry_price = None
trade_time = None
current_signal = None
current_signal_direction = None
market_data_connection = None
latest_market_data = []

# --- AUTHENTICATION ---
async def login_to_projectx():
    global SESSION_TOKEN
    logger.info("Attempting to log in to ProjectX...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.topstepx.com/api/Auth/loginKey",
                json={"userName": TOPSTEP_USERNAME, "apiKey": TOPSTEP_API_KEY},
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            SESSION_TOKEN = response.json().get("token")
            if SESSION_TOKEN:
                logger.info("ProjectX Login successful")
            else:
                logger.error("ProjectX Login failed: No token received")
        except Exception as e:
            logger.error(f"ProjectX Login failed: {e}")
            SESSION_TOKEN = None

async def ensure_token():
    if not SESSION_TOKEN:
        await login_to_projectx()

async def get_projectx_token():
    if not SESSION_TOKEN:
        await login_to_projectx()
    return SESSION_TOKEN

async def start_market_data_stream():
    global SESSION_TOKEN
    # Ensure the session token and contract ID are valid
    if not SESSION_TOKEN:
        await login_to_projectx()

    token = SESSION_TOKEN
    contract_id = os.getenv("PROJECTX_CONTRACT_ID")

    if not token:
        logger.error("❌ No valid SESSION_TOKEN found. Aborting WebSocket connection.")
        return

    if not contract_id:
        logger.error("❌ No valid PROJECTX_CONTRACT_ID found. Aborting WebSocket connection.")
        return

    logger.info("🌐 Starting WebSocket connection to market data stream...")
    try:
        setupSignalRConnection(token, contract_id)
        logger.info("✅ Market data stream started successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to start market data stream: {e}")

    # Event listeners
#   signalrEvents.on('quote', handle_quote_event)
#    signalrEvents.on('trade', handle_trade_event)
#    signalrEvents.on('depth', handle_depth_event)

# --- EVENT HANDLERS ---
def fetch_latest_quote():
    return get_event_data('quote')

def fetch_latest_trade():
    return get_event_data('trade')

def fetch_latest_depth():
    return get_event_data('depth')

def handle_quote_event(data):
    print(f"[Quote Event] Received Data: {data}")

def handle_trade_event(data):
    print(f"[Trade Event] Received Data: {data}")

def handle_depth_event(data):
    print(f"[Depth Event] Received Data: {data}")

async def stop_market_data_stream():
    global market_data_connection
    if market_data_connection:
        print("🔌 Stopping market data stream...")
        closeSignalRConnection()
        market_data_connection = None

async def startup_event():
    logger.info("Attempting to log in to ProjectX...")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.topstepx.com/api/Auth/loginKey",
                json={"userName": TOPSTEP_USERNAME, "apiKey": TOPSTEP_API_KEY},
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            result = response.json()
            token = result.get("token")

            if not token:
                logger.error("No token returned from login response.")
                raise RuntimeError("ProjectX login failed — no token returned.")

            logger.info("ProjectX Login successful")

            # Start the market data stream
            setupSignalRConnection(token, CONTRACT_ID)

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during login: {e}")
        raise

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise
        
@app.on_event("startup")
async def startup_wrapper():
    await startup_event()
    await start_market_data_stream(token, CONTRACT_ID)

@app.on_event("shutdown")
async def shutdown_event():
    await stop_market_data_stream()

@app.get("/status")
async def status():
    return {
        "latest_quote": fetch_latest_quote(),
        "latest_trade": fetch_latest_trade(),
        "latest_depth": fetch_latest_depth()
    }

# --- DATA MODEL ---
class SignalAlert(BaseModel):
    signal: str
    ticker: str
    time: str

class StatusResponse(BaseModel):
    trade_active: bool
    entry_price: float | None
    trade_time: str | None
    current_signal: str | None
    daily_pnl: float

# --- COMMON CSS ---
COMMON_CSS = """
<style>
    body {
        background-color: #131722; color: #D1D4DC;
        font-family: 'Roboto', -apple-system, BlinkMacSystemFont, 'Segoe UI', Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
        margin: 0; padding: 20px; font-size: 14px; line-height: 1.6;
    }
    .container {
        max-width: 900px; margin: auto; background-color: #1E222D;
        padding: 20px; border-radius: 8px; box-shadow: 0 0 15px rgba(0,0,0,0.2);
    }
    h1, h2 {
        color: #FFFFFF; border-bottom: 1px solid #434651;
        padding-bottom: 10px; margin-top: 25px; margin-bottom: 15px;
    }
    h1 { font-size: 28px; } h2 { font-size: 22px; }
    a { color: #58A6FF; text-decoration: none; }
    a:hover { text-decoration: underline; }
    form { background-color: #2A2E39; padding: 20px; border-radius: 6px; margin-bottom: 30px; }
    label { display: block; margin-bottom: 8px; color: #B2B5BE; font-weight: 500; }
    input[type="text"], input[type="number"], select {
        width: calc(100% - 22px); padding: 10px; margin-bottom: 15px;
        background-color: #131722; color: #D1D4DC;
        border: 1px solid #434651; border-radius: 4px; box-sizing: border-box;
    }
    button[type="submit"], .button-link {
        background-color: #2962FF; color: white; padding: 10px 20px;
        border: none; border-radius: 4px; cursor: pointer; font-size: 15px;
        font-weight: 500; text-align: center; display: inline-block; margin-top: 10px;
    }
    button[type="submit"]:hover, .button-link:hover { background-color: #1E88E5; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
    th, td { border: 1px solid #434651; padding: 10px; text-align: left; font-size:13px;}
    th { background-color: #2A2E39; color: #FFFFFF; font-weight: 600; }
    tr:nth-child(even) { background-color: #252832; } /* Subtle striping */
    hr { border: none; border-top: 1px solid #434651; margin: 30px 0; }
    p strong { color: #E0E0E0; }
    ul.menu-list { list-style-type: none; padding: 0; }
    ul.menu-list li { background-color: #2A2E39; margin-bottom: 10px; padding: 15px; border-radius: 4px;}
    ul.menu-list li a { font-size: 16px; display: block; }
    .status-section p { background-color: #2A2E39; padding: 10px; border-radius: 4px; margin-bottom: 8px; }
    .delete-button { background-color: #D32F2F !important; }
    .delete-button:hover { background-color: #C62828 !important; }
    .clone-button { background-color: #00796B !important; }
    .clone-button:hover { background-color: #004D40 !important; }
    pre { background-color: #2A2E39; padding: 15px; border-radius: 4px; color: #D1D4DC; overflow-x: auto; }
</style>
"""

# --- HELPER FUNCTIONS ---
def save_strategies():
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)

def log_event(file_path, event_data):
    if not os.path.exists(file_path):
        with open(file_path, "w") as f:
            json.dump([], f) # Create empty list if file doesn't exist
    
    with open(file_path, "r+") as f:
        try:
            log_list = json.load(f)
        except json.JSONDecodeError:
            log_list = []
        log_list.append({**{"timestamp": datetime.utcnow().isoformat()}, **event_data})
        f.seek(0)
        json.dump(log_list, f, indent=2)
        f.truncate()

async def place_order(direction: str):
    await ensure_token()
    side = "buy" if direction == "long" else "sell"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://gateway-api.projectx.com/api/orders",
            headers={"Authorization": f"Bearer {SESSION_TOKEN}"},
            json={
                "accountId": ACCOUNT_ID,
                "symbol": config["CONTRACT_SYMBOL"],
                "qty": config["TRADE_SIZE"],
                "side": side,
                "type": "market"
            }
        )
        response.raise_for_status()
        return response.json()

async def check_and_close_trade(current_price: float):
    global trade_active, entry_price, daily_pnl, current_signal
    if not trade_active or entry_price is None:
        return

    pnl = (current_price - entry_price) * (1 if current_signal == 'long' else -1) * 20
    if pnl >= config["MAX_TRADE_PROFIT"] or pnl <= config["MAX_TRADE_LOSS"]:
        await close_position()
        daily_pnl += pnl
        trade_active = False
        print(f"Trade exited at {current_price}, PnL: {pnl}")

    if daily_pnl >= config["MAX_DAILY_PROFIT"] or daily_pnl <= config["MAX_DAILY_LOSS"]:
        trade_active = False
        print("Trading halted for the day")

async def close_position():
    await ensure_token()
    side = "sell" if current_signal == "long" else "buy"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://gateway-api.projectx.com/api/orders",
            headers={"Authorization": f"Bearer {SESSION_TOKEN}"},
            json={
                "accountId": ACCOUNT_ID,
                "symbol": config["CONTRACT_SYMBOL"],
                "qty": config["TRADE_SIZE"],
                "side": side,
                "type": "market"
            }
        )
        response.raise_for_status()
        return response.json()

# --- Start SignalR WebSocket connection ---
def start_market_data_stream(token, contract_id):
    global market_data_connection
    if market_data_connection:
        print("🔁 WebSocket already connected. Skipping restart.")
        return

    hub_url = f"https://rtc.topstepx.com/hubs/market?access_token={token}"
    try:
        connection = HubConnectionBuilder()\
            .with_url(hub_url)\
            .with_automatic_reconnect({"keep_alive_interval": 10, "reconnect_interval": 5})\
            .build()

        def handle_trade(args):
            print("TRADE:", args)
            if args:
                latest_market_data.append({"type": "trade", "data": args})
                if len(latest_market_data) > 50:
                    latest_market_data.pop(0)

        def handle_quote(args):
            print("QUOTE:", args)
            if args:
                latest_market_data.append({"type": "quote", "data": args})
                if len(latest_market_data) > 50:
                    latest_market_data.pop(0)

        connection.on("GatewayTrade", handle_trade)
        connection.on("GatewayQuote", handle_quote)
        connection.start()
        connection.send("SubscribeContractTrades", [contract_id])
        connection.send("SubscribeContractQuotes", [contract_id])
        market_data_connection = connection
        print("✅ WebSocket connection established and subscribed")
    except Exception as e:
        print("❌ WebSocket connection failed:", e)
        
# Call on startup
@app.on_event("startup")
async def on_startup():
    await get_projectx_token()
    if SESSION_TOKEN:
        start_market_data_stream(SESSION_TOKEN, "CON.F.US.EP.M25")  # Replace with valid contract ID
    else:
        print("⚠️ Cannot start WebSocket: SESSION_TOKEN is missing")
        
async def projectx_api_request(method: str, endpoint: str, payload: dict = None):
    token = await get_projectx_token()
    if not token:
        raise Exception("Not authenticated with ProjectX")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"} # text/plain was in docs, but json is more common
    
    async with httpx.AsyncClient() as client:
        if method.upper() == "POST":
            response = await client.post(f"{PROJECTX_BASE_URL}{endpoint}", json=payload, headers=headers)
        elif method.upper() == "GET": # Example if you add GET requests
            response = await client.get(f"{PROJECTX_BASE_URL}{endpoint}", headers=headers)
        else:
            raise ValueError("Unsupported HTTP method")

        if response.status_code == 401: # Token expired or invalid
            print("ProjectX token expired or invalid, attempting re-login.")
            projectx_session_token = None # Clear old token
            token = await get_projectx_token() # Get new token
            if not token:
                raise Exception("Re-authentication with ProjectX failed")
            headers["Authorization"] = f"Bearer {token}" # Update headers
            # Retry the original request
            if method.upper() == "POST":
                response = await client.post(f"{PROJECTX_BASE_URL}{endpoint}", json=payload, headers=headers)
            # Add retry for GET if needed
        
        response.raise_for_status() # Raise HTTPError for bad responses (4XX or 5XX)
        return response.json()

async def place_order_projectx(direction: str, strategy_cfg: dict):
    # Map signal to ProjectX API values
    side_value = 0 if direction == "long" else 1 # 0 for Bid (buy), 1 for Ask (sell)
    order_type_value = 2 # Market Order

    if "PROJECTX_CONTRACT_ID" not in strategy_cfg:
        # Fallback or dynamic fetch needed here. For now, error.
        # Ideally, fetch this when strategy is loaded/selected if not present.
        # contract_details = await projectx_api_request("POST", "/Contract/search", {"searchText": strategy_cfg["CONTRACT_SYMBOL"], "live": False})
        # if contract_details and contract_details.get("contracts"):
        #     strategy_cfg["PROJECTX_CONTRACT_ID"] = contract_details["contracts"][0]["id"] # Simplistic: takes first match
        # else:
        raise ValueError(f"PROJECTX_CONTRACT_ID not found for symbol {strategy_cfg['CONTRACT_SYMBOL']}")
    
    payload = {
        "accountId": int(ACCOUNT_ID), # Ensure it's an integer
        "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"],
        "type": order_type_value,
        "side": side_value,
        "size": strategy_cfg["TRADE_SIZE"]
        # limitPrice, stopPrice etc. would go here if not a market order
    }
    log_event(ALERT_LOG_PATH, {"event": "Placing Order", "strategy": current_strategy_name, "payload": payload})
    return await projectx_api_request("POST", "/Order/place", payload=payload)


async def close_position_projectx(strategy_cfg: dict, current_active_signal: str):
    # Closing a position is effectively placing an opposing market order
    # OR using a specific "close position" endpoint if available and preferred.
    # For simplicity with market orders, we place an opposite order.
    # A more robust way would be to use /api/Position/closeContract
    
    # This example uses the "place opposite order" method:
    # opposite_direction = "short" if current_active_signal == "long" else "long"
    # return await place_order_projectx(opposite_direction, strategy_cfg)

    # Using the documented /api/Position/closeContract (preferred)
    if "PROJECTX_CONTRACT_ID" not in strategy_cfg:
         raise ValueError(f"PROJECTX_CONTRACT_ID not found for symbol {strategy_cfg['CONTRACT_SYMBOL']}")

    payload = {
        "accountId": int(ACCOUNT_ID),
        "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"]
    }
    log_event(ALERT_LOG_PATH, {"event": "Closing Position", "strategy": current_strategy_name, "payload": payload})
    return await projectx_api_request("POST", "/Position/closeContract", payload=payload)


async def fetch_current_price(contract_id: str):
    # Placeholder - This needs to be a real price feed
    # Option 1: Poll /api/History/retrieveBars (less ideal for frequent checks)
    # try:
    #     data = await projectx_api_request("POST", "/History/retrieveBars", {
    #         "contractId": contract_id,
    #         "live": False, # or True for live
    #         "startTime": (datetime.utcnow() - timedelta(minutes=5)).isoformat() + "Z",
    #         "endTime": datetime.utcnow().isoformat() + "Z",
    #         "unit": 2, # Minute
    #         "unitNumber": 1,
    #         "limit": 1,
    #         "includePartialBar": True
    #     })
    #     if data.get("bars") and len(data["bars"]) > 0:
    #         return data["bars"][0]["c"] # Closing price of the last bar
    # except Exception as e:
    #     print(f"Error fetching current price: {e}")
    # return None # Fallback

    # For now, we'll keep the simulation for the /check endpoint as ProjectX real-time market data is not yet integrated.
    global entry_price
    if entry_price:
        # Simulate some price movement for testing check_and_close_trade
        import random
        simulated_move = random.uniform(-5, 5) # Simulate a move of +/- 5 points
        return entry_price + simulated_move
    return None


async def check_and_close_active_trade(strategy_name_of_trade: str):
    global trade_active, entry_price, daily_pnl, current_signal_direction, current_trade_id
    
    if not trade_active or entry_price is None:
        return

    # Get the config for the strategy that PLACED the trade
    trade_strategy_cfg = strategies.get(strategy_name_of_trade)
    if not trade_strategy_cfg:
        print(f"Error: Strategy config for '{strategy_name_of_trade}' (active trade) not found.")
        return

    # TODO: THIS IS WHERE YOU NEED TO GET THE REAL CURRENT PRICE FOR trade_strategy_cfg["PROJECTX_CONTRACT_ID"]
    # current_market_price = await fetch_current_price(trade_strategy_cfg["PROJECTX_CONTRACT_ID"])
    # For now, using placeholder from global state that /check updates for demo
    current_market_price = await fetch_current_price(trade_strategy_cfg.get("PROJECTX_CONTRACT_ID", "N/A")) # Get current price


    if current_market_price is None:
        print("Could not fetch current market price to check trade.")
        return

    # PnL Calculation (assuming 1 point = $20 for NQ, and direct price diff for points)
    # This should be made dynamic based on contract's tickSize and tickValue
    points_pnl = (current_market_price - entry_price) * (1 if current_signal_direction == 'long' else -1)
    dollar_pnl_per_contract = points_pnl * 20 # NQ specific, $20 per point
    total_dollar_pnl = dollar_pnl_per_contract * trade_strategy_cfg["TRADE_SIZE"]

    exit_reason = None
    if total_dollar_pnl >= trade_strategy_cfg["MAX_TRADE_PROFIT"]:
        exit_reason = "Max Trade Profit Hit"
    elif total_dollar_pnl <= trade_strategy_cfg["MAX_TRADE_LOSS"]:
        exit_reason = "Max Trade Loss Hit"

    if exit_reason:
        print(f"[{strategy_name_of_trade}] Attempting to close trade. Reason: {exit_reason}. PnL: ${total_dollar_pnl:.2f}")
        try:
            await close_position_projectx(trade_strategy_cfg, current_signal_direction)
            log_event(TRADE_LOG_PATH, {
            "event": "entry", "strategy": strategy_webhook_name, "signal": alert.signal, 
            "ticker": alert.ticker, "entry_price_estimate": entry_price,
            "projectx_order_id": current_trade_id
            })
            daily_pnl += total_dollar_pnl # This daily_pnl should be per-strategy in a more advanced setup
            trade_active = False
            entry_price = None
            current_signal_direction = None
            trade_time = None
            current_trade_id = None
            print(f"Trade exited at {current_market_price}, Actual PnL: {total_dollar_pnl:.2f}")
        except Exception as e:
            print(f"Error closing position: {e}")
            log_event(ALERT_LOG_PATH, {"event": "Error Closing Position", "strategy": strategy_name_of_trade, "error": str(e)})


    # Check daily PnL limits (this global daily_pnl needs to be strategy-specific too)
    if daily_pnl >= active_strategy_config["MAX_DAILY_PROFIT"] or daily_pnl <= active_strategy_config["MAX_DAILY_LOSS"]:
        if trade_active: # If a trade is still active when daily limit hit
            print(f"Daily PnL limit hit (${daily_pnl:.2f}). Forcing close of active trade for strategy {strategy_name_of_trade}.")
            try:
                await close_position_projectx(trade_strategy_cfg, current_signal_direction)
                # Recalculate PNL for this forced exit
                final_points_pnl = (current_market_price - entry_price) * (1 if current_signal_direction == 'long' else -1)
                final_dollar_pnl = final_points_pnl * 20 * trade_strategy_cfg["TRADE_SIZE"]
                log_event(TRADE_LOG_PATH, {
                    "event": "exit_forced_daily_limit", "strategy": strategy_name_of_trade, "signal": current_signal_direction,
                    "entry_price": entry_price, "exit_price": current_market_price, "pnl": final_dollar_pnl,
                    "reason": "Daily PnL Limit Hit", "projectx_order_id": current_trade_id
                })
                daily_pnl += (final_dollar_pnl - total_dollar_pnl) # Adjust daily PnL with the actual PnL of this forced close
            except Exception as e:
                 print(f"Error force-closing position for daily limit: {e}")
        trade_active = False # Halt further trading for this strategy today
        print(f"Trading halted for the day for strategy {current_strategy_name} due to PnL limits.")
        # Note: This halts based on current_strategy_name's config, but daily_pnl is global. Needs refinement.

# --- MAIN ENDPOINTS ---
@app.post("/webhook/{strategy_webhook_name}")
async def receive_alert_strategy(strategy_webhook_name: str, alert: SignalAlert):
    global trade_active, entry_price, current_signal_direction, trade_time, daily_pnl, active_strategy_config, current_trade_id
    
    log_event(ALERT_LOG_PATH, {"event": "Webhook Received", "strategy": strategy_webhook_name, "signal": alert.signal, "ticker": alert.ticker})

    if strategy_webhook_name not in strategies:
        return {"status": "error", "reason": f"Strategy '{strategy_webhook_name}' not found"}

    # Load the specific strategy config for this webhook
    strategy_cfg_for_trade = strategies[strategy_webhook_name]
    
    # TODO: Daily PNL should be tracked per strategy. This is a simplification.
    if daily_pnl >= strategy_cfg_for_trade["MAX_DAILY_PROFIT"] or daily_pnl <= strategy_cfg_for_trade["MAX_DAILY_LOSS"]:
        log_event(ALERT_LOG_PATH, {"event": "Trading Halted (Daily Limit)", "strategy": strategy_webhook_name})
        return {"status": "halted", "reason": f"daily limit reached for strategy {strategy_webhook_name}"}

    if trade_active: # Global trade_active; means bot is in *any* trade.
        log_event(ALERT_LOG_PATH, {"event": "Trade Ignored (Already Active)", "strategy": strategy_webhook_name})
        return {"status": "ignored", "reason": "trade already active (globally)"}

    print(f"[{strategy_webhook_name}] Received {alert.signal} signal for {alert.ticker} at {alert.time}")

    try:
        # Place the order using the specific strategy's config
        result = await place_order_projectx(alert.signal, strategy_cfg_for_trade)
        
        if result.get("success") and result.get("orderId"):
            current_trade_id = result["orderId"]
            # TODO: Need to fetch actual fillPrice. This is a placeholder.
            # For now, assume immediate fill at some simulated price or known level.
            # This part is CRITICAL and needs to be replaced with real fill price logic.
            # Let's assume we can't get fill price immediately.
            # We'd need to poll or use websockets. For now, we'll simulate:
            simulated_entry_price = 20900.00 # Placeholder - GET REAL PRICE
            
            # Fetch current market price to use as entry for now
            # This is still not ideal as it's not the fill price
            # contract_id_for_price = strategy_cfg_for_trade.get("PROJECTX_CONTRACT_ID", "N/A")
            # fetched_price_as_entry = await fetch_current_price(contract_id_for_price)
            # entry_price = fetched_price_as_entry if fetched_price_as_entry is not None else simulated_entry_price
            
            entry_price = simulated_entry_price # MAJOR TODO: Replace with actual fill price logic

            trade_active = True
            current_signal_direction = alert.signal
            trade_time = datetime.utcnow()
            # Store which strategy initiated this trade
            global strategy_that_opened_trade 
            strategy_that_opened_trade = strategy_webhook_name

            log_event(TRADE_LOG_PATH, {
                "event": "entry", "strategy": strategy_webhook_name, "signal": alert.signal, 
                "ticker": alert.ticker, "entry_price_estimate": entry_price, # Mark as estimate
                "projectx_order_id": current_trade_id
            })
            return {"status": "trade placed (simulated entry)", "projectx_order_id": current_trade_id, "entry_price_estimate": entry_price}
        else:
            error_msg = result.get("errorMessage", "Unknown error placing order.")
            log_event(ALERT_LOG_PATH, {"event": "Order Placement Failed", "strategy": strategy_webhook_name, "error": error_msg, "response": result})
            return {"status": "error", "reason": f"Failed to place order: {error_msg}"}

    except Exception as e:
        log_event(ALERT_LOG_PATH, {"event": "Error Processing Webhook", "strategy": strategy_webhook_name, "error": str(e)})
        return {"status": "error", "detail": str(e)}

strategy_that_opened_trade = None # Global to track which strategy opened the current trade

@app.get("/check_trade_status") # Renamed from /check
async def check_trade_status_endpoint():
    global strategy_that_opened_trade
    if not trade_active:
        return {"status": "no active trade"}
    if not strategy_that_opened_trade:
        return {"status": "error", "reason": "Trade active but opening strategy unknown."}

    # This is where you'd implement the actual price fetching and PnL check
    # For demonstration, we call check_and_close_active_trade
    # In a real scenario, this might be triggered by a scheduler or an internal loop.
    # And it MUST use real market data.
    await check_and_close_active_trade(strategy_that_opened_trade)
    
    if trade_active : # If still active after check
        return {"status": "checked_still_active", "entry_price": entry_price, "signal": current_signal_direction}
    else:
        return {"status": "checked_trade_closed_or_halted"}

@app.get("/live_feed", response_class=HTMLResponse)
async def live_feed_page():
    rows = "".join([
        f"<tr><td>{item['type']}</td><td>{json.dumps(item['data'])}</td></tr>"
        for item in reversed(latest_market_data[-50:])
    ])
    return HTMLResponse(f"""
    <html><head><title>Live Market Feed</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Live Market Feed (latest 50 events)</h1>
    <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    <table><thead><tr><th>Type</th><th>Data</th></tr></thead><tbody>{rows}</tbody></table>
    </div></body></html>""")

@app.get("/status", response_model=StatusResponse)
async def get_status_endpoint(): # Renamed
    return StatusResponse(
        trade_active=trade_active,
        entry_price=entry_price,
        trade_time=trade_time.isoformat() if trade_time else None,
        current_signal_direction=current_signal_direction,
        daily_pnl=daily_pnl,
        current_strategy_name=current_strategy_name, # The one selected in UI
        active_strategy_config=strategies.get(current_strategy_name, {}) # Config displayed in UI
    )

@app.get("/", response_class=HTMLResponse)
async def config_dashboard_with_selection_get(strategy_selected: str = None):
    global current_strategy_name
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    # active_strategy_config is updated by the dashboard_page if current_strategy_name changes
    return await config_dashboard_page()

@app.head("/", response_class=HTMLResponse)
async def config_dashboard_with_selection_head(strategy_selected: str = None):
    global current_strategy_name
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    # FastAPI handles sending only headers for HEAD requests when the GET handler returns a Response
    return await config_dashboard_page()

# Make sure your config_dashboard_page function is defined correctly
async def config_dashboard_page(): # This is the function that generates the actual HTML
    global current_strategy_name # This global is important for it to pick up the selection
    strategy_options_html = "".join([f'<option value="{name}" {"selected" if name == current_strategy_name else ""}>{name}</option>' for name in strategies])
    
    strategy_rows_html = ""
    for name in strategies:
        delete_action = f"<a href='/delete_strategy_action?strategy_to_delete={name}' class='button-link delete-button' style='margin-right: 5px;'>Delete</a>" if name != "default" else "<span>N/A (Default)</span>"
        clone_action = f"<a href='/clone_strategy_action?strategy_to_clone={name}' class='button-link clone-button'>Clone</a>"
        strategy_rows_html += f"<tr><td>{name}</td><td>{delete_action} {clone_action}</td></tr>"

    displayed_config = strategies.get(current_strategy_name, strategies.get("default", {}))

    # Using the global runtime state variables for the status section
    entry_price_display = entry_price if entry_price is not None else "None"
    trade_time_display = trade_time.isoformat() if trade_time else "None"
    current_signal_display = current_signal_direction if current_signal_direction else "None"
    daily_pnl_display = f"{daily_pnl:.2f}"
    strategy_opened_trade_display = strategy_that_opened_trade if strategy_that_opened_trade else "N/A"


    html_content = f"""
    <html><head><title>Trading Bot Dashboard</title>{COMMON_CSS}
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    </head><body><div class="container">
        <h1>Trading Bot Configuration</h1>
        <form action="/update_strategy_config" method="post">
            <label for="strategy_select">Displaying/Editing Strategy:</label>
            <select name="strategy_to_display" id="strategy_select">{strategy_options_html}</select>
            
            <h2>Configure Strategy: '{current_strategy_name}'</h2>
            <input type="hidden" name="strategy_name_to_update" value="{current_strategy_name}">
            <label for="symbol">Contract Symbol (e.g., NQ):</label>
            <input name="contract_symbol" id="symbol" value="{displayed_config.get('CONTRACT_SYMBOL', '')}" />
            <label for="projectx_contract_id">ProjectX Contract ID (e.g., CON.F.US.MNQ.H25):</label>
            <input name="projectx_contract_id" id="projectx_contract_id" value="{displayed_config.get('PROJECTX_CONTRACT_ID', '')}" />
            <label for="size">Trade Size:</label>
            <input name="trade_size" id="size" type="number" value="{displayed_config.get('TRADE_SIZE', 1)}" />
            <label for="max_trade_loss">Max Trade Loss ($):</label>
            <input name="max_trade_loss" id="max_trade_loss" type="number" step="any" value="{displayed_config.get('MAX_TRADE_LOSS', -350.0)}" />
            <label for="max_trade_profit">Max Trade Profit ($):</label>
            <input name="max_trade_profit" id="max_trade_profit" type="number" step="any" value="{displayed_config.get('MAX_TRADE_PROFIT', 450.0)}" />
            <label for="max_daily_loss">Max Daily Loss ($):</label>
            <input name="max_daily_loss" id="max_daily_loss" type="number" step="any" value="{displayed_config.get('MAX_DAILY_LOSS', -1200.0)}" />
            <label for="max_daily_profit">Max Daily Profit ($):</label>
            <input name="max_daily_profit" id="max_daily_profit" type="number" step="any" value="{displayed_config.get('MAX_DAILY_PROFIT', 2000.0)}" />
            <button type="submit">Update Config for '{current_strategy_name}'</button>
        </form>

        <h2>Available Strategies</h2>
        <table><thead><tr><th>Name</th><th>Actions</th></tr></thead><tbody>{strategy_rows_html}</tbody></table>
        <form action="/create_new_strategy_action" method="post" style="margin-top: 20px;">
            <label for="new_strategy_name_input">New Strategy Name:</label>
            <input name="new_strategy_name_input" id="new_strategy_name_input" type="text" placeholder="Enter new strategy name" required />
            <button type="submit">Create New Strategy (from Default)</button>
        </form><hr>

        <h2>Live Bot Status</h2>
        <div class="status-section" id="status-container">
            <p><strong>Strategy in UI:</strong> {current_strategy_name}</p>
            <p><strong>Globally Active Trade:</strong> {trade_active}</p>
            <p><strong>Trade Opened By Strategy:</strong> {strategy_opened_trade_display}</p>
            <p><strong>Entry Price:</strong> {entry_price_display}</p>
            <p><strong>Trade Time (UTC):</strong> {trade_time_display}</p>
            <p><strong>Current Signal Direction:</strong> {current_signal_display}</p>
            <p><strong>Global Daily PnL:</strong> {daily_pnl_display}</p>
            <p><a href="/check_trade_status" class="button-link">Manually Check Active Trade</a></p>
        </div>
        <p><a href="/dashboard_menu_page" class="button-link">Go to Main Menu</a></p>
    </div>
    <script>
        document.getElementById('strategy_select').addEventListener('change', function() {{
            window.location.href = '/?strategy_selected=' + this.value;
        }});
    </script>
    </body></html>"""
    return HTMLResponse(content=html_content)

@app.get("/") # Handles both root and strategy selection for display
async def config_dashboard_with_selection(strategy_selected: str = None):
    global current_strategy_name, active_strategy_config
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
        active_strategy_config = strategies[current_strategy_name]
    # If no strategy_selected or invalid, it defaults to the global current_strategy_name
    return await config_dashboard_page()


@app.post("/update_strategy_config") # Renamed
async def update_strategy_config_action( # Renamed
    strategy_name_to_update: str = Form(...),
    contract_symbol: str = Form(...),
    projectx_contract_id: str = Form(...),
    trade_size: int = Form(...),
    max_trade_loss: float = Form(...),
    max_trade_profit: float = Form(...),
    max_daily_loss: float = Form(...),
    max_daily_profit: float = Form(...)
):
    global active_strategy_config, current_strategy_name
    if strategy_name_to_update in strategies:
        strategies[strategy_name_to_update].update({
            "CONTRACT_SYMBOL": contract_symbol,
            "PROJECTX_CONTRACT_ID": projectx_contract_id,
            "TRADE_SIZE": trade_size,
            "MAX_TRADE_LOSS": max_trade_loss,
            "MAX_TRADE_PROFIT": max_trade_profit,
            "MAX_DAILY_LOSS": max_daily_loss,
            "MAX_DAILY_PROFIT": max_daily_profit
        })
        save_strategies_to_file()
        current_strategy_name = strategy_name_to_update # Keep UI on the edited strategy
        active_strategy_config = strategies[current_strategy_name]
    return RedirectResponse(url=f"/?strategy_selected={strategy_name_to_update}", status_code=303)

@app.post("/create_new_strategy_action") # Renamed
async def create_new_strategy_action_endpoint(new_strategy_name_input: str = Form(...)): # Renamed
    global current_strategy_name, active_strategy_config
    if new_strategy_name_input and new_strategy_name_input.strip() and new_strategy_name_input not in strategies:
        strategies[new_strategy_name_input.strip()] = dict(strategies["default"]) # Copy from default
        save_strategies_to_file()
        current_strategy_name = new_strategy_name_input.strip()
        active_strategy_config = strategies[current_strategy_name]
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303) # Or show an error

@app.get("/delete_strategy_action") # Renamed
async def delete_strategy_action_endpoint(strategy_to_delete: str): # Renamed
    global current_strategy_name, active_strategy_config
    if strategy_to_delete in strategies and strategy_to_delete != "default":
        del strategies[strategy_to_delete]
        save_strategies_to_file()
        if current_strategy_name == strategy_to_delete: # If deleting the current one
            current_strategy_name = "default"
            active_strategy_config = strategies[current_strategy_name]
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303) # Or show an error

@app.get("/clone_strategy_action") # Renamed
async def clone_strategy_action_endpoint(strategy_to_clone: str): # Renamed
    global current_strategy_name, active_strategy_config
    if strategy_to_clone in strategies:
        new_name_base = f"{strategy_to_clone}_copy"
        new_name = new_name_base
        count = 1
        while new_name in strategies:
            new_name = f"{new_name_base}{count}"
            count += 1
        strategies[new_name] = dict(strategies[strategy_to_clone])
        save_strategies_to_file()
        current_strategy_name = new_name # Switch UI to the new clone
        active_strategy_config = strategies[current_strategy_name]
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)
    
@app.get("/dashboard_menu_page", response_class=HTMLResponse) # Renamed
async def dashboard_menu_html_page(): # Renamed
    return HTMLResponse(f"""
    <html><head><title>Bot Dashboard Menu</title>{COMMON_CSS}
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    </head><body><div class="container">
        <h1>Topstep Bot Dashboard</h1>
        <ul class="menu-list">
            <li><a href="/">Configuration & Status</a></li>
            <li><a href="/logs/trades">Trade History</a></li>
            <li><a href="/logs/alerts">Alert Log</a></li>
            <li><a href="/toggle_trading_status">Toggle Trading (View Status)</a></li>
        </ul>
    </div></body></html>""")

# Placeholder for trading toggle status (not fully implemented functionality)
bot_trading_enabled = True 

@app.get("/toggle_trading_status", response_class=HTMLResponse)
async def toggle_trading_view():
    global bot_trading_enabled
    status_message = "ENABLED" if bot_trading_enabled else "DISABLED"
    button_text = "Disable Trading" if bot_trading_enabled else "Enable Trading"
    return HTMLResponse(f"""
    <html><head><title>Toggle Trading</title>{COMMON_CSS}</head><body><div class="container">
        <h1>Toggle Bot Trading</h1>
        <p>Current Bot Trading Status: <strong>{status_message}</strong></p>
        <form action="/toggle_trading_action" method="post">
            <button type="submit">{button_text}</button>
        </form>
        <p><a href="/dashboard_menu_page" class="button-link">Back to Menu</a></p>
    </div></body></html>""")

@app.post("/toggle_trading_action")
async def toggle_trading_action_endpoint():
    global bot_trading_enabled
    bot_trading_enabled = not bot_trading_enabled
    status_message = "ENABLED" if bot_trading_enabled else "DISABLED"
    print(f"Bot trading status changed to: {status_message}")
    log_event(ALERT_LOG_PATH, {"event": "Trading Status Changed", "status": status_message})
    return RedirectResponse(url="/toggle_trading_status", status_code=303)


@app.get("/logs/trades", response_class=HTMLResponse)
async def trade_log_view_page():
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "r") as f:
            try:
                trades = json.load(f)
            except json.JSONDecodeError:
                trades = []
    else:
        trades = []

    summary_by_strategy = {}
    for t in trades:
        if not t.get("event", "").startswith("exit"):
            continue
        strat = t.get("strategy", "Unknown")
        summary = summary_by_strategy.setdefault(strat, {
            "wins": 0, "losses": 0, "total_pnl": 0, "count": 0,
            "best": float('-inf'), "worst": float('inf')
        })
        pnl = t.get("pnl", 0)
        summary["wins"] += 1 if pnl > 0 else 0
        summary["losses"] += 1 if pnl <= 0 else 0
        summary["total_pnl"] += pnl
        summary["count"] += 1
        summary["best"] = max(summary["best"], pnl)
        summary["worst"] = min(summary["worst"], pnl)

    summary_html = "".join([
        f"<h3>{s}</h3><ul>"
        f"<li>Total Trades: {d['count']}</li>"
        f"<li>Wins: {d['wins']} | Losses: {d['losses']}</li>"
        f"<li>Total PnL: ${d['total_pnl']:.2f}</li>"
        f"<li>Average PnL: ${d['total_pnl']/d['count']:.2f}</li>"
        f"<li>Best: ${d['best']:.2f} | Worst: ${d['worst']:.2f}</li></ul>"
        for s, d in summary_by_strategy.items()
    ])

    rows_html = "".join([
        f"<tr><td>{t.get('timestamp','')}</td><td>{t.get('strategy','')}</td><td>{t.get('ticker','')}</td><td>{t.get('signal','')}</td>"
        f"<td>{t.get('entry_price_estimate', t.get('entry_price',''))}</td><td>{t.get('exit_price','')}</td><td>{t.get('pnl','')}</td></tr>"
        for t in reversed(trades)
    ])

    return HTMLResponse(f"""
    <html><head><title>Trade History</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Trade History</h1>
    <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    <h2>Performance Summary</h2>
    {summary_html}
    <h2>Trade Log</h2>
    <a href='/download/trades.csv' class='button-link'>Download CSV</a>
    <table><thead><tr><th>Time</th><th>Strategy</th><th>Symbol</th><th>Signal</th><th>Entry</th><th>Exit</th><th>PnL</th></tr></thead><tbody>{rows_html}</tbody></table>
    </div></body></html>""")

@app.get("/download/trades.csv")
async def download_trade_log():
    if not os.path.exists(TRADE_LOG_PATH):
        return HTMLResponse("Trade log not found", status_code=404)

    with open(TRADE_LOG_PATH, "r") as f:
        try:
            trades = json.load(f)
        except json.JSONDecodeError:
            trades = []

    def generate():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["timestamp", "strategy", "ticker", "signal", "entry_price_estimate", "exit_price", "pnl"])
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "timestamp": t.get("timestamp"),
                "strategy": t.get("strategy"),
                "ticker": t.get("ticker"),
                "signal": t.get("signal"),
                "entry_price_estimate": t.get("entry_price_estimate", t.get("entry_price")),
                "exit_price": t.get("exit_price"),
                "pnl": t.get("pnl")
            })
        yield output.getvalue()

    return StreamingResponse(generate(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trades.csv"})

@app.get("/logs/alerts", response_class=HTMLResponse)
async def alert_log_view_page():
    if os.path.exists(ALERT_LOG_PATH):
        with open(ALERT_LOG_PATH, "r") as f:
            try:
                alerts = json.load(f)
            except json.JSONDecodeError:
                alerts = []
    else:
        alerts = []

    rows_html = "".join([
        f"<tr><td>{a.get('timestamp','')}</td><td>{a.get('strategy','')}</td><td>{a.get('signal','')}</td><td>{a.get('ticker','')}</td><td>{a.get('event','')}</td><td>{a.get('error',a.get('detail',''))}</td></tr>"
        for a in reversed(alerts)
    ])

    return HTMLResponse(f"""
    <html><head><title>Alert History</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Alert History</h1>
    <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    <table><thead><tr><th>Time</th><th>Strategy</th><th>Signal</th><th>Ticker</th><th>Event</th><th>Error</th></tr></thead><tbody>{rows_html}</tbody></table>
    </div></body></html>""")
