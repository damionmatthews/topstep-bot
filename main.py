from fastapi import FastAPI, Request, Form, BackgroundTasks, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import json
import csv
from signalRClient import setupSignalRConnection, closeSignalRConnection, get_event_data, HubConnectionBuilder, register_trade_callback
import logging
import os
import asyncio
import userHubClient
from userHubClient import register_trade_event_callback, setupUserHubConnection, handle_user_trade

app = FastAPI()

# --- ENVIRONMENT CONFIG ---
ACCOUNT_ID = 7715028
CONTRACT_ID = os.getenv("CONTRACT_ID")
SESSION_TOKEN = None

# Strategy storage path
API_BASE_AUTH = "https://api.topstepx.com"
API_BASE_GATEWAY = "https://api.topstepx.com"
STRATEGY_PATH = "strategies.json"
STRATEGIES_FILE_PATH = "strategies.json"  # Or wherever your strategies are stored
def save_strategies_to_file():
    with open(STRATEGIES_FILE_PATH, "w") as f:
        json.dump(strategies, f, indent=2)
        
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
    "MAX_TRADE_LOSS": -160,
    "MAX_TRADE_PROFIT": 90,
    "CONTRACT_SYMBOL": "NQ",
    "PROJECTX_CONTRACT_ID": "CON.F.US.ENQ.M25",
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
                json={"userName": os.getenv("TOPSTEP_USERNAME"), "apiKey": os.getenv("TOPSTEP_API_KEY")}
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
    contract_id = os.getenv("PROJECTX_CONTRACT_ID") or "CON.F.US.EP.M25"

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

# --- EVENT HANDLERS ---
# Register the callback on app startup
def init_userhub_callbacks():
    def handle_user_trade(args):
        logger.info(f"[TRACE] handle_user_trade triggered with: {args}")
        # Example: process the trade or forward it to a downstream system
        trade_id = args.get("orderId")
        price = args.get("price")
        status = args.get("status")

        logger.info(f"[Trade] ID: {trade_id}, Price: {price}, Status: {status}")

    userHubClient.register_trade_event_callback(handle_user_trade)

# Background loop to regularly check trade status
async def periodic_status_check():
    while True:
        try:
            await check_and_close_active_trade()
        except Exception as e:
            logger.error(f"[PeriodicCheck] Error: {e}")
        await asyncio.sleep(60)

# Called by userHubClient when trade events come in
def on_trade_update(args):
    logger.info(f"[DEBUG] on_trade_update received: {args}")
    if isinstance(args, dict):
        trades = args.get("data") or [args]
    elif isinstance(args, list):
        trades = args
    else:
        logger.warning("[Trade Update] Unexpected format: %s", args)
        return

    for trade in trades:
        order_id = trade.get("orderId")
        price = trade.get("price")
        status = trade.get("status")

        for strategy, state in trade_states.items():
            current_trade = state.get("current_trade")
            if not current_trade or current_trade.order_id != order_id:
                continue

            if price:
                current_trade.entry_price = price
                log_event(TRADE_LOG_PATH, {
                    "event": "entry_filled",
                    "strategy": strategy,
                    "orderId": order_id,
                    "entry_price": price
                })

            if status == "Closed":
                logger.info(f"[Trade Handler] Trade {order_id} for {strategy} closed externally.")
                exit_price = price or current_trade.entry_price
                direction = current_trade.direction
                points = (exit_price - current_trade.entry_price) * (1 if direction == "long" else -1)
                pnl = points * 20  # For NQ

                log_event(TRADE_LOG_PATH, {
                    "event": "exit_external",
                    "strategy": strategy,
                    "orderId": order_id,
                    "entry_price": current_trade.entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "timestamp": datetime.utcnow().isoformat()
                })

                state["trade_active"] = False
                state["current_trade"] = None

# Called by FastAPI on startup
async def startup_tasks():
    init_userhub_callbacks()
    asyncio.create_task(periodic_status_check())
    
