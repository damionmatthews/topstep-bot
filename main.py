from fastapi import FastAPI, Request, Form, BackgroundTasks, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field # Ensured Field is imported
from typing import Optional, List, Any, Dict, Callable
from pydantic import BaseModel, Field
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from topstep_client import (
    APIClient,
    get_authenticated_client,
    MarketDataStream,
    UserHubStream,
    StreamConnectionState,
    TopstepAPIError,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    Account,
    Contract,
    OrderRequest,
    OrderDetails,
    PlaceOrderResponse
)
from topstep_client.schemas import (
    OrderSide, 
    OrderType, 
    PositionType
)

import httpx # Kept for now, though direct usage should be minimized
import os
import json
import csv
# from signalRClient import setupSignalRConnection, closeSignalRConnection, get_event_data, HubConnectionBuilder, register_trade_callback # Removed
import logging
import os
import asyncio
# import userHubClient # Removed
# from userHubClient import register_trade_event_callback, setupUserHubConnection, handle_user_trade # Removed

app = FastAPI()

# --- ENVIRONMENT CONFIG ---
ACCOUNT_ID = 7715028 # Example, should be ideally fetched from APIClient after auth or config
CONTRACT_ID = os.getenv("CONTRACT_ID")
SESSION_TOKEN = None # Will be managed by APIClient

# Strategy storage path
API_BASE_AUTH = "https://api.topstepx.com" # Used by APIClient by default
API_BASE_GATEWAY = "https://api.topstepx.com" # Used by APIClient by default
STRATEGY_PATH = "strategies.json"
STRATEGIES_FILE_PATH = "strategies.json"
def save_strategies_to_file():
    with open(STRATEGIES_FILE_PATH, "w") as f:
        json.dump(strategies, f, indent=2)
        
TRADE_LOG_PATH = "trade_log.json"
ALERT_LOG_PATH = "alert_log.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

background_loop = asyncio.get_event_loop()

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
daily_pnl = 0.0 # This should become per-strategy
trade_active = False # This should become per-strategy
entry_price = None # This should become per-strategy
trade_time = None # This should become per-strategy
current_signal = None # This should become per-strategy
current_signal_direction = None # This should become per-strategy
# market_data_connection = None # Replaced by market_stream

# --- TOPSTEP CLIENT INITIALIZATION ---
api_client: Optional[APIClient] = None
market_stream: Optional[MarketDataStream] = None
user_stream: Optional[UserHubStream] = None

async def initialize_topstep_client():
    global api_client, market_stream, user_stream, SESSION_TOKEN
    logger.info("Initializing Topstep Client...")
    try:
        # APIClient will use TOPSTEP_USERNAME and TOPSTEP_API_KEY from env by default
        api_client = APIClient()
        await api_client.authenticate() # Authenticate on startup
        SESSION_TOKEN = api_client._session_token # Update global SESSION_TOKEN if still used elsewhere for compatibility
        logger.info("APIClient authenticated.")

        # Initialize streams
        market_stream = MarketDataStream(api_client, on_state_change_callback=handle_market_stream_state_change, on_trade_callback=handle_market_trade_event, on_quote_callback=handle_market_quote_event, on_depth_callback=handle_market_depth_event, debug=True)
        user_stream = UserHubStream(api_client, on_state_change_callback=handle_user_stream_state_change, on_user_trade_callback=handle_user_trade_event_from_stream, on_user_order_callback=handle_user_order_event_from_stream, on_user_position_callback=handle_user_position_event_from_stream, debug=True)

        logger.info("Market and User streams initialized.")

    except AuthenticationError as e:
        logger.error(f"Client Authentication Error: {e}")
        # Handle auth failure, maybe retry or exit
    except Exception as e:
        logger.error(f"Failed to initialize Topstep Client: {e}", exc_info=True)

async def start_streams_if_needed():
    global market_stream, user_stream, api_client
    if not api_client or not api_client._session_token:
        logger.warning("API client not authenticated. Cannot start streams.")
        return

    if market_stream and market_stream.current_state != StreamConnectionState.CONNECTED:
        logger.info("Attempting to start market data stream...")
        # Ensure ACCOUNT_ID is correctly sourced, e.g., from api_client.user_id or a primary account after auth
        # This is a placeholder for actual account ID logic
        global ACCOUNT_ID
        try:
            accs = await api_client.get_accounts()
            if accs:
                ACCOUNT_ID = accs[0].id # Use the first account's ID; adjust as needed
                logger.info(f"Using Account ID: {ACCOUNT_ID} for operations.")
            else:
                logger.error("No accounts found for the user. Cannot determine ACCOUNT_ID.")
                return # Or handle as appropriate
        except Exception as e:
            logger.error(f"Failed to get accounts to determine ACCOUNT_ID: {e}")
            return


        contract_id_to_subscribe = active_strategy_config.get("PROJECTX_CONTRACT_ID") or os.getenv("CONTRACT_ID") or "CON.F.US.NQ.M25"
        if contract_id_to_subscribe:
            # asyncio.create_task(market_stream.start()) # Commented out as per requirement
            # market_stream.subscribe_contract(contract_id_to_subscribe) # Commented out as per requirement
            logger.info(f"Market stream start and subscription are now deferred until explicitly needed by a trade for contract {contract_id_to_subscribe}.") # Updated log message
        else:
            logger.error("No contract ID configured for market data stream.")

    if user_stream and user_stream.current_state != StreamConnectionState.CONNECTED:
        logger.info("Attempting to start user hub stream...")
        asyncio.create_task(user_stream.start())
        logger.info("User hub stream start initiated.")

# Stream state change handlers
def handle_market_stream_state_change(state: StreamConnectionState):
    logger.info(f"Market Stream state changed: {state.value}")
    if state == StreamConnectionState.CONNECTED:
        contract_id_to_subscribe = active_strategy_config.get("PROJECTX_CONTRACT_ID") or os.getenv("CONTRACT_ID") or "CON.F.US.NQ.M25"
        if market_stream and contract_id_to_subscribe:
            logger.info(f"Market Stream CONNECTED. Ensuring subscription to {contract_id_to_subscribe}.")
            # Subscription is now handled by _on_open in the stream based on its _subscriptions set
            # To ensure it subscribes, we call subscribe_contract which adds to _subscriptions
            # then start() will trigger the actual send on connection.
            # If already connected, we might need to explicitly call send here or have stream handle it.
            # The current MarketDataStream's _on_open handles resubscription.
            # Calling subscribe_contract here ensures it's in the set for future (re)connections too.
            market_stream.subscribe_contract(contract_id_to_subscribe)


def handle_user_stream_state_change(state: StreamConnectionState):
    logger.info(f"User Hub Stream state changed: {state.value}")

async def ensure_market_stream_for_contract(contract_id: str):
    global market_stream
    if not market_stream:
        logger.error("MarketDataStream is not initialized. Cannot ensure stream for contract.")
        return

    if market_stream.current_state != StreamConnectionState.CONNECTED:
        logger.info(f"MarketDataStream not connected. Attempting to start for contract {contract_id}...")
        try:
            success = await market_stream.start()
            if not success:
                logger.error(f"Failed to start MarketDataStream for contract {contract_id}.")
                return
            # Wait a brief moment for connection to fully establish if start() is not fully blocking
            await asyncio.sleep(1) # Allow time for _on_open to potentially fire
        except Exception as e:
            logger.error(f"Exception starting MarketDataStream for {contract_id}: {e}", exc_info=True)
            return

    # Subscribe (MarketDataStream.subscribe_contract should be idempotent or handle existing subscriptions)
    if market_stream.current_state == StreamConnectionState.CONNECTED:
        logger.info(f"Ensuring subscription to {contract_id} on MarketDataStream.")
        market_stream.subscribe_contract(contract_id)
    else:
        logger.warning(f"MarketDataStream still not connected after start attempt. Cannot subscribe to {contract_id}.")

async def manage_market_stream_after_trade_close(closed_trade_contract_id: str):
    global market_stream, trade_states, strategies # Ensure strategies is accessible
    if not market_stream:
        logger.warning("MarketDataStream not initialized, cannot manage after trade close.")
        return

    if not closed_trade_contract_id or closed_trade_contract_id == "UNKNOWN_CONTRACT":
        logger.warning(f"Invalid or unknown contract ID ('{closed_trade_contract_id}') for closed trade. Cannot manage market stream subscriptions.")
        return

    # Check if any other active trades are using this contract_id
    is_contract_still_active = False
    for strategy_name, state in trade_states.items():
        if state.get("trade_active"):
            current_trade_in_state: Optional[Trade] = state.get("current_trade")
            # Ensure current_trade_in_state and its contract_id are valid before comparing
            if current_trade_in_state and \
               hasattr(current_trade_in_state, 'contract_id') and \
               current_trade_in_state.contract_id == closed_trade_contract_id:
                is_contract_still_active = True
                break

    if not is_contract_still_active:
        logger.info(f"No other active trades for {closed_trade_contract_id}. Unsubscribing from MarketDataStream.")
        if market_stream.current_state == StreamConnectionState.CONNECTED:
            market_stream.unsubscribe_contract(closed_trade_contract_id)
            # Give a moment for unsubscribe to process if needed
            await asyncio.sleep(0.5)
        else:
            # If not connected, ensure it's removed from the internal list for next connect
            # This check is important to prevent errors if _subscriptions is None or contract_id is not present
            if hasattr(market_stream, '_subscriptions') and market_stream._subscriptions is not None and closed_trade_contract_id in market_stream._subscriptions:
                 market_stream._subscriptions.remove(closed_trade_contract_id)
            logger.info(f"Market stream not connected, noted unsubscription for {closed_trade_contract_id}.")

        # Check if any subscriptions are left at all
        # Accessing _subscriptions directly; ideally MarketDataStream would have a public property/method
        if hasattr(market_stream, '_subscriptions') and not market_stream._subscriptions:
            logger.info("No active market data subscriptions left. Stopping MarketDataStream.")
            await market_stream.stop()
    else:
        logger.info(f"Contract {closed_trade_contract_id} is still active in other trades. Market stream subscription retained.")