def fetch_latest_quote():
    return get_event_data()

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
    
    TOPSTEP_USERNAME = os.getenv("TOPSTEP_USERNAME")
    TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")

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

            contract_id = os.getenv("CONTRACT_ID") or "CON.F.US.EP.M25"
            if not token or not contract_id:
                logger.error("[SignalR] Missing authToken or contractId.")
            else:
                setupSignalRConnection(token, contract_id)
                setupUserHubConnection(token)
                init_userhub_callbacks()

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during login: {e}")
        raise
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise

async def trade_event_handler(args):
    global strategy_that_opened_trade
    if strategy_that_opened_trade:
        await check_and_close_active_trade(strategy_that_opened_trade)

register_trade_callback(trade_event_handler)

# CORS middleware for frontend calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def init_userhub_callbacks():
    def handle_user_trade(args):
        logger.info(f"[TRACE] handle_user_trade triggered with: {args}")
        trade_id = args.get("orderId")
        price = args.get("price")
        status = args.get("status")

        logger.info(f"[Trade] ID: {trade_id}, Price: {price}, Status: {status}")
        # Add your custom logic here

    userHubClient.register_trade_event_callback(handle_user_trade)

@app.on_event("startup")
async def startup_event():
    try:
        logger.info("Starting up service...")
        userHubClient.start_userhub_connection()  # make sure this function exists and starts the SignalR connection
        init_userhub_callbacks()
        logger.info("[Startup] UserHub connection initialized.")
    except Exception as e:
        logger.error(f"Startup failed: {str(e)}")
        raise
        
async def startup_wrapper():
    await startup_event()

# TEMP: Simulate a close event to validate handler
handle_user_trade({
    "orderId": 1139051093,
    "price": 4400.25,
    "status": "Closed"
})

async def launch_background_check_loop():
    asyncio.create_task(trade_status_check_loop())

async def trade_status_check_loop():
    while True:
        try:
            await check_trade_status_endpoint()
        except Exception as e:
            logger.error(f"[Trade Loop] Error in periodic check: {e}")
        await asyncio.sleep(30)  # ⏱ Check every 30 seconds

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
    ticker: Optional[str] = "NQ"
    time: Optional[str] = None

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
            "https://api.topstepx.com/api/orders",
            headers={"Authorization": f"Bearer {SESSION_TOKEN}"},
            json={
                "accountId": ACCOUNT_ID,
                "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"],
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
            "https://api.topstepx.com/api/orders",
            headers={"Authorization": f"Bearer {SESSION_TOKEN}"},
            json={
                "accountId": ACCOUNT_ID,
                "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"],
                "qty": strategy_cfg["TRADE_SIZE"],
                "side": side,
                "type": "market"
            }
        )
        response.raise_for_status()
        return response.json()

# --- SIGNALR ---
async def on_execution_message(message):
    logger.info(f"Execution message received: {message}")

async def start_websocket_listener():
    global hub_connection
    hub_connection = HubConnectionBuilder()\
        .with_url("https://api.topstepx.com/signalr")\
        .build()
    hub_connection.on("GatewayExecution", on_execution_message)
    await hub_connection.start()
    logger.info("WebSocket connected and listening.")

# Call on startup
@app.on_event("startup")
async def on_startup():
    await get_projectx_token()
    if SESSION_TOKEN:
        await start_market_data_stream()
    else:
        print(" Cannot start WebSocket: SESSION_TOKEN is missing")
        
async def projectx_api_request(method: str, endpoint: str, payload: dict = None, retries=3):
    for attempt in range(retries):
        token = await get_projectx_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        url = f"https://api.topstepx.com{endpoint}"

        try:
            async with httpx.AsyncClient() as client:
                if method.upper() == "POST":
                    response = await client.post(url, json=payload, headers=headers)
                else:
                    response = await client.get(url, headers=headers)

                if response.status_code == 401:
                    logger.warning("[HTTP] Unauthorized - refreshing token...")
                    await login_to_projectx()
                    continue

                response.raise_for_status()
                return response.json()

        except httpx.RequestError as e:
            logger.warning(f"[HTTP] Attempt {attempt+1} failed: {e}")
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"[HTTP] Unhandled error: {e}")
            raise

    raise RuntimeError(f"API request failed after {retries} attempts: {method} {endpoint}")

# Define the Trade class
class Trade:
    def __init__(self, strategy, order_id, entry_price, direction):
        self.strategy = strategy
        self.order_id = order_id
        self.entry_price = entry_price
        self.direction = direction
        self.entry_time = datetime.utcnow()
        self.exit_price = None
        self.pnl = 0

    def to_dict(self):
        return self.__dict__

# --- Strategy-based Trade States ---
trade_states = {}
user_trade_events = []
import os

STRATEGY_PATH = "strategies.json"

if os.path.exists(STRATEGY_PATH):
    with open(STRATEGY_PATH, "r") as f:
        strategies = json.load(f)
else:
    strategies = {
        "default": {
            "MAX_DAILY_LOSS": -1200,
            "MAX_DAILY_PROFIT": 2000,
            "MAX_TRADE_LOSS": -160,
            "MAX_TRADE_PROFIT": 90,
            "CONTRACT_SYMBOL": "NQ",
            "PROJECTX_CONTRACT_ID": "CON.F.US.ENQ.M25",
            "TRADE_SIZE": 1
        }
    }
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)
BASE_LOG_DIR = os.path.abspath("./logs")
os.makedirs(BASE_LOG_DIR, exist_ok=True)
ALERT_LOG_PATH = os.path.join(BASE_LOG_DIR, "alert_log.json")
TRADE_LOG_PATH = os.path.join(BASE_LOG_DIR, "trade_log.json")

# Fetch current price from trade event
# --- Update fetch_current_price ---
def get_event_data(event_type=None):
    return {}  # Replace with actual logic

def fetch_current_price():
    trade_event = get_event_data('trade')
    if isinstance(trade_event, dict):
        trades = trade_event.get("data") or trade_event.get("trades")
        if isinstance(trades, list) and trades:
            last_trade = trades[-1]
            return last_trade.get("price") or last_trade.get("p")
    return None

# --- Update handle_user_trade to capture actual entry price ---
def handle_user_trade(args):
    global trade_states
    try:
        user_trade_events.append(args)
        logger.info(f"[UserHub] handle_user_trade triggered with: {args}")

        trade = args[0] if isinstance(args, list) else args
        order_id = trade.get('orderId')
        price = trade.get('price')
        status = trade.get('status')

        if not order_id:
            logger.warning("[UserHub] No orderId in trade event. Skipping.")
            return

        for strategy, state in trade_states.items():
            current = state.get("current_trade")
            if current and current.order_id == order_id:
                # Set entry price if available
                if price and not current.entry_price:
                    current.entry_price = price
                    log_event(TRADE_LOG_PATH, {
                        "event": "entry_filled",
                        "strategy": strategy,
                        "orderId": order_id,
                        "entry_price": price
                    })
                    logger.info(f"[UserHub] Entry price updated for strategy '{strategy}': {price}")

                # Handle external close
                if status == "Closed":
                    exit_price = price or current.entry_price
                    pnl = ((exit_price - current.entry_price) *
                           (1 if current.direction == "long" else -1)) * 20 * active_strategy_config.get("TRADE_SIZE", 1)

                    log_event(TRADE_LOG_PATH, {
                        "event": "exit_external",
                        "strategy": strategy,
                        "orderId": order_id,
                        "entry_price": current.entry_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                    logger.info(f"[UserHub] Trade {order_id} closed for strategy '{strategy}'. PnL: {pnl:.2f}")

                    # Reset state
                    state["trade_active"] = False
                    state["current_trade"] = None
                    return

    except Exception as e:
        logger.error(f"[UserHub] Trade handler error: {e}")

# --- ORDER FUNCTIONS ---
async def place_order_projectx(signal, strategy_cfg):
    if "PROJECTX_CONTRACT_ID" not in strategy_cfg or not strategy_cfg["PROJECTX_CONTRACT_ID"]:
        raise ValueError("PROJECTX_CONTRACT_ID is missing or empty in strategy configuration.")
    if not ACCOUNT_ID:
        raise ValueError("ACCOUNT_ID environment variable is not set.")

    payload = {
        "accountId": int(ACCOUNT_ID),
        "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"],
        "type": 2,  # Market order
        "side": 0 if signal_direction == "long" else 1,
        "size": strategy_cfg["TRADE_SIZE"]
    }

    logger.info(f"[Order Attempt] Sending order with payload: {json.dumps(payload)}")

    try:
        # --- Manual HTTP call to capture full response ---
        token = await get_projectx_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        url = "https://api.topstepx.com/api/Order/place"

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)

        # Log raw response details
        logger.info(f"[HTTP Status] {response.status_code}")
        logger.info(f"[HTTP Headers] {dict(response.headers)}")
        logger.info(f"[HTTP Body] {response.text}")

        result = response.json()
        logger.info(f"[Order Response] Parsed JSON: {result}")

        if not result.get("success", False) or not result.get("orderId"):
            error_msg = result.get("errorMessage") or f"Unknown error. Code {result.get('errorCode')}"
            raise ValueError(f"Order placement failed, response: {error_msg}")

        logger.info(f"✅ Order placed successfully. Order ID: {result['orderId']}")
        return {"success": True, "orderId": result["orderId"]}

    except Exception as e:
        logger.error(f"❌ Exception during order placement: {str(e)}")
        raise