async def stop_all_streams():
    global market_stream, user_stream
    if market_stream and market_stream.current_state != StreamConnectionState.DISCONNECTED:
        logger.info("Stopping market stream...")
        await market_stream.stop()
    if user_stream and user_stream.current_state != StreamConnectionState.DISCONNECTED:
        logger.info("Stopping user hub stream...")
        await user_stream.stop()
    logger.info("All streams stopped.")

# CORS middleware for frontend calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- AUTHENTICATION (Old functions removed as APIClient handles this) ---

# --- EVENT HANDLERS ---
latest_market_quotes: List[Any] = []
latest_market_trades: List[Any] = []
latest_market_depth: List[Any] = []

def handle_market_quote_event(data: List[Any]):
    global latest_market_quotes
    logger.debug(f"[MarketStream] Quote Event Data: {data}")
    latest_market_quotes.extend(data) # Assuming data is a list of quotes
    if len(latest_market_quotes) > 100: latest_market_quotes = latest_market_quotes[-100:]
    # Process each quote if necessary, e.g. update a dict by contract ID

async def trade_event_handler(trade_event_data: Any):
    """
    Handles a single market trade event.
    This function is called for each trade event received from the market data stream.
    It then iterates through all active strategies and calls check_and_close_active_trade.
    """
    global trade_states
    logger.debug(f"trade_event_handler received: {trade_event_data}")
    # The trade_event_data itself might be useful for check_and_close_active_trade if it needs the latest price
    # For now, check_and_close_active_trade fetches its own price.

    active_strategies_to_check = []
    for strategy_name, state in trade_states.items():
        if state.get("trade_active") and state.get("current_trade"):
            active_strategies_to_check.append(strategy_name)

    for strategy_name in active_strategies_to_check:
        logger.debug(f"Checking active trade for strategy '{strategy_name}' due to market trade event.")
        try:
            await check_and_close_active_trade(strategy_name)
        except Exception as e:
            logger.error(f"Error in check_and_close_active_trade for strategy {strategy_name} (triggered by market event): {e}", exc_info=True)

def handle_market_trade_event(data: List[Any]):
    global latest_market_trades, background_loop
    logger.debug(f"[MarketStream] Batch of Trade Event Data received, count: {len(data)}")
    latest_market_trades.extend(data) # Assuming data is a list of trades from the stream
    if len(latest_market_trades) > 100: latest_market_trades = latest_market_trades[-100:] # Keep buffer trimmed

    # Process each trade event in the received list
    for trade_event_item in data:
        # Schedule trade_event_handler to run in the background loop
        # This allows check_and_close_active_trade (which is async) to be called.
        asyncio.run_coroutine_threadsafe(trade_event_handler(trade_event_item), background_loop)

def handle_market_depth_event(data: List[Any]):
    global latest_market_depth
    logger.debug(f"[MarketStream] Depth Event Data: {data}")
    latest_market_depth.extend(data) # Assuming data is a list of depth updates
    if len(latest_market_depth) > 100: latest_market_depth = latest_market_depth[-100:]

def handle_user_trade_event_from_stream(data: List[Any]):
    logger.info(f"[UserStream] User Trade Event Data: {data}")
    for trade_event in data: # data is a list of trade events from the stream
        # The original handle_user_trade function needs to be called or its logic adapted here.
        # Assuming trade_event is a dict compatible with the old handle_user_trade.
        handle_user_trade(trade_event)

def handle_user_order_event_from_stream(data: List[Any]):
    logger.info(f"[UserStream] User Order Event Data: {data}")
    # Process order events (e.g., update local order book, PnL calculations)

def handle_user_position_event_from_stream(data: List[Any]):
    logger.info(f"[UserStream] User Position Event Data: {data}")
    # Process position updates

# Modify fetch_latest_quote etc. to use new storage
def fetch_latest_quote(): return latest_market_quotes[-10:] if latest_market_quotes else []
def fetch_latest_trade(): return latest_market_trades[-10:] if latest_market_trades else []
def fetch_latest_depth(): return latest_market_depth[-10:] if latest_market_depth else []


# Background loop to regularly check trade status (if still needed)
async def periodic_status_check():
    while True:
        try:
            # This function needs to be adapted if it relied on global state changed by old clients
            # await check_and_close_active_trade() # This function needs review for new client
            pass
        except Exception as e:
            logger.error(f"[PeriodicCheck] Error: {e}")
        await asyncio.sleep(60)

# Called by userHubClient when trade events come in (now handle_user_trade_event_from_stream)
# def on_trade_update(args): ... # Replaced by handle_user_trade_event_from_stream and its call to handle_user_trade

async def old_startup_event():
    pass

@app.on_event("startup")
async def startup_event():
    await initialize_topstep_client()
    await start_streams_if_needed()
    # init_userhub_callbacks() # Logic should be in stream handlers or called by them
    # asyncio.create_task(periodic_status_check()) # Keep if still needed and adapted

async def old_shutdown_event():
    pass

@app.on_event("shutdown")
async def shutdown_event():
    await stop_all_streams()
    if api_client:
        await api_client.close()


@app.get("/status") # Ensure this matches the one below or remove duplicate
async def status_endpoint_old(): # Renamed to avoid conflict
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
        order_type: str = Field(default="market", alias="orderType")
        limit_price: Optional[float] = Field(default=None, alias="limitPrice")
        stop_price: Optional[float] = Field(default=None, alias="stopPrice")
        trailingDistance: Optional[int] = Field(default=None, alias="trailingDistance")
        quantity: Optional[int] = Field(default=None, alias="qty")
        account_id: int = Field(alias="accountId") # Add this line

class StatusResponse(BaseModel):
    trade_active: bool
    entry_price: Optional[float] = None
    trade_time: Optional[str] = None
    current_signal: Optional[str] = None # 'long' or 'short'
    daily_pnl: float
    # Added these for more complete UI status
    current_strategy_name: str
    active_strategy_config: dict

class ManualTradeParams(BaseModel):
    account_id: int = Field(..., alias="accountId")
    contract_id: str = Field(..., alias="contractId")
    side: str  # Expected "long" or "short"
    size: int
    trailingDistance: Optional[int] = Field(default=None, alias="trailingDistance")
    # limit_price: Optional[float] = Field(default=None, alias="limitPrice") # Add if limit orders are also needed from this form

class AccountActionParams(BaseModel):
    account_id: int = Field(..., alias="accountId")


# --- COMMON CSS (omitted for brevity in this diff, assumed unchanged) ---
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
def save_strategies(): # Should be save_strategies_to_file to match definition
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)

def log_event(file_path, event_data):
    if not os.path.exists(file_path):
        with open(file_path, "w") as f:
            json.dump([], f)
    
    with open(file_path, "r+") as f:
        try:
            log_list = json.load(f)
        except json.JSONDecodeError:
            log_list = []
        log_list.append({**{"timestamp": datetime.utcnow().isoformat()}, **event_data})
        f.seek(0)
        json.dump(log_list, f, indent=2)
        f.truncate()

# --- ORDER FUNCTIONS (adapted to use APIClient) ---
async def place_order_projectx(alert: SignalAlert, strategy_cfg: dict):
    global ACCOUNT_ID 
    if not api_client: 
        raise TopstepAPIError("APIClient not initialized")
    
    order_account_id = alert.account_id if alert.account_id else ACCOUNT_ID
    if not order_account_id:
        raise ValueError("Account ID is missing: not globally configured and not in alert.")
    
    order_contract_id = alert.ticker or strategy_cfg.get("PROJECTX_CONTRACT_ID")
    if not order_contract_id: 
        raise ValueError("Contract ID missing in alert and strategy config.")

    order_quantity = alert.quantity if alert.quantity is not None else strategy_cfg.get("TRADE_SIZE", 1)
    
    # Convert side string to integer
    side_str = alert.signal.lower()
    order_side_int: int
    if side_str == "long" or side_str == "buy":
        order_side_int = OrderSide.Bid.value
    elif side_str == "short" or side_str == "sell":
        order_side_int = OrderSide.Ask.value
    else:
        raise ValueError(f"Invalid signal/side from webhook: {alert.signal}")

    # Convert order_type string to integer
    order_type_str = alert.order_type.lower()
    order_type_int: int
    if order_type_str == "market":
        order_type_int = OrderType.Market.value
    elif order_type_str == "limit":
        order_type_int = OrderType.Limit.value
    elif order_type_str == "stop": 
        order_type_int = OrderType.Stop.value 
    elif order_type_str == "trailingstop":
        order_type_int = OrderType.TrailingStop.value 
    else:
        logger.error(f"Unknown order type from webhook: {alert.order_type}")
        raise ValueError(f"Unknown order type from webhook: {alert.order_type}")

    pydantic_request_params = {
        "account_id": order_account_id,
        "contract_id": order_contract_id,
        "size": order_quantity,
        "side": order_side_int, # Use integer
        "type": order_type_int,  # Use integer
        "limit_price": alert.limit_price, 
        "stop_price": alert.stop_price    
    }

    if order_type_int == OrderType.Limit.value:
        if alert.limit_price is None:
            raise ValueError("limit_price is required for a limit order.")
    elif order_type_int == OrderType.Stop.value: 
        if alert.stop_price is None:
            raise ValueError("stop_price is required for a stop order.")
    elif order_type_int == OrderType.TrailingStop.value:
        if alert.trailingDistance is None or alert.trailingDistance <= 0:
            raise ValueError("trailing_distance (as price offset) required and must be positive for TrailingStop.")
        pydantic_request_params["trail_price"] = alert.trailingDistance 
        if alert.stop_price is None: 
             pydantic_request_params["stop_price"] = None
    
    final_params_for_request = {k: v for k, v in pydantic_request_params.items() if v is not None}

    logger.info(f"[Order Attempt] Constructing PlaceOrderRequest with: {final_params_for_request}")
    order_req = OrderRequest(**final_params_for_request) 

    try:
        result: PlaceOrderResponse = await api_client.place_order(order_req)
        if result.success and result.order_id is not None:
            logger.info(f"✅ Order placed successfully via APIClient. Order ID: {result.order_id}")
            return {"success": True, "orderId": result.order_id, "details": result.dict(by_alias=True)}
        else:
            err_msg = result.error_message if result else "Order placement failed or no order ID returned."
            error_code_val = result.error_code.value if result and result.error_code else "N/A"
            logger.error(f"Order placement failed: {err_msg} (Code: {error_code_val}). API Response: {result.dict(by_alias=True) if result else 'No response object'}")
            return {"success": False, "errorMessage": err_msg, "details": result.dict(by_alias=True) if result else None}
    except Exception as e:
        logger.error(f"❌ Unexpected exception during order placement: {str(e)}", exc_info=True)
        return {"success": False, "errorMessage": str(e), "details": None}

async def close_position_projectx(strategy_cfg: dict, current_active_signal_direction: str, target_account_id: int, size_to_close: int):
    if not api_client: 
        raise TopstepAPIError("APIClient not initialized")
    if not target_account_id: 
        raise ValueError("Target Account ID not provided for closing position.")

    # Convert side string to integer
    order_side_int: int
    if current_active_signal_direction == "long":
        order_side_int = OrderSide.Ask.value # To close a long, we sell (Ask)
    else: # direction is "short"
        order_side_int = OrderSide.Bid.value  # To close a short, we buy (Bid)
        
    contract_id = strategy_cfg.get("PROJECTX_CONTRACT_ID")
    if not contract_id: 
        raise ValueError(f"PROJECTX_CONTRACT_ID not found for strategy.")

    order_req = OrderRequest(
        account_id=target_account_id, 
        contract_id=contract_id,
        size=size_to_close, 
        side=order_side_int,    # Use integer
        type=OrderType.Market.value # Use integer
    )
    log_event(ALERT_LOG_PATH, {"event": "Closing Position Attempt", "strategy": strategy_cfg.get("CONTRACT_SYMBOL","N/A"), "payload": order_req.dict(by_alias=True)})
    try:
        response: PlaceOrderResponse = await api_client.place_order(order_req)
        if response.success and response.order_id is not None:
            logger.info(f"Close position order placed: {response.order_id}")
            return {"success": True, "orderId": response.order_id}
        else:
            err = response.error_message or "Close position order failed."
            logger.error(f"❌ Close position order failed: {err}. API Response: {response.dict(by_alias=True)}")
            return {"success": False, "errorMessage": err, "details": response.dict(by_alias=True)}
    except Exception as e:
        logger.error(f"Unexpected error closing position: {e}", exc_info=True)
        return {"success": False, "errorMessage": str(e)}

# Define the Trade class
class Trade: # Simple trade state tracking
    def __init__(self, strategy_name: str, order_id: int, direction: str, account_id: int, contract_id: str, entry_price: Optional[float] = None, size: int = 1):
        self.strategy_name = strategy_name
        self.order_id = order_id
        self.account_id = account_id
        self.contract_id = contract_id
        self.entry_price = entry_price
        self.direction = direction # 'long' or 'short'
        self.size = size
        self.entry_time = datetime.utcnow()
        self.exit_price: Optional[float] = None
        self.pnl: float = 0.0
        self.status: str = "open" # open, closed

    def to_dict(self):
        return {
            "strategy_name": self.strategy_name,
            "order_id": self.order_id,
            "account_id": self.account_id,
            "contract_id": self.contract_id,
            "entry_price": self.entry_price,
            "direction": self.direction,
            "size": self.size,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "status": self.status,
        }

# --- Strategy-based Trade States ---
trade_states: dict[str, dict[str, Any]] = {} # Keyed by strategy_name
# Each strategy state: {"trade_active": bool, "daily_pnl": float, "current_trade": Optional[Trade]}

# --- Update fetch_current_price ---
def fetch_current_price(contract_id: Optional[str] = None) -> Optional[float]:
    # This needs to get the latest price for a specific contract_id from latest_market_trades or latest_market_quotes
    # For simplicity, let's assume latest_market_trades contains trades with 'price' and 'contractId'
    if not contract_id: # If no specific contract, try to get a general one (less ideal)
        if latest_market_trades:
            return latest_market_trades[-1].get("price") # Or parse from the list structure
        if latest_market_quotes: # Quotes might be a list of [bid, ask, contractId, ...]
            # This part is highly dependent on actual quote structure
            return latest_market_quotes[-1][0] if latest_market_quotes[-1] else None
        return None

    # Search for the specific contract_id in trades or quotes
    for trade_event_list in reversed(latest_market_trades):
        for trade_event in trade_event_list: # Assuming trades come in lists
             if isinstance(trade_event, dict) and trade_event.get("contractId") == contract_id and "price" in trade_event:
                return trade_event["price"]
    # Fallback to quotes if no trade found for contract
    # Ensure quote_event is a list and has enough elements before accessing indices.
    for quote_event_list in reversed(latest_market_quotes):
        for quote_event in quote_event_list:
            if isinstance(quote_event, list) and len(quote_event) > 2 and quote_event[2] == contract_id:
                # Example: price is quote_event[0] (bid) or quote_event[1] (ask)
                # Using midpoint for simplicity, adjust as needed
                if quote_event[0] is not None and quote_event[1] is not None:
                    return (quote_event[0] + quote_event[1]) / 2
                elif quote_event[0] is not None:
                    return quote_event[0] # Just bid
                elif quote_event[1] is not None:
                    return quote_event[1] # Just ask
    return None