# Define SignalAlert model
class SignalAlert(BaseModel):
    signal: str
    ticker: str
    time: str = None

async def place_order_projectx(signal_direction, strategy_cfg):
    payload = {
        "accountId": int(ACCOUNT_ID),
        "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"],
        "type": 2,  # Market order
        "side": 0 if signal_direction == "long" else 1,
        "size": strategy_cfg["TRADE_SIZE"]
    }

    token = await get_projectx_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    url = "https://api.topstepx.com/api/Order/place"

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        result = response.json()

    if result.get("success") and result.get("orderId"):
        return {"success": True, "orderId": result["orderId"]}
    else:
        raise ValueError(f"Order placement failed: {result}")

# --- Safe Log Writer ---
def log_event(path, data):
    import json, os
    try:
        if not isinstance(data, dict):
            logger.warning(f"log_event skipped invalid data: {data}")
            return
        log = []
        if os.path.exists(path):
            with open(path, 'r') as f:
                try:
                    log = json.load(f)
                    if not isinstance(log, list):
                        logger.warning(f"log_event found malformed log: resetting {path}")
                        log = []
                except json.JSONDecodeError:
                    logger.warning(f"log_event could not parse {path}, resetting.")
                    log = []
        log.append(data)
        with open(path, 'w') as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        logger.error(f"log_event error: {e}")

# --- Trade Viewer Sanitizer ---
def filter_valid_trades(trades):
    valid_trades = []
    for t in trades:
        if isinstance(t, dict) and t.get("event", "").startswith("exit"):
            valid_trades.append(t)
        else:
            logger.warning(f"Invalid trade log entry skipped: {t}")
    return valid_trades

async def poll_order_fill(order_id: int):
    max_attempts = 10
    delay = 2
    for attempt in range(max_attempts):
        await asyncio.sleep(delay)
        response = await projectx_api_request("POST", "/api/Order/searchOpen", payload={"accountId": int(ACCOUNT_ID)})
        open_orders = response.get("orders", [])
        if not any(order.get("id") == order_id for order in open_orders):
            logger.info(f"Order {order_id} has been filled.")
            return True
        logger.info(f"Order {order_id} not filled yet. Attempt {attempt + 1}/{max_attempts}")
    logger.warning(f"Order {order_id} was not filled after {max_attempts} attempts.")
    return False
    
async def close_position_projectx(strategy_cfg: dict, current_active_signal: str):
    if "PROJECTX_CONTRACT_ID" not in strategy_cfg:
         raise ValueError(f"PROJECTX_CONTRACT_ID not found for symbol {strategy_cfg['CONTRACT_SYMBOL']}")
    payload = {
        "accountId": int(ACCOUNT_ID),
        "contractId": strategy_cfg["PROJECTX_CONTRACT_ID"]
    }
    log_event(ALERT_LOG_PATH, {"event": "Closing Position", "strategy": current_strategy_name, "payload": payload})
    return await projectx_api_request("POST", "/api/Position/closeContract", payload=payload)