def handle_user_trade(trade_data_item: dict): # Renamed arg to avoid conflict, processes one trade item
    global trade_states # Uses the new per-strategy trade_states
    try:
        # user_trade_events.append(trade_data_item) # If a global raw log is still needed
        logger.info(f"[UserHubCallback] handle_user_trade processing: {trade_data_item}")

        # Assuming trade_data_item is a dictionary representing a single trade event
        order_id = trade_data_item.get('orderId')
        price = trade_data_item.get('price')
        status = trade_data_item.get('status') # e.g. "Filled", "PartiallyFilled", "Working" -> "Filled" is an execution
        # It seems the original `handle_user_trade` was processing "Closed" status for PnL.
        # "Filled" usually means execution. "Closed" might mean the order is no longer active (filled or cancelled).
        # This needs clarification based on TopStep's actual stream messages.
        # For now, let's assume "Filled" updates entry/exit and "Closed" (if it means fully done) finalizes PnL.

        if not order_id:
            logger.warning("[UserHubCallback] No orderId in trade event. Skipping.")
            return

        # Find which strategy's trade this belongs to
        for strategy_name, state in trade_states.items():
            current_trade_obj = state.get("current_trade")
            if current_trade_obj and current_trade_obj.order_id == order_id:
                # This trade event belongs to current_trade_obj of strategy_name
                if status == "Filled" and price is not None:
                    if current_trade_obj.entry_price is None: # First fill for this trade
                        current_trade_obj.entry_price = price
                        current_trade_obj.status = "open_filled" # Mark as filled
                        log_event(TRADE_LOG_PATH, {
                            "event": "entry_filled_userhub", "strategy": strategy_name,
                            "orderId": order_id, "entry_price": price,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        logger.info(f"[UserHubCallback] Entry price for trade {order_id} (Strat: {strategy_name}) updated to: {price}")
                    # If it's a closing trade, this logic might need to be different
                    # e.g. if an opposing order was placed and this is its fill notification
                    elif current_trade_obj.status == "closing": # An exit order was placed and got filled
                        current_trade_obj.exit_price = price
                        current_trade_obj.status = "closed"

                        # Calculate PnL
                        pnl_points = (current_trade_obj.exit_price - current_trade_obj.entry_price) * \
                                     (1 if current_trade_obj.direction == "long" else -1)
                        # Assuming NQ $20 per point, adapt if other symbols
                        contract_cfg = strategies.get(strategy_name, {})
                        point_value = 20 if contract_cfg.get("CONTRACT_SYMBOL", "NQ") == "NQ" else 50 # Example for ES
                        pnl_dollars = pnl_points * point_value * current_trade_obj.size
                        current_trade_obj.pnl = pnl_dollars
                        state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl_dollars

                        log_event(TRADE_LOG_PATH, {
                            "event": "exit_filled_userhub", "strategy": strategy_name,
                            "orderId": order_id, "entry_price": current_trade_obj.entry_price,
                            "exit_price": current_trade_obj.exit_price, "pnl": pnl_dollars,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        logger.info(f"[UserHubCallback] Exit for trade {order_id} (Strat: {strategy_name}). Exit Price: {price}, PnL: {pnl_dollars:.2f}")

                        closed_trade_obj_ref = current_trade_obj # Keep reference before clearing
                        state["trade_active"] = False
                        state["current_trade"] = None # Clear the trade

                        if closed_trade_obj_ref and hasattr(closed_trade_obj_ref, 'contract_id') and closed_trade_obj_ref.contract_id:
                            logger.info(f"Trade closed for contract {closed_trade_obj_ref.contract_id}. Scheduling stream management.")
                            asyncio.create_task(manage_market_stream_after_trade_close(closed_trade_obj_ref.contract_id))
                        else:
                            logger.warning(f"Closed trade object ({closed_trade_obj_ref.order_id if closed_trade_obj_ref else 'N/A'}) or its contract_id not available for stream management.")

                elif status == "Closed": # Order is no longer active, might be fully filled or cancelled
                    if current_trade_obj.status == "open_filled" and current_trade_obj.exit_price is None:
                        # This means the entry order was filled, but now it's "Closed" without an explicit exit fill.
                        # This could be an external close or cancellation. Assume it's an issue or needs manual check.
                        logger.warning(f"[UserHubCallback] Trade {order_id} (Strat: {strategy_name}) 'Closed' without recorded exit. PnL not calculated. Current state: {current_trade_obj.to_dict()}")
                        # Potentially reset state here if it means the trade is truly over
                        # state["trade_active"] = False
                        # state["current_trade"] = None
                    elif current_trade_obj.status == "closed": # Already handled by "Filled" state for exit
                        pass


    except Exception as e:
        logger.error(f"[UserHubCallback] Trade handler error: {e}", exc_info=True)


async def check_and_close_active_trade(strategy_name_of_trade: str):
    # This function needs to use the new per-strategy trade_states
    state = trade_states.get(strategy_name_of_trade)
    if not state or not state.get("trade_active") or not state.get("current_trade"):
        logger.debug(f"check_and_close_active_trade called for {strategy_name_of_trade}, but no active trade found in state.")
        return

    current_trade_obj: Trade = state["current_trade"]
    trade_strategy_cfg = strategies.get(strategy_name_of_trade)

    if not trade_strategy_cfg:
        logger.error(f"Error: Strategy config for '{strategy_name_of_trade}' (active trade) not found.")
        return

    if current_trade_obj.entry_price is None:
        logger.warning(f"Entry price not yet available for trade check (Strat: {strategy_name_of_trade}). Order ID: {current_trade_obj.order_id}")
        return

    current_market_price = fetch_current_price(contract_id=trade_strategy_cfg.get("PROJECTX_CONTRACT_ID"))
    if current_market_price is None:
        logger.warning(f"Could not fetch current market price for {trade_strategy_cfg.get('PROJECTX_CONTRACT_ID')} to check trade.")
        return

    points_pnl = (current_market_price - current_trade_obj.entry_price) * \
                 (1 if current_trade_obj.direction == 'long' else -1)

    # Determine point value based on contract (e.g., NQ $20/pt, ES $50/pt)
    # This should be part of strategy_cfg or a global contract details map
    point_value = 20 if trade_strategy_cfg.get("CONTRACT_SYMBOL", "NQ") == "NQ" else 50 # Default for ES

    total_dollar_pnl = points_pnl * point_value * current_trade_obj.size

    exit_reason = None
    if total_dollar_pnl >= trade_strategy_cfg.get("MAX_TRADE_PROFIT", float('inf')):
        exit_reason = "Max Trade Profit Hit"
    elif total_dollar_pnl <= trade_strategy_cfg.get("MAX_TRADE_LOSS", float('-inf')):
        exit_reason = "Max Trade Loss Hit"

    if exit_reason:
        logger.info(f"[{strategy_name_of_trade}] Attempting to close trade. Reason: {exit_reason}. Current PnL: ${total_dollar_pnl:.2f}")
        try:
            # Mark the trade as "closing" before sending order, so UserHub fill can identify it as an exit
            current_trade_obj.status = "closing"
            close_response = await close_position_projectx(trade_strategy_cfg, current_trade_obj.direction, current_trade_obj.account_id)
            if close_response.get("success"):
                logger.info(f"[{strategy_name_of_trade}] Close order placed. Order ID: {close_response.get('orderId')}. Waiting for fill via UserHub.")
                # PnL calculation and state reset will now primarily happen in handle_user_trade when fill is confirmed
            else:
                current_trade_obj.status = "open_filled" # Revert status if close order failed
                logger.error(f"[{strategy_name_of_trade}] Failed to place close order: {close_response.get('errorMessage')}")
                log_event(ALERT_LOG_PATH, {"event": "Error Closing Position", "strategy": strategy_name_of_trade, "error": close_response.get('errorMessage')})

        except Exception as e:
            current_trade_obj.status = "open_filled" # Revert status
            logger.error(f"Error during automated closing of position for {strategy_name_of_trade}: {e}", exc_info=True)
            log_event(ALERT_LOG_PATH, {"event": "Exception Closing Position", "strategy": strategy_name_of_trade, "error": str(e)})

    # Check daily PnL limits for this strategy
    current_daily_pnl = state.get("daily_pnl", 0.0)
    # Add unrealized PnL of current trade for this check
    effective_daily_pnl = current_daily_pnl + total_dollar_pnl

    if effective_daily_pnl >= trade_strategy_cfg.get("MAX_DAILY_PROFIT", float('inf')) or \
       effective_daily_pnl <= trade_strategy_cfg.get("MAX_DAILY_LOSS", float('-inf')):

        limit_type = "MAX_DAILY_PROFIT" if effective_daily_pnl >= trade_strategy_cfg.get("MAX_DAILY_PROFIT", float('inf')) else "MAX_DAILY_LOSS"
        logger.info(f"[{strategy_name_of_trade}] Daily PnL limit ({limit_type}) hit or exceeded with current trade. Effective PnL: ${effective_daily_pnl:.2f}. Limit: {trade_strategy_cfg.get(limit_type)}")

        if state.get("trade_active"): # If a trade is still active when daily limit hit
            logger.info(f"Forcing close of active trade for strategy {strategy_name_of_trade} due to daily PnL limit.")
            try:
                current_trade_obj.status = "closing" # Mark for exit
                close_response = await close_position_projectx(trade_strategy_cfg, current_trade_obj.direction, current_trade_obj.account_id)
                if close_response.get("success"):
                    logger.info(f"[{strategy_name_of_trade}] Daily limit force-close order placed. Order ID: {close_response.get('orderId')}. Waiting for fill.")
                    # PnL and state reset in handle_user_trade
                else:
                    current_trade_obj.status = "open_filled" # Revert status
                    logger.error(f"[{strategy_name_of_trade}] Failed to place force-close order for daily limit: {close_response.get('errorMessage')}")
            except Exception as e:
                 current_trade_obj.status = "open_filled" # Revert status
                 logger.error(f"Error force-closing position for {strategy_name_of_trade} (daily limit): {e}", exc_info=True)

        # Mark strategy as halted for the day (example, actual halting mechanism might differ)
        state["trading_halted_today"] = True
        logger.info(f"Trading halted for the day for strategy {strategy_name_of_trade} due to PnL limits.")


# --- MAIN ENDPOINTS ---
# strategy_that_opened_trade = None # Replaced by per-strategy state in trade_states

@app.post("/webhook/{strategy_webhook_name}")
async def receive_alert_strategy(strategy_webhook_name: str, alert: SignalAlert, background_tasks: BackgroundTasks):
    global trade_states, ACCOUNT_ID, strategies # Ensure strategies is accessible for strategy_cfg

    log_event(ALERT_LOG_PATH, {
        "event": "Webhook Received", "strategy": strategy_webhook_name,
        "signal": alert.signal, "ticker": alert.ticker
    })

    if strategy_webhook_name not in strategies:
        return {"status": "error", "reason": f"Strategy '{strategy_webhook_name}' not found"}

    strategy_cfg = strategies[strategy_webhook_name]
    # Initialize state for the strategy if it's the first time
    state = trade_states.setdefault(strategy_webhook_name, {
        "trade_active": False, "daily_pnl": 0.0, "current_trade": None, "trading_halted_today": False
    })

    if state.get("trading_halted_today"):
        log_event(ALERT_LOG_PATH, {"event": "Trading Halted (Daily Limit)", "strategy": strategy_webhook_name})
        return {"status": "halted", "reason": "daily PnL limit previously reached for this strategy"}

    if state["trade_active"]:
        log_event(ALERT_LOG_PATH, {"event": "Trade Ignored (Already Active)", "strategy": strategy_webhook_name})
        return {"status": "ignored", "reason": f"trade already active for strategy {strategy_webhook_name}"}

    if not ACCOUNT_ID: # Ensure ACCOUNT_ID is available
        logger.error("ACCOUNT_ID not available for order placement.")
        return {"status": "error", "reason": "Server error: Account ID not configured."}


    logger.info(f"[{strategy_webhook_name}] Received {alert.signal} signal for {alert.ticker} at {alert.time if alert.time else 'N/A'}, OrderType: {alert.order_type}")

    try:
        # Updated call to place_order_projectx, now passing the full alert object
        result = await place_order_projectx(alert, strategy_cfg)

        if result.get("success") and result.get("orderId"):
            order_id = result["orderId"]
            signal_dir = "long" if alert.signal.lower() == "long" else "short"
            # Create a new Trade object and store it in this strategy's state
            # Entry price is initially None; UserHubStream callback (handle_user_trade) will update it on fill
            state["current_trade"] = Trade(
                strategy_name=strategy_webhook_name,
                order_id=order_id, # This is result.orderId from PlaceOrderResponse now
                direction=signal_dir,
                account_id=alert.account_id,
                contract_id=order_req.contract_id, # Use contract_id from the original order_req
                entry_price=None,
                size=strategy_cfg.get("TRADE_SIZE", 1)
            )
            state["trade_active"] = True # Mark trade as active for this strategy

            log_event(TRADE_LOG_PATH, {
                "event": "entry_order_placed", "strategy": strategy_webhook_name,
                "signal": alert.signal, "ticker": alert.ticker,
                "projectx_order_id": order_id, "timestamp": datetime.utcnow().isoformat()
            })

            # Ensure market data stream is active for the traded contract
            contract_to_subscribe = alert.ticker # Primary source from alert
            if not contract_to_subscribe:
                 # Access strategy_cfg which should be defined earlier in this function
                strategy_cfg_for_contract = strategies.get(strategy_webhook_name, {})
                contract_to_subscribe = strategy_cfg_for_contract.get("PROJECTX_CONTRACT_ID") # Fallback

            if contract_to_subscribe:
                background_tasks.add_task(ensure_market_stream_for_contract, contract_to_subscribe)
                logger.info(f"Scheduled MarketDataStream check/start for contract {contract_to_subscribe} in background.")
            else:
                logger.error(f"No contract ID available to ensure market stream for strategy {strategy_webhook_name}.")

            return {"status": "trade_placed", "projectx_order_id": order_id}
        else:
            error_msg = result.get("errorMessage", "Unknown error placing order.")
            log_event(ALERT_LOG_PATH, {
                "event": "Order Placement Failed", "strategy": strategy_webhook_name,
                "error": error_msg, "response": result
            })
            return {"status": "error", "reason": f"Failed to place order: {error_msg}"}
    except Exception as e:
        logger.error(f"Error processing webhook for {strategy_webhook_name}: {e}", exc_info=True)
        log_event(ALERT_LOG_PATH, {
            "event": "Error Processing Webhook", "strategy": strategy_webhook_name, "error": str(e)
        })
        return {"status": "error", "detail": str(e)}

@app.post("/manual/market_order", summary="Place a manual market order")
async def post_manual_market_order(params: ManualTradeParams, background_tasks: BackgroundTasks):
    if not api_client: 
        return {"success": False, "message": "APIClient not initialized."}
    
    logger.info(f"[Manual Trade] Received market order request: {params.dict(by_alias=True)}")

    order_side_int: int
    if params.side.lower() == "long":
        order_side_int = OrderSide.Bid.value
    elif params.side.lower() == "short":
        order_side_int = OrderSide.Ask.value
    else:
        return {"success": False, "message": f"Invalid side parameter: {params.side}. Expected 'long' or 'short'."}

    order_req = OrderRequest(
        account_id=params.account_id,
        contract_id=params.contract_id,
        size=params.size,
        side=order_side_int,    # Use integer
        type=OrderType.Market.value  # Use integer
    )

    try:
        result_details: PlaceOrderResponse = await api_client.place_order(order_req)
        if result_details and result_details.success and result_details.order_id is not None:
            msg = f"Market order placed successfully. Order ID: {result_details.order_id}, Success: {result_details.success}"
            logger.info(f"[Manual Trade] {msg}")
            log_event(TRADE_LOG_PATH, {"event": "manual_market_order_placed", "params": params.dict(by_alias=True), "result": result_details.dict(by_alias=True)})
            background_tasks.add_task(ensure_market_stream_for_contract, params.contract_id)
            return {"success": True, "message": msg, "details": result_details.dict(by_alias=True)}
        else:
            err_msg = result_details.error_message if result_details else "Market order placement failed or no order ID returned."
            logger.error(f"[Manual Trade] {err_msg}. API Response: {result_details.dict(by_alias=True) if result_details else 'No response object'}")
            log_event(ALERT_LOG_PATH, {"event": "manual_market_order_failed", "params": params.dict(by_alias=True), "error": err_msg, "details": result_details.dict(by_alias=True) if result_details else None})
            return {"success": False, "message": err_msg, "details": result_details.dict(by_alias=True) if result_details else None}
    except Exception as e:
        logger.error(f"[Manual Trade] Exception placing market order: {e}", exc_info=True)
        log_event(ALERT_LOG_PATH, {"event": "manual_market_order_exception", "params": params.dict(by_alias=True), "error": str(e)})
        return {"success": False, "message": f"An exception occurred: {str(e)}"}

@app.post("/manual/trailing_stop_order", summary="Place a manual trailing stop order")
async def post_manual_trailing_stop_order(params: ManualTradeParams, background_tasks: BackgroundTasks):
    if not api_client: 
        return {"success": False, "message": "APIClient not initialized."}
    
    logger.info(f"[Manual Trade] Received trailing stop order request: {params.dict(by_alias=True)}")

    if params.trailingDistance is None or params.trailingDistance <= 0:
        return {"success": False, "message": "Trailing distance must be a positive value for a trailing stop order."}

    order_side_int: int
    if params.side.lower() == "long":
        order_side_int = OrderSide.Bid.value
    elif params.side.lower() == "short":
        order_side_int = OrderSide.Ask.value
    else:
        return {"success": False, "message": f"Invalid side parameter: {params.side}. Expected 'long' or 'short'."}

    order_req = OrderRequest( 
        account_id=params.account_id,
        contract_id=params.contract_id,
        size=params.size,
        side=order_side_int, # Use integer
        type=OrderType.TrailingStop.value, # Use integer
        trail_price=params.trailingDistance 
    )

    try:
        result_details: PlaceOrderResponse = await api_client.place_order(order_req)
        if result_details and result_details.success and result_details.order_id is not None:
            msg = f"Trailing stop order placed successfully. Order ID: {result_details.order_id}, Success: {result_details.success}"
            logger.info(f"[Manual Trade] {msg}")
            log_event(TRADE_LOG_PATH, {"event": "manual_trailing_stop_placed", "params": params.dict(by_alias=True), "result": result_details.dict(by_alias=True)})
            background_tasks.add_task(ensure_market_stream_for_contract, params.contract_id)
            return {"success": True, "message": msg, "details": result_details.dict(by_alias=True)}
        else:
            err_msg = result_details.error_message if result_details else "Trailing stop order placement failed or no order ID returned."
            logger.error(f"[Manual Trade] {err_msg}. API Response: {result_details.dict(by_alias=True) if result_details else 'No response object'}")
            log_event(ALERT_LOG_PATH, {"event": "manual_trailing_stop_failed", "params": params.dict(by_alias=True), "error": err_msg, "details": result_details.dict(by_alias=True) if result_details else None})
            return {"success": False, "message": err_msg, "details": result_details.dict(by_alias=True) if result_details else None}
    except Exception as e:
        logger.error(f"[Manual Trade] Exception placing trailing stop order: {e}", exc_info=True)
        log_event(ALERT_LOG_PATH, {"event": "manual_trailing_stop_exception", "params": params.dict(by_alias=True), "error": str(e)})
        return {"success": False, "message": f"An exception occurred: {str(e)}"}

@app.post("/manual/cancel_all_orders", summary="Cancel all open orders for an account (Simulated)")
async def post_manual_cancel_all_orders(params: AccountActionParams):
    if not api_client:
        return {"success": False, "message": "APIClient not initialized."}

    logger.info(f"[Manual Action] Received Cancel All Orders request for account: {params.account_id}")
    cancelled_count = 0
    errors = []

    try:
        # In a real implementation, get_open_orders would fetch from the API.
        # Here, it uses the simulated version which currently returns [].
        open_orders = await api_client.get_open_orders(params.account_id)

        if not open_orders:
            logger.info(f"[Manual Action] No open orders found to cancel for account {params.account_id} (or simulation returned empty).")
            return {"success": True, "message": "No open orders found to cancel (or simulation returned empty).", "cancelled_count": 0, "errors": []}

        for order in open_orders:
            try:
                # The api_client.cancel_order expects order_id and account_id.
                # OpenOrderSchema has id and accountId.
                success = await api_client.cancel_order(order_id=order.id, account_id=order.account_id)
                if success:
                    logger.info(f"[Manual Action] Order {order.id} for account {order.account_id} cancelled successfully.")
                    cancelled_count += 1
                else:
                    logger.warning(f"[Manual Action] Failed to cancel order {order.id} for account {order.account_id}.")
                    errors.append(f"Failed to cancel order {order.id}")
            except Exception as e_cancel:
                logger.error(f"[Manual Action] Exception cancelling order {order.id}: {e_cancel}", exc_info=True)
                errors.append(f"Exception cancelling order {order.id}: {str(e_cancel)}")

        msg = f"Cancel All Orders for account {params.account_id} processed. Cancelled: {cancelled_count}. Errors: {len(errors)}."
        log_event(ALERT_LOG_PATH, {"event": "manual_cancel_all_processed", "params": params.dict(by_alias=True), "cancelled_count": cancelled_count, "errors": errors})
        return {"success": True, "message": msg, "cancelled_count": cancelled_count, "errors": errors}

    except Exception as e:
        logger.error(f"[Manual Action] Exception in Cancel All Orders for account {params.account_id}: {e}", exc_info=True)
        log_event(ALERT_LOG_PATH, {"event": "manual_cancel_all_exception", "params": params.dict(by_alias=True), "error": str(e)})
        return {"success": False, "message": f"An exception occurred: {str(e)}"}

@app.post("/manual/flatten_all_trades", summary="Flatten all open positions for an account")
async def post_manual_flatten_all_trades(params: AccountActionParams):
    if not api_client: 
        return {"success": False, "message": "APIClient not initialized."}
    
    logger.info(f"[Manual Action] Received Flatten All Trades request for account: {params.account_id}")
    flattened_count = 0
    errors = []
    try:
        positions = await api_client.get_positions(params.account_id)
        if not positions:
            logger.info(f"[Manual Action] No open positions found to flatten for account {params.account_id}.")
            return {"success": True, "message": "No open positions found.", "flattened_count": 0, "errors": []}
        
        for pos in positions: 
            try:
                opposing_side_int: int
                if pos.type == PositionType.Long: 
                    opposing_side_int = OrderSide.Ask.value # Sell to close long
                elif pos.type == PositionType.Short:
                    opposing_side_int = OrderSide.Bid.value  # Buy to close short
                else:
                    logger.warning(f"[Manual Action] Unknown position type for position ID {pos.id}: {pos.type}. Skipping.")
                    errors.append(f"Unknown position type for {pos.contract_id}")
                    continue
                
                order_req = OrderRequest(
                    account_id=pos.account_id, 
                    contract_id=pos.contract_id,
                    size=abs(int(pos.size)), 
                    side=opposing_side_int,    # Use integer
                    type=OrderType.Market.value # Use integer
                )
                logger.info(f"[Manual Action] Attempting to flatten position for {pos.contract_id} (Qty: {pos.size}, Side: {pos.type.name}) with order: {order_req.dict(by_alias=True)}")

                result_details: PlaceOrderResponse = await api_client.place_order(order_req)
                if result_details and result_details.success and result_details.order_id is not None:
                    logger.info(f"[Manual Action] Flatten order for {pos.contract_id} placed. Order ID: {result_details.order_id}")
                    flattened_count += 1
                else:
                    err_msg = result_details.error_message if result_details else f"Failed to place flatten order for {pos.contract_id}."
                    logger.warning(f"[Manual Action] {err_msg}. API Response: {result_details.dict(by_alias=True) if result_details else 'No response object'}")
                    errors.append(err_msg)
            except Exception as e_flatten:
                logger.error(f"[Manual Action] Exception flattening position for {pos.contract_id}: {e_flatten}", exc_info=True)
                errors.append(f"Exception flattening {pos.contract_id}: {str(e_flatten)}")

        msg = f"Flatten All Trades for account {params.account_id} processed. Flatten orders placed: {flattened_count}. Errors: {len(errors)}."
        log_event(ALERT_LOG_PATH, {"event": "manual_flatten_all_processed", "params": params.dict(by_alias=True), "flattened_count": flattened_count, "errors": errors})
        return {"success": True, "message": msg, "flattened_count": flattened_count, "errors": errors}
    except Exception as e:
        logger.error(f"[Manual Action] Exception in Flatten All Trades for account {params.account_id}: {e}", exc_info=True)
        log_event(ALERT_LOG_PATH, {"event": "manual_flatten_all_exception", "params": params.dict(by_alias=True), "error": str(e)})
        return {"success": False, "message": f"An exception occurred: {str(e)}"}
        
# Removed save_trade_states as trade_states is in-memory; persistence would need more robust handling

@app.get("/check_trade_status")
async def check_trade_status_endpoint():
    # This endpoint needs to iterate over all strategies in trade_states or take a strategy_name param
    active_trades_summary = []
    for strategy_name, state in trade_states.items():
        if state.get("trade_active") and state.get("current_trade"):
            await check_and_close_active_trade(strategy_name) # This will check PnL targets
            current_trade_obj: Trade = state["current_trade"]
            active_trades_summary.append({
                "strategy": strategy_name,
                "status": "checked_still_active" if state.get("trade_active") else "checked_now_closed",
                "entry_price": current_trade_obj.entry_price,
                "signal": current_trade_obj.direction,
                "order_id": current_trade_obj.order_id
            })
    if not active_trades_summary:
        return {"status": "no_active_trades_found"}
    return {"active_trades_status": active_trades_summary}


@app.get("/live_feed", response_class=HTMLResponse)
async def live_feed_page():
    # This needs to be adapted based on how new stream handlers store data
    # For now, using the raw latest_market_quotes, trades, depth
    # Consider formatting them better if they are complex objects/lists

    def format_data(item_list, event_type):
        html_rows = ""
        for item_group in reversed(item_list[-20:]): # Show last 20 groups of events
            for item in item_group: # Iterate if item_group is a list of multiple events
                 html_rows += f"<tr><td>{event_type}</td><td>{json.dumps(item, indent=2)}</td></tr>"
        return html_rows

    quote_rows = format_data(latest_market_quotes, "Quote")
    trade_rows = format_data(latest_market_trades, "Trade")
    depth_rows = format_data(latest_market_depth, "Depth")

    return HTMLResponse(f"""
    <html><head><title>Live Market Feed</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Live Market Feed (latest events)</h1>
    <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    <h2>Quotes</h2><table><thead><tr><th>Type</th><th>Data</th></tr></thead><tbody>{quote_rows}</tbody></table>
    <h2>Trades</h2><table><thead><tr><th>Type</th><th>Data</th></tr></thead><tbody>{trade_rows}</tbody></table>
    <h2>Depth</h2><table><thead><tr><th>Type</th><th>Data</th></tr></thead><tbody>{depth_rows}</tbody></table>
    </div></body></html>""")

@app.get("/status_ui", response_model=StatusResponse) # Renamed to avoid conflict with /status used by other things
async def get_status_endpoint():
    # This needs to reflect the currently selected strategy in the UI (current_strategy_name)
    # and its specific state from trade_states
    strategy_state = trade_states.get(current_strategy_name, {})
    active_trade_obj: Optional[Trade] = strategy_state.get("current_trade")

    return StatusResponse(
        trade_active=strategy_state.get("trade_active", False),
        entry_price=active_trade_obj.entry_price if active_trade_obj else None,
        trade_time=active_trade_obj.entry_time.isoformat() if active_trade_obj and active_trade_obj.entry_time else None,
        current_signal=active_trade_obj.direction if active_trade_obj else None,
        daily_pnl=strategy_state.get("daily_pnl", 0.0),
        current_strategy_name=current_strategy_name,
        active_strategy_config=strategies.get(current_strategy_name, {})
    )

@app.get("/debug_account_info")
async def debug_account_info():
    if not api_client:
        return {"error": "APIClient not initialized"}
    try:
        # Token handled by APIClient
        accounts_data = await api_client.get_accounts(only_active=False)
        contracts_data = await api_client.search_contracts(search_text="ENQ", live=True) # Example search

        return {
            "session_token_active": bool(api_client._session_token), # Just indicate if token exists
            "accounts": [acc.dict(by_alias=True) for acc in accounts_data],
            "sample_contract_search_ENQ": [c.dict(by_alias=True) for c in contracts_data]
        }
    except Exception as e:
        logger.error(f"Error in /debug_account_info: {e}", exc_info=True)
        return {"error": str(e)}

# --- UI Endpoints (largely unchanged but rely on global state that's now per-strategy) ---
# These need careful review to correctly display per-strategy status from trade_states

@app.get("/", response_class=HTMLResponse)
async def config_dashboard_with_selection_get(strategy_selected: str = None): #This is one of the / endpoint
    global current_strategy_name, active_strategy_config # active_strategy_config is legacy global
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    active_strategy_config = strategies[current_strategy_name] # Update global legacy one too for now
    return await config_dashboard_page()


@app.get("/admin/clear_logs")
async def clear_logs():
    try:
        for f_name in [TRADE_LOG_PATH, ALERT_LOG_PATH]: # Use defined paths
            if os.path.exists(f_name):
                os.remove(f_name)
        return {"status": "success", "message": "Log files deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/contract_search", response_class=HTMLResponse)
async def contract_search_page(search_query: str = ""):
    results_html = ""
    if search_query and api_client:
        try:
            # Assuming APIClient has search_contracts method
            contracts_result = await api_client.search_contracts(search_text=search_query, live=False)
            for contract in contracts_result: # Iterate over List[Contract]
                results_html += f"""
                <tr>
                    <td>{contract.id}</td>
                    <td>{contract.name}</td>
                    <td>{contract.description or ""}</td>
                    <td>{contract.tick_size}</td>
                    <td>{contract.tick_value}</td>
                    <td><button onclick="copyToConfig('{contract.id}')">Copy</button></td>
                </tr>
                """
        except Exception as e:
            results_html = f"<tr><td colspan='6'>Error: {str(e)}</td></tr>"
    elif not api_client:
        results_html = "<tr><td colspan='6'>API Client not initialized.</td></tr>"

    # ... (rest of HTML for contract search page remains largely same)
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
            // This JS needs to target the correct input field, assuming it's still 'projectx_contract_id'
            const input = document.getElementById('projectx_contract_id');
            if (input) {{
                input.value = contractId;
                alert("Contract ID copied to config input.");
            }} else {{
                // If the input ID changed or is dynamic based on selected strategy, this needs update
                alert("ProjectX Contract ID input field not found in the strategy config form.");
            }}
        }}
    </script>
    </body></html>"""
    return HTMLResponse(content=html)
    
@app.head("/", response_class=HTMLResponse) # This is another / endpoint
async def config_dashboard_with_selection_head(strategy_selected: str = None):
    global current_strategy_name
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    return await config_dashboard_page()


async def config_dashboard_page():
    global current_strategy_name # This is the strategy selected in the UI
    strategy_options_html = "".join([f'<option value="{name}" {"selected" if name == current_strategy_name else ""}>{name}</option>' for name in strategies])
    
    strategy_rows_html = ""
    for name in strategies:
        delete_action = f"<a href='/delete_strategy_action?strategy_to_delete={name}' class='button-link delete-button' style='margin-right: 5px;'>Delete</a>" if name != "default" else "<span>N/A (Default)</span>"
        clone_action = f"<a href='/clone_strategy_action?strategy_to_clone={name}' class='button-link clone-button'>Clone</a>"
        strategy_rows_html += f"<tr><td>{name}</td><td>{delete_action} {clone_action}</td></tr>"

    # Config displayed is for the strategy selected in UI
    displayed_config = strategies.get(current_strategy_name, strategies.get("default", {}))

    # Status display needs to fetch from trade_states for the current_strategy_name
    strategy_runtime_state = trade_states.get(current_strategy_name, {})
    active_trade_for_current_strategy: Optional[Trade] = strategy_runtime_state.get("current_trade")

    trade_active_display = strategy_runtime_state.get('trade_active', False)
    entry_price_display = active_trade_for_current_strategy.entry_price if active_trade_for_current_strategy else "None"
    trade_time_display = active_trade_for_current_strategy.entry_time.isoformat() if active_trade_for_current_strategy and active_trade_for_current_strategy.entry_time else "None"
    current_signal_display = active_trade_for_current_strategy.direction if active_trade_for_current_strategy else "None"
    daily_pnl_display = f"{strategy_runtime_state.get('daily_pnl', 0.0):.2f}"
    # 'strategy_that_opened_trade' is now implicit by looking at current_strategy_name's state
    trade_opened_by_display = current_strategy_name if trade_active_display else "N/A"


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

        <h2>Live Bot Status (Strategy: {current_strategy_name})</h2>
        <div class="status-section" id="status-container">
            <p><strong>Trade Active:</strong> {trade_active_display}</p>
            <p><strong>Entry Price:</strong> {entry_price_display}</p>
            <p><strong>Trade Time (UTC):</strong> {trade_time_display}</p>
            <p><strong>Current Signal Direction:</strong> {current_signal_display}</p>
            <p><strong>Daily PnL for {current_strategy_name}:</strong> {daily_pnl_display}</p>
            <p><a href="/check_trade_status?strategy_name={current_strategy_name}" class="button-link">Manually Check Active Trade for {current_strategy_name}</a></p>
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


@app.post("/update_strategy_config")
async def update_strategy_config_action(
    strategy_name_to_update: str = Form(...),
    contract_symbol: str = Form(...),
    projectx_contract_id: str = Form(...),
    trade_size: int = Form(...),
    max_trade_loss: float = Form(...),
    max_trade_profit: float = Form(...),
    max_daily_loss: float = Form(...),
    max_daily_profit: float = Form(...)
):
    global current_strategy_name # Keep this global for UI selection
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
        save_strategies_to_file() # Use the correct function name
        current_strategy_name = strategy_name_to_update
    return RedirectResponse(url=f"/?strategy_selected={strategy_name_to_update}", status_code=303)

@app.post("/create_new_strategy_action")
async def create_new_strategy_action_endpoint(new_strategy_name_input: str = Form(...)):
    global current_strategy_name
    if new_strategy_name_input and new_strategy_name_input.strip() and new_strategy_name_input not in strategies:
        new_name = new_strategy_name_input.strip()
        strategies[new_name] = dict(strategies["default"])
        trade_states[new_name] = {"trade_active": False, "daily_pnl": 0.0, "current_trade": None, "trading_halted_today": False} # Initialize state
        save_strategies_to_file()
        current_strategy_name = new_name
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/delete_strategy_action")
async def delete_strategy_action_endpoint(strategy_to_delete: str):
    global current_strategy_name
    if strategy_to_delete in strategies and strategy_to_delete != "default":
        del strategies[strategy_to_delete]
        if strategy_to_delete in trade_states: # Also remove its runtime state
            del trade_states[strategy_to_delete]
        save_strategies_to_file()
        if current_strategy_name == strategy_to_delete:
            current_strategy_name = "default"
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/clone_strategy_action")
async def clone_strategy_action_endpoint(strategy_to_clone: str):
    global current_strategy_name
    if strategy_to_clone in strategies:
        new_name_base = f"{strategy_to_clone}_copy"
        new_name = new_name_base
        count = 1
        while new_name in strategies:
            new_name = f"{new_name_base}{count}"
            count += 1
        strategies[new_name] = dict(strategies[strategy_to_clone])
        trade_states[new_name] = {"trade_active": False, "daily_pnl": 0.0, "current_trade": None, "trading_halted_today": False} # Initialize state for clone
        save_strategies_to_file()
        current_strategy_name = new_name
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/manual_trade", response_class=HTMLResponse)
async def manual_trade_page():
    # COMMON_CSS should be defined globally in main.py already
    html_content = f"""
    <html>
    <head>
        <title>Manual Trade Entry</title>
        {COMMON_CSS}
        <style>
            .form-grid {{
                display: grid;
                grid-template-columns: auto 1fr;
                gap: 10px 20px;
                align-items: center;
            }}
            .form-grid label {{
                text-align: right;
            }}
            .button-group button {{
                margin-right: 10px;
                margin-bottom: 10px; /* For wrapping */
            }}
            #manual_trade_response {{
                margin-top: 20px;
                padding: 10px;
                border: 1px solid #434651;
                border-radius: 4px;
                background-color: #2A2E39;
                white-space: pre-wrap; /* To respect newlines in the response */
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Manual Trade Entry</h1>
            <p><a href="/dashboard_menu_page" class="button-link">Back to Menu</a></p>

            <form id="manual_trade_form">
                <div class="form-grid">
                    <label for="accountId">Account ID:</label>
                    <input type="number" id="accountId" name="accountId" required>

                    <label for="contractId">Contract ID:</label>
                    <input type="text" id="contractId" name="contractId" required placeholder="e.g., CON.F.US.ENQ.M25">

                    <label for="side">Side:</label>
                    <select id="side" name="side">
                        <option value="long">Long</option>
                        <option value="short">Short</option>
                    </select>

                    <label for="size">Size (Contracts):</label>
                    <input type="number" id="size" name="size" min="1" value="1" required>

                    <label for="trailingDistance">Trailing Stop (Ticks, for Trailing Stop Order):</label>
                    <input type="number" id="trailingDistance" name="trailingDistance" min="0">
                </div>
            </form>

            <div class="button-group" style="margin-top: 20px;">
                <button type="button" onclick="submitManualOrder('market')">Place Market Order</button>
                <button type="button" onclick="submitManualOrder('trailing_stop')">Place Trailing Stop Order</button>
                <button type="button" onclick="submitAccountAction('cancel_all')">Cancel All Orders (Account)</button>
                <button type="button" onclick="submitAccountAction('flatten_all')">Flatten All Trades (Account)</button>
            </div>

            <h2>Response:</h2>
            <pre id="manual_trade_response">No action taken yet.</pre>
        </div>

        <script>
            async function submitManualOrder(orderType) {{
                const form = document.getElementById('manual_trade_form');
                const responseArea = document.getElementById('manual_trade_response');

                const formData = {{
                    accountId: parseInt(form.elements.accountId.value),
                    contractId: form.elements.contractId.value,
                    side: form.elements.side.value,
                    size: parseInt(form.elements.size.value),
                    trailingDistance: form.elements.trailingDistance.value ? parseInt(form.elements.trailingDistance.value) : null
                }};

                if (!formData.accountId || !formData.contractId || !formData.side || !formData.size) {{
                    responseArea.textContent = "Error: Account ID, Contract ID, Side, and Size are required.";
                    return;
                }}
                if (orderType === 'trailing_stop' && (!formData.trailingDistance || formData.trailingDistance <= 0)) {{
                    responseArea.textContent = "Error: Trailing Stop Ticks must be a positive number for a trailing stop order.";
                    return;
                }}

                let endpoint = '';
                if (orderType === 'market') {{
                    endpoint = '/manual/market_order';
                }} else if (orderType === 'trailing_stop') {{
                    endpoint = '/manual/trailing_stop_order';
                }} else {{
                    responseArea.textContent = 'Error: Unknown order type.';
                    return;
                }}

                responseArea.textContent = 'Submitting ' + orderType + ' order...';

                try {{
                    const response = await fetch(endpoint, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(formData)
                    }});
                    const result = await response.json();
                    responseArea.textContent = JSON.stringify(result, null, 2);
                }} catch (error) {{
                    console.error('Error submitting manual order:', error);
                    responseArea.textContent = 'Error: ' + error.message;
                }}
            }}

            async function submitAccountAction(actionType) {{
                const form = document.getElementById('manual_trade_form'); // Get accountId from the main form
                const responseArea = document.getElementById('manual_trade_response');

                const accountIdValue = form.elements.accountId.value;
                if (!accountIdValue) {{
                    responseArea.textContent = "Error: Account ID is required for this action.";
                    return;
                }}
                const accountData = {{ accountId: parseInt(accountIdValue) }};

                let endpoint = '';
                let actionName = '';
                if (actionType === 'cancel_all') {{
                    endpoint = '/manual/cancel_all_orders';
                    actionName = 'Cancel All Orders';
                }} else if (actionType === 'flatten_all') {{
                    endpoint = '/manual/flatten_all_trades';
                    actionName = 'Flatten All Trades';
                }} else {{
                    responseArea.textContent = 'Error: Unknown account action.';
                    return;
                }}

                responseArea.textContent = 'Submitting ' + actionName + ' for account ' + accountData.accountId + '...';

                try {{
                    const response = await fetch(endpoint, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(accountData)
                    }});
                    const result = await response.json();
                    responseArea.textContent = JSON.stringify(result, null, 2);
                }} catch (error) {{
                    console.error('Error submitting account action:', error);
                    responseArea.textContent = 'Error: ' + error.message;
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
    
@app.get("/dashboard_menu_page", response_class=HTMLResponse)
async def dashboard_menu_html_page():
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
            <li><a href="/debug_account_info">Debug Account Info</a></li>
            <li><a href="/live_feed">Live Market Feed</a></li>
            <li><a href="/manual_trade">Manual Trade Entry</a></li>
        </ul>
    </div></body></html>""")

bot_trading_enabled = True # Global toggle for all strategies; could be per-strategy too

@app.get("/toggle_trading_status", response_class=HTMLResponse)
async def toggle_trading_view():
    global bot_trading_enabled
    status_message = "ENABLED" if bot_trading_enabled else "DISABLED"
    button_text = "Disable Trading (Global)" if bot_trading_enabled else "Enable Trading (Global)"
    return HTMLResponse(f"""
    <html><head><title>Toggle Trading</title>{COMMON_CSS}</head><body><div class="container">
        <h1>Toggle Bot Trading (Global)</h1>
        <p>Current Bot Trading Status: <strong>{status_message}</strong></p>
        <p>Note: This is a global toggle. Individual strategy PnL limits still apply.</p>
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
    logger.info(f"Global bot trading status changed to: {status_message}")
    log_event(ALERT_LOG_PATH, {"event": "Global Trading Status Changed", "status": status_message})
    return RedirectResponse(url="/toggle_trading_status", status_code=303)

@app.get("/logs/trades", response_class=HTMLResponse)
async def trade_log_view_page():
    # This log view remains global, showing trades from all strategies
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "r") as f:
            try:
                trades_log_entries = json.load(f)
            except json.JSONDecodeError:
                trades_log_entries = []
    else:
        trades_log_entries = []

    summary_by_strategy = {}
    # Filter for actual PnL events (e.g., "exit_filled_userhub", "exit_forced_daily_limit")
    for t_entry in trades_log_entries:
        if t_entry.get("event", "").startswith("exit"): # Consider all exit events for PnL
            strat = t_entry.get("strategy", "Unknown")
            summary = summary_by_strategy.setdefault(strat, {
                "wins": 0, "losses": 0, "total_pnl": 0, "count": 0,
                "best": float('-inf'), "worst": float('inf')
            })
            pnl = t_entry.get("pnl", 0.0)
            if pnl is not None: # Ensure PnL is present
                summary["wins"] += 1 if pnl > 0 else 0
                summary["losses"] += 1 if pnl <= 0 else 0 # Count 0 PnL as loss or neutral
                summary["total_pnl"] += pnl
                summary["count"] += 1
                summary["best"] = max(summary["best"] if summary["best"] != float('-inf') else pnl, pnl)
                summary["worst"] = min(summary["worst"] if summary["worst"] != float('inf') else pnl, pnl)

    summary_html = "<h2>Performance Summary by Strategy</h2>"
    for s, d in summary_by_strategy.items():
        avg_pnl_html = f"${d['total_pnl']/d['count']:.2f}" if d['count'] > 0 else "N/A"
        best_pnl_html = f"${d['best']:.2f}" if d['best'] != float('-inf') else "N/A"
        worst_pnl_html = f"${d['worst']:.2f}" if d['worst'] != float('inf') else "N/A"
        summary_html += (
            f"<h3>{s}</h3><ul>"
            f"<li>Total Trades Logged (Exits): {d['count']}</li>"
            f"<li>Wins: {d['wins']} | Losses: {d['losses']}</li>"
            f"<li>Total PnL: ${d['total_pnl']:.2f}</li>"
            f"<li>Average PnL per Trade: {avg_pnl_html}</li>"
            f"<li>Best Trade: {best_pnl_html} | Worst Trade: {worst_pnl_html}</li></ul>"
        )
    if not summary_by_strategy:
        summary_html += "<p>No completed trades with PnL found in logs.</p>"


    rows_html = "".join([
        f"<tr><td>{t.get('timestamp','')}</td><td>{t.get('strategy','')}</td><td>{t.get('event','')}</td>"
        f"<td>{t.get('projectx_order_id', t.get('orderId',''))}</td>"
        f"<td>{t.get('entry_price','')}</td><td>{t.get('exit_price','')}</td><td>{t.get('pnl','')}</td>"
        f"<td>{t.get('signal','')}</td><td>{t.get('ticker','')}</td></tr>"
        for t in reversed(trades_log_entries) # Show all log entries
    ])
    # ... (rest of HTML for trade log page)
    return HTMLResponse(f"""
    <html><head><title>Trade History</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Trade History</h1>
    <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    {summary_html}
    <h2>Detailed Trade Log</h2>
    <a href='/download/trades.csv' class='button-link'>Download CSV</a>
    <table><thead><tr><th>Time</th><th>Strategy</th><th>Event</th><th>Order ID</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Signal</th><th>Ticker</th></tr></thead><tbody>{rows_html}</tbody></table>
    </div></body></html>""")


@app.get("/download/trades.csv")
async def download_trade_log():
    # This CSV download should also reflect the global log
    import io # Ensure io is imported
    if not os.path.exists(TRADE_LOG_PATH):
        return HTMLResponse("Trade log not found", status_code=404)

    with open(TRADE_LOG_PATH, "r") as f:
        try:
            trades_log_content = json.load(f)
        except json.JSONDecodeError:
            trades_log_content = []

    def generate_csv_data():
        output = io.StringIO()
        # Define CSV headers based on common fields in log entries
        fieldnames = ["timestamp", "strategy", "event", "projectx_order_id", "orderId", "entry_price", "exit_price", "pnl", "signal", "ticker", "error", "detail", "response"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore') # Ignore fields not in fieldnames
        writer.writeheader()
        for entry in trades_log_content:
            writer.writerow(entry)
        yield output.getvalue()

    return StreamingResponse(generate_csv_data(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trade_log_full.csv"})

@app.get("/logs/alerts", response_class=HTMLResponse)
async def alert_log_view_page():
    # This log view is global
    if os.path.exists(ALERT_LOG_PATH):
        with open(ALERT_LOG_PATH, "r") as f:
            try:
                alerts = json.load(f)
            except json.JSONDecodeError:
                alerts = []
    else:
        alerts = []

    rows_html = "".join([
        f"<tr><td>{a.get('timestamp','')}</td><td>{a.get('strategy','')}</td><td>{a.get('signal','')}</td><td>{a.get('ticker','')}</td><td>{a.get('event','')}</td><td>{a.get('error',a.get('detail', json.dumps(a.get('response')) if a.get('response') else ''))}</td></tr>"
        for a in reversed(alerts)
    ])
    # ... (rest of HTML for alert log page)
    return HTMLResponse(f"""
    <html><head><title>Alert History</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Alert History</h1>
    <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    <table><thead><tr><th>Time</th><th>Strategy</th><th>Signal</th><th>Ticker</th><th>Event</th><th>Details/Error</th></tr></thead><tbody>{rows_html}</tbody></table>
    </div></body></html>""")

# Ensure ACCOUNT_ID is initialized appropriately, e.g. after APIClient authentication
# The current ACCOUNT_ID = 7715028 is hardcoded. This should ideally be fetched.
# Example: In initialize_topstep_client, after auth, fetch accounts and set ACCOUNT_ID.
# For now, it's used as a global.

# Final review notes:
# - SESSION_TOKEN is still globally defined but should be primarily managed and used by APIClient.
# - ACCOUNT_ID is crucial and its initialization needs to be robust (e.g., from user's primary account after login).
# - Global state variables (daily_pnl, trade_active, etc.) have been partially transitioned to per-strategy state
#   in `trade_states`. UI and logic need to consistently use this per-strategy state.
# - Error handling in API calls (place_order_projectx, close_position_projectx) now relies on exceptions from APIClient
#   or returns a dict with "success": False. Calling code should handle this.
# - SignalR client specific code (setupSignalRConnection, etc.) is removed. New streams are used.
# - `check_and_close_active_trade` and `webhook` handler are the core logic adapting to new client and per-strategy state.
# - Many print() statements were used for logging; replaced with logger.info/debug/error.
# - The `trade_event_handler` previously registered with `register_trade_callback` seems to be related to
#   `check_and_close_active_trade`. This link needs to be re-established if market trades should trigger this check.
#   Currently, `handle_market_trade_event` is basic.

logger.info("main.py refactoring complete. Application ready to be started.")