# --- Ensure entry_price is available before PnL ---
async def check_and_close_active_trade(strategy_name_of_trade: str):
    global trade_active, entry_price, daily_pnl, current_signal_direction, current_trade_id

    if not trade_active:
        logger.debug("check_and_close_active_trade called, but no trade is active.")
        return

    logger.debug(f"Trade exit check: entry_price={entry_price}, direction={current_signal_direction}")

    if entry_price is None:
        logger.warning("Entry price not yet available for trade check.")
        return

    # Get the config for the strategy that PLACED the trade
    trade_strategy_cfg = strategies.get(strategy_name_of_trade)
    if not trade_strategy_cfg:
        print(f"Error: Strategy config for '{strategy_name_of_trade}' (active trade) not found.")
        return
    current_market_price = await fetch_current_price(trade_strategy_cfg.get("PROJECTX_CONTRACT_ID", "N/A")) # Get current price

    if current_market_price is None:
        print("Could not fetch current market price to check trade.")
        return
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
strategy_that_opened_trade = None  # Global to track which strategy opened the current trade

# Receive alert and execute strategy
@app.post("/webhook/{strategy_webhook_name}")
async def receive_alert_strategy(strategy_webhook_name: str, alert: SignalAlert):
    global trade_states

    log_event(ALERT_LOG_PATH, {
        "event": "Webhook Received",
        "strategy": strategy_webhook_name,
        "signal": alert.signal,
        "ticker": alert.ticker
    })

    if strategy_webhook_name not in strategies:
        return {"status": "error", "reason": f"Strategy '{strategy_webhook_name}' not found"}

    strategy_cfg = strategies[strategy_webhook_name]
    state = trade_states.setdefault(strategy_webhook_name, {
        "trade_active": False,
        "daily_pnl": 0.0,
        "current_trade": None
    })

    if state["daily_pnl"] >= strategy_cfg.get("MAX_DAILY_PROFIT", float("inf")) or state["daily_pnl"] <= strategy_cfg.get("MAX_DAILY_LOSS", float("-inf")):
        log_event(ALERT_LOG_PATH, {
            "event": "Trading Halted (Daily Limit)",
            "strategy": strategy_webhook_name
        })
        return {"status": "halted", "reason": "daily limit reached"}

    if state["trade_active"]:
        log_event(ALERT_LOG_PATH, {
            "event": "Trade Ignored (Already Active)",
            "strategy": strategy_webhook_name
        })
        return {"status": "ignored", "reason": "trade already active for strategy"}

    print(f"[{strategy_webhook_name}] Received {alert.signal} signal for {alert.ticker} at {alert.time if hasattr(alert, 'time') and alert.time else 'N/A'}")

    try:
        result = await place_order_projectx(alert.signal, strategy_cfg)
        print(f"[DEBUG] place_order_projectx result: {result}")
        if result.get("success") and result.get("orderId"):
            order_id = result["orderId"]
            state["current_trade"] = Trade(strategy_webhook_name, order_id, None, alert.signal)
            state["trade_active"] = True
            print("[DEBUG] Logging trade entry...")
            log_event(TRADE_LOG_PATH, {
                "event": "entry",
                "strategy": strategy_webhook_name,
                "signal": alert.signal,
                "ticker": alert.ticker,
                "entry_price_estimate": None,
                "projectx_order_id": order_id
            })
            print("[DEBUG] Trade entry logged successfully.")
            return {"status": "trade placed", "projectx_order_id": order_id}
        else:
            print(f"[DEBUG] Order not placed. Response: {result}")
            error_msg = result.get("errorMessage", "Unknown error placing order.")
            log_event(ALERT_LOG_PATH, {
                "event": "Order Placement Failed",
                "strategy": strategy_webhook_name,
                "error": error_msg,
                "response": result
            })
            return {"status": "error", "reason": f"Failed to place order: {error_msg}"}
    except Exception as e:
        log_event(ALERT_LOG_PATH, {
            "event": "Error Processing Webhook",
            "strategy": strategy_webhook_name,
            "error": str(e)
        })
        return {"status": "error", "detail": str(e)}
        
def save_trade_states():
    with open("active_trades.json", "w") as f:
        json.dump({k: v["current_trade"].to_dict() for k, v in trade_states.items() if v["current_trade"]}, f, indent=2)

# Placeholder for user_trade_events list
user_trade_events = []

@app.get("/check_trade_status")
async def check_trade_status_endpoint():
    global strategy_that_opened_trade
    if not trade_active:
        return {"status": "no active trade"}
    if not strategy_that_opened_trade:
        return {"status": "error", "reason": "Trade active but opening strategy unknown."}

    await check_and_close_active_trade(strategy_that_opened_trade)

    if trade_active:
        return {
            "status": "checked_still_active",
            "entry_price": entry_price,
            "signal": current_signal_direction
        }
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

@app.get("/debug_account_info")
async def debug_account_info():
    try:
        token = await get_projectx_token()
        accounts_response = await projectx_api_request("POST", "/api/Account/search", payload={})
        contracts_response = await projectx_api_request("POST", "/api/Contract/search", payload={"live": True, "searchText": "ENQ"})

        return {
            "session_token": token,
            "accounts": accounts_response,
            "sample_contract_search_ENQ": contracts_response.get("contracts", [])
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/", response_class=HTMLResponse)
async def config_dashboard_with_selection_get(strategy_selected: str = None):
    global current_strategy_name
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    return await config_dashboard_page()

@app.get("/admin/clear_logs")
async def clear_logs():
    try:
        for f in ["trade_log.json", "alert_log.json"]:
            if os.path.exists(f):
                os.remove(f)
        return {"status": "success", "message": "Log files deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/contract_search", response_class=HTMLResponse)
async def contract_search_page(search_query: str = ""):
    results_html = ""
    if search_query:
        try:
            contracts = await projectx_api_request("POST", "/api/Contract/search", payload={"live": False, "searchText": search_query})
            for contract in contracts.get("contracts", []):
                results_html += f"""
                <tr>
                    <td>{contract['id']}</td>
                    <td>{contract['name']}</td>
                    <td>{contract['description']}</td>
                    <td>{contract['tickSize']}</td>
                    <td>{contract['tickValue']}</td>
                    <td><button onclick="copyToConfig('{contract['id']}')">Copy</button></td>
                </tr>
                """
        except Exception as e:
            results_html = f"<tr><td colspan='6'>Error: {str(e)}</td></tr>"

    html = f"""
    <html>
    <head><title>Contract Search</title>{COMMON_CSS}</head>
    <body><div class="container">
        <h1>Contract Search</h1>
        <form method="get">
            <label for="search_query">Search Term:</label>
            <input type="text" id="search_query" name="search_query" value="{search_query}" />
            <button type="submit">Search</button>
        </form>
        <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
        <table>
            <thead>
                <tr><th>ID</th><th>Name</th><th>Description</th><th>Tick Size</th><th>Tick Value</th><th>Action</th></tr>
            </thead>
            <tbody>{results_html}</tbody>
        </table>
    </div>
    <script>
        function copyToConfig(contractId) {{
            const input = document.getElementById('projectx_contract_id');
            if (input) {{
                input.value = contractId;
                alert("Contract ID copied to config input.");
            }} else {{
                alert("ProjectX Contract ID input field not found in strategy config form.");
            }}
        }}
    </script>
    </body></html>"""
    
    return HTMLResponse(content=html)

    
@app.head("/", response_class=HTMLResponse)
async def config_dashboard_with_selection_head(strategy_selected: str = None):
    global current_strategy_name
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    # FastAPI handles sending only headers for HEAD requests when the GET handler returns a Response
    return await config_dashboard_page()

async def config_dashboard_page():
    global current_strategy_name
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
            <li><a href="/contract_search">Contract Search</a></li>
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
