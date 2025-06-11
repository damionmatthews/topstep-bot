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
    Account, # This is an alias for TradingAccountModel
    Contract, # This is an alias for ContractModel
    OrderRequest, # This is an alias for PlaceOrderRequest
    OrderDetails, # This is an alias for OrderModel
    PlaceOrderResponse, # Direct import
    OrderSide, 
    OrderType, 
    PositionType,
    PositionModel # Added for post_manual_flatten_all_trades
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
CONTRACT_ID = os.getenv("CONTRACT_ID") # Example, ensure this is used as symbol_id if appropriate
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
    "CONTRACT_SYMBOL": "NQ", # User-friendly symbol
    "PROJECTX_CONTRACT_ID": "CON.F.US.ENQ.M25", # Actual symbol_id for API
    "TRADE_SIZE": 1
    }
    }
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)

current_strategy_name = "default"
active_strategy_config = strategies[current_strategy_name] # Legacy global, try to phase out
daily_pnl = 0.0 # This should become per-strategy (partially done in trade_states)
trade_active = False # This should become per-strategy (partially done in trade_states)
entry_price = None # This should become per-strategy (partially done in trade_states)
trade_time = None # This should become per-strategy (partially done in trade_states)
current_signal = None # This should become per-strategy (partially done in trade_states)
current_signal_direction = None # This should become per-strategy (partially done in trade_states)

# --- TOPSTEP CLIENT INITIALIZATION ---
api_client: Optional[APIClient] = None
market_stream: Optional[MarketDataStream] = None
user_stream: Optional[UserHubStream] = None

async def initialize_topstep_client():
    global api_client, market_stream, user_stream, SESSION_TOKEN, ACCOUNT_ID
    logger.info("Initializing Topstep Client...")
    try:
        api_client = APIClient() # Uses env vars TOPSTEP_USERNAME, TOPSTEP_API_KEY
        await api_client.authenticate()
        SESSION_TOKEN = api_client._session_token # Update global if needed for compatibility
        logger.info("APIClient authenticated.")

        # Attempt to set a default ACCOUNT_ID from fetched accounts
        try:
            accs = await api_client.get_accounts() # Uses TradingAccountModel
            if accs:
                ACCOUNT_ID = accs[0].id # Use the first account's ID
                logger.info(f"Default ACCOUNT_ID set to: {ACCOUNT_ID} from fetched accounts.")
            else:
                logger.warning("No accounts found for the user. Global ACCOUNT_ID remains as initially set or default.")
        except Exception as e:
            logger.error(f"Failed to get accounts to set default ACCOUNT_ID: {e}")


        market_stream = MarketDataStream(api_client, on_state_change_callback=handle_market_stream_state_change, on_trade_callback=handle_market_trade_event, on_quote_callback=handle_market_quote_event, on_depth_callback=handle_market_depth_event, debug=True)
        user_stream = UserHubStream(api_client, on_state_change_callback=handle_user_stream_state_change, on_user_trade_callback=handle_user_trade_event_from_stream, on_user_order_callback=handle_user_order_event_from_stream, on_user_position_callback=handle_user_position_event_from_stream, debug=True)
        logger.info("Market and User streams initialized.")

    except AuthenticationError as e:
        logger.error(f"Client Authentication Error: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize Topstep Client: {e}", exc_info=True)

async def start_streams_if_needed():
    global market_stream, user_stream, api_client, ACCOUNT_ID # Ensure ACCOUNT_ID is available
    if not api_client or not api_client._session_token:
        logger.warning("API client not authenticated. Cannot start streams.")
        return

    if not ACCOUNT_ID: # Double check ACCOUNT_ID before stream operations if it's critical
        logger.error("Global ACCOUNT_ID not set. Cannot reliably start streams or operations that require it.")
        # Potentially re-fetch or prompt if ACCOUNT_ID is essential here.

    if market_stream and market_stream.current_state != StreamConnectionState.CONNECTED:
        logger.info("Attempting to start market data stream...")
        contract_id_to_subscribe = active_strategy_config.get("PROJECTX_CONTRACT_ID") # This is symbol_id
        if contract_id_to_subscribe:
            logger.info(f"Market stream start and subscription for {contract_id_to_subscribe} deferred until explicitly needed by a trade.")
        else:
            logger.error("No contract ID (symbol_id) configured in active_strategy_config for market data stream.")

    if user_stream and user_stream.current_state != StreamConnectionState.CONNECTED:
        logger.info("Attempting to start user hub stream...")
        asyncio.create_task(user_stream.start())
        logger.info("User hub stream start initiated.")

def handle_market_stream_state_change(state: StreamConnectionState):
    logger.info(f"Market Stream state changed: {state.value}")
    if state == StreamConnectionState.CONNECTED:
        contract_id_to_subscribe = active_strategy_config.get("PROJECTX_CONTRACT_ID") # This is symbol_id
        if market_stream and contract_id_to_subscribe:
            logger.info(f"Market Stream CONNECTED. Ensuring subscription to {contract_id_to_subscribe}.")
            market_stream.subscribe_contract(contract_id_to_subscribe)

def handle_user_stream_state_change(state: StreamConnectionState):
    logger.info(f"User Hub Stream state changed: {state.value}")

async def ensure_market_stream_for_contract(symbol_id: str): # Renamed contract_id to symbol_id for clarity
    global market_stream
    if not market_stream:
        logger.error("MarketDataStream is not initialized. Cannot ensure stream for contract.")
        return

    if market_stream.current_state != StreamConnectionState.CONNECTED:
        logger.info(f"MarketDataStream not connected. Attempting to start for symbol {symbol_id}...")
        try:
            success = await market_stream.start()
            if not success:
                logger.error(f"Failed to start MarketDataStream for symbol {symbol_id}.")
                return
            await asyncio.sleep(1) 
        except Exception as e:
            logger.error(f"Exception starting MarketDataStream for {symbol_id}: {e}", exc_info=True)
            return

    if market_stream.current_state == StreamConnectionState.CONNECTED:
        logger.info(f"Ensuring subscription to {symbol_id} on MarketDataStream.")
        market_stream.subscribe_contract(symbol_id)
    else:
        logger.warning(f"MarketDataStream still not connected after start attempt. Cannot subscribe to {symbol_id}.")

async def manage_market_stream_after_trade_close(closed_trade_symbol_id: str): # Renamed for clarity
    global market_stream, trade_states
    if not market_stream:
        logger.warning("MarketDataStream not initialized, cannot manage after trade close.")
        return

    if not closed_trade_symbol_id or closed_trade_symbol_id == "UNKNOWN_CONTRACT": # Assuming "UNKNOWN_CONTRACT" is a placeholder
        logger.warning(f"Invalid or unknown symbol ID ('{closed_trade_symbol_id}') for closed trade. Cannot manage market stream subscriptions.")
        return

    is_contract_still_active = False
    for strategy_name, state in trade_states.items():
        if state.get("trade_active"):
            current_trade_in_state: Optional[Trade] = state.get("current_trade")
            if current_trade_in_state and hasattr(current_trade_in_state, 'symbol_id') and current_trade_in_state.symbol_id == closed_trade_symbol_id:
                is_contract_still_active = True
                break
    
    if not is_contract_still_active:
        logger.info(f"No other active trades for {closed_trade_symbol_id}. Unsubscribing from MarketDataStream.")
        if market_stream.current_state == StreamConnectionState.CONNECTED:
            market_stream.unsubscribe_contract(closed_trade_symbol_id)
            await asyncio.sleep(0.5)
        else:
            if hasattr(market_stream, '_subscriptions') and market_stream._subscriptions is not None and closed_trade_symbol_id in market_stream._subscriptions:
                 market_stream._subscriptions.remove(closed_trade_symbol_id)
            logger.info(f"Market stream not connected, noted unsubscription for {closed_trade_symbol_id}.")

        if hasattr(market_stream, '_subscriptions') and not market_stream._subscriptions:
            logger.info("No active market data subscriptions left. Stopping MarketDataStream.")
            await market_stream.stop()
    else:
        logger.info(f"Symbol {closed_trade_symbol_id} is still active in other trades. Market stream subscription retained.")

async def stop_all_streams():
    global market_stream, user_stream
    if market_stream and market_stream.current_state != StreamConnectionState.DISCONNECTED:
        logger.info("Stopping market stream...")
        await market_stream.stop()
    if user_stream and user_stream.current_state != StreamConnectionState.DISCONNECTED:
        logger.info("Stopping user hub stream...")
        await user_stream.stop()
    logger.info("All streams stopped.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

latest_market_quotes: List[Any] = []
latest_market_trades: List[Any] = []
latest_market_depth: List[Any] = []

def handle_market_quote_event(data: List[Any]):
    global latest_market_quotes
    logger.debug(f"[MarketStream] Quote Event Data: {data}")
    latest_market_quotes.extend(data)
    if len(latest_market_quotes) > 100: latest_market_quotes = latest_market_quotes[-100:]

async def trade_event_handler(trade_event_data: Any):
    global trade_states
    logger.debug(f"trade_event_handler received: {trade_event_data}")
    active_strategies_to_check = [name for name, state in trade_states.items() if state.get("trade_active") and state.get("current_trade")]
    for strategy_name in active_strategies_to_check:
        logger.debug(f"Checking active trade for strategy '{strategy_name}' due to market trade event.")
        try:
            await check_and_close_active_trade(strategy_name)
        except Exception as e:
            logger.error(f"Error in check_and_close_active_trade for strategy {strategy_name} (triggered by market event): {e}", exc_info=True)

def handle_market_trade_event(data: List[Any]):
    global latest_market_trades, background_loop
    logger.debug(f"[MarketStream] Batch of Trade Event Data received, count: {len(data)}")
    latest_market_trades.extend(data)
    if len(latest_market_trades) > 100: latest_market_trades = latest_market_trades[-100:]
    for trade_event_item in data:
        asyncio.run_coroutine_threadsafe(trade_event_handler(trade_event_item), background_loop)

def handle_market_depth_event(data: List[Any]):
    global latest_market_depth
    logger.debug(f"[MarketStream] Depth Event Data: {data}")
    latest_market_depth.extend(data)
    if len(latest_market_depth) > 100: latest_market_depth = latest_market_depth[-100:]

def handle_user_trade_event_from_stream(data: List[Any]):
    logger.info(f"[UserStream] User Trade Event Data: {data}")
    for trade_event in data:
        handle_user_trade(trade_event)

def handle_user_order_event_from_stream(data: List[Any]):
    logger.info(f"[UserStream] User Order Event Data: {data}")

def handle_user_position_event_from_stream(data: List[Any]):
    logger.info(f"[UserStream] User Position Event Data: {data}")

def fetch_latest_quote(): return latest_market_quotes[-10:] if latest_market_quotes else []
def fetch_latest_trade(): return latest_market_trades[-10:] if latest_market_trades else []
def fetch_latest_depth(): return latest_market_depth[-10:] if latest_market_depth else []

@app.on_event("startup")
async def startup_event():
    await initialize_topstep_client()
    await start_streams_if_needed()

@app.on_event("shutdown")
async def shutdown_event():
    await stop_all_streams()
    if api_client:
        await api_client.close()

@app.get("/status")
async def status_endpoint_old():
    return {"latest_quote": fetch_latest_quote(), "latest_trade": fetch_latest_trade(), "latest_depth": fetch_latest_depth()}

class SignalAlert(BaseModel):
    signal: str
    ticker: Optional[str] = "NQ" # This should be symbol_id
    time: Optional[str] = None
    order_type: str = Field(default="market", alias="orderType")
    limit_price: Optional[float] = Field(default=None, alias="limitPrice")
    stop_price: Optional[float] = Field(default=None, alias="stopPrice")
    trailing_distance: Optional[float] = Field(default=None, alias="trailingDistance") # Changed from trailingDistance: Optional[int]
    quantity: Optional[int] = Field(default=None, alias="qty")
    account_id: int = Field(alias="accountId")

class StatusResponse(BaseModel):
    trade_active: bool
    entry_price: Optional[float] = None
    trade_time: Optional[str] = None
    current_signal: Optional[str] = None
    daily_pnl: float
    current_strategy_name: str
    active_strategy_config: dict

class ManualTradeParams(BaseModel):
    account_id: int = Field(..., alias="accountId")
    contract_id: str = Field(..., alias="contractId") # UI sends contractId, map to symbol_id in endpoint
    side: str
    size: int # UI sends size, map to position_size in endpoint
    trailingDistance: Optional[float] = Field(default=None, alias="trailingDistance") # UI sends trailingDistance, map to trail_distance

class AccountActionParams(BaseModel):
    account_id: int = Field(..., alias="accountId")

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

# --- ORDER FUNCTIONS (Corrected Versions) ---
async def place_order_projectx(alert: SignalAlert, strategy_cfg: dict):
    global ACCOUNT_ID 
    if not api_client: 
        raise TopstepAPIError("APIClient not initialized")
    
    order_account_id = alert.account_id if alert.account_id else ACCOUNT_ID
    if not order_account_id:
        raise ValueError("Account ID is missing: not globally configured and not in alert.")
    
    # Use 'symbol_id' based on updated PlaceOrderRequest
    order_symbol_id = alert.ticker or strategy_cfg.get("PROJECTX_CONTRACT_ID") 
    if not order_symbol_id: 
        raise ValueError("Symbol ID (from alert.ticker or PROJECTX_CONTRACT_ID) missing.")

    # Use 'position_size' based on updated PlaceOrderRequest
    order_position_size = alert.quantity if alert.quantity is not None else strategy_cfg.get("TRADE_SIZE", 1)
    
    side_str = alert.signal.lower()
    order_side_int: int
    if side_str == "long" or side_str == "buy":
        order_side_int = OrderSide.Bid.value
    elif side_str == "short" or side_str == "sell":
        order_side_int = OrderSide.Ask.value
    else:
        raise ValueError(f"Invalid signal/side from webhook: {alert.signal}")

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
        "symbol_id": order_symbol_id,         # Changed from contract_id
        "position_size": order_position_size, # Changed from size
        "side": order_side_int,
        "type": order_type_int,
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
        if alert.trailing_distance is None or alert.trailing_distance <= 0: # trailing_distance from SignalAlert
            raise ValueError("trailing_distance (as offset) required and must be positive for TrailingStop.")
        # Use 'trail_distance' based on updated PlaceOrderRequest
        pydantic_request_params["trail_distance"] = alert.trailing_distance 
        if alert.stop_price is None: 
             pydantic_request_params["stop_price"] = None # Explicitly set to None if not provided for TS
    
    final_params_for_request = {k: v for k, v in pydantic_request_params.items() if v is not None}

    logger.info(f"[Order Attempt] Constructing PlaceOrderRequest with: {final_params_for_request}")
    # OrderRequest is an alias for PlaceOrderRequest
    order_req = OrderRequest(**final_params_for_request) 

    try:
        result: PlaceOrderResponse = await api_client.place_order(order_req)
        if result.success and result.order_id is not None:
            logger.info(f"✅ Order placed successfully. Order ID: {result.order_id}")
            # When creating Trade object, use order_symbol_id for symbol_id field of Trade
            # Example: state["current_trade"] = Trade(..., symbol_id=order_symbol_id, ...)
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

    order_side_int: int
    if current_active_signal_direction == "long":
        order_side_int = OrderSide.Ask.value 
    else: 
        order_side_int = OrderSide.Bid.value  
        
    order_symbol_id = strategy_cfg.get("PROJECTX_CONTRACT_ID") # This should align with symbolId used in PlaceOrderRequest
    if not order_symbol_id: 
        raise ValueError(f"PROJECTX_CONTRACT_ID (as symbolId) not found for strategy.")

    # OrderRequest is an alias for PlaceOrderRequest
    order_req = OrderRequest(
        account_id=target_account_id, 
        symbol_id=order_symbol_id,      # Changed from contract_id
        position_size=size_to_close,    # Changed from size
        side=order_side_int,    
        type=OrderType.Market.value 
    )
    log_event(ALERT_LOG_PATH, {"event": "Closing Position Attempt", "strategy": strategy_cfg.get("CONTRACT_SYMBOL","N/A"), "payload": order_req.dict(by_alias=True)})
    try:
        response: PlaceOrderResponse = await api_client.place_order(order_req)
        if response.success and response.order_id is not None:
            logger.info(f"Close position order placed: {response.order_id}")
            return {"success": True, "orderId": response.order_id, "details": response.dict(by_alias=True)}
        else:
            err = response.error_message or "Close position order failed."
            logger.error(f"❌ Close position order failed: {err}. API Response: {response.dict(by_alias=True)}")
            return {"success": False, "errorMessage": err, "details": response.dict(by_alias=True)}
    except Exception as e:
        logger.error(f"Unexpected error closing position: {e}", exc_info=True)
        return {"success": False, "errorMessage": str(e)}

# Define the Trade class
class Trade: 
    def __init__(self, strategy_name: str, order_id: int, direction: str, account_id: int, symbol_id: str, entry_price: Optional[float] = None, size: int = 1): # Changed contract_id to symbol_id
        self.strategy_name = strategy_name
        self.order_id = order_id
        self.account_id = account_id
        self.symbol_id = symbol_id # Changed contract_id to symbol_id
        self.entry_price = entry_price
        self.direction = direction 
        self.size = size
        self.entry_time = datetime.utcnow()
        self.exit_price: Optional[float] = None
        self.pnl: float = 0.0
        self.status: str = "open" 

    def to_dict(self):
        return {
            "strategy_name": self.strategy_name,
            "order_id": self.order_id,
            "account_id": self.account_id,
            "symbol_id": self.symbol_id, # Changed contract_id to symbol_id
            "entry_price": self.entry_price,
            "direction": self.direction,
            "size": self.size,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "status": self.status,
        }

trade_states: dict[str, dict[str, Any]] = {} 

def fetch_current_price(symbol_id: Optional[str] = None) -> Optional[float]: # Renamed contract_id to symbol_id
    if not symbol_id: 
        if latest_market_trades: return latest_market_trades[-1].get("price")
        if latest_market_quotes: return latest_market_quotes[-1][0] if latest_market_quotes[-1] else None
        return None

    for trade_event_list in reversed(latest_market_trades):
        for trade_event in trade_event_list:
             if isinstance(trade_event, dict) and trade_event.get("contractId") == symbol_id and "price" in trade_event: # Market data uses contractId
                return trade_event["price"]
    for quote_event_list in reversed(latest_market_quotes):
        for quote_event in quote_event_list:
            if isinstance(quote_event, list) and len(quote_event) > 2 and quote_event[2] == symbol_id: # Market data uses contractId
                if quote_event[0] is not None and quote_event[1] is not None: return (quote_event[0] + quote_event[1]) / 2
                elif quote_event[0] is not None: return quote_event[0]
                elif quote_event[1] is not None: return quote_event[1]
    return None

def handle_user_trade(trade_data_item: dict): 
    global trade_states 
    try:
        logger.info(f"[UserHubCallback] handle_user_trade processing: {trade_data_item}")
        order_id = trade_data_item.get('orderId')
        price = trade_data_item.get('price')
        status = trade_data_item.get('status') 

        if not order_id:
            logger.warning("[UserHubCallback] No orderId in trade event. Skipping.")
            return

        for strategy_name, state in trade_states.items():
            current_trade_obj: Optional[Trade] = state.get("current_trade")
            if current_trade_obj and current_trade_obj.order_id == order_id:
                if status == "Filled" and price is not None:
                    if current_trade_obj.entry_price is None: 
                        current_trade_obj.entry_price = price
                        current_trade_obj.status = "open_filled" 
                        log_event(TRADE_LOG_PATH, {"event": "entry_filled_userhub", "strategy": strategy_name, "orderId": order_id, "entry_price": price, "timestamp": datetime.utcnow().isoformat()})
                        logger.info(f"[UserHubCallback] Entry price for trade {order_id} (Strat: {strategy_name}) updated to: {price}")
                    elif current_trade_obj.status == "closing": 
                        current_trade_obj.exit_price = price
                        current_trade_obj.status = "closed"
                        pnl_points = (current_trade_obj.exit_price - current_trade_obj.entry_price) * (1 if current_trade_obj.direction == "long" else -1)
                        contract_cfg = strategies.get(strategy_name, {})
                        point_value = 20 if contract_cfg.get("CONTRACT_SYMBOL", "NQ") == "NQ" else 50 
                        pnl_dollars = pnl_points * point_value * current_trade_obj.size
                        current_trade_obj.pnl = pnl_dollars
                        state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl_dollars
                        log_event(TRADE_LOG_PATH, {"event": "exit_filled_userhub", "strategy": strategy_name, "orderId": order_id, "entry_price": current_trade_obj.entry_price, "exit_price": current_trade_obj.exit_price, "pnl": pnl_dollars, "timestamp": datetime.utcnow().isoformat()})
                        logger.info(f"[UserHubCallback] Exit for trade {order_id} (Strat: {strategy_name}). Exit Price: {price}, PnL: {pnl_dollars:.2f}")
                        closed_trade_obj_ref = current_trade_obj 
                        state["trade_active"] = False
                        state["current_trade"] = None 
                        if closed_trade_obj_ref and hasattr(closed_trade_obj_ref, 'symbol_id') and closed_trade_obj_ref.symbol_id: # Check symbol_id
                            logger.info(f"Trade closed for symbol {closed_trade_obj_ref.symbol_id}. Scheduling stream management.") # Use symbol_id
                            asyncio.create_task(manage_market_stream_after_trade_close(closed_trade_obj_ref.symbol_id)) # Pass symbol_id
                        else:
                            logger.warning(f"Closed trade object ({closed_trade_obj_ref.order_id if closed_trade_obj_ref else 'N/A'}) or its symbol_id not available for stream management.")
                elif status == "Closed": 
                    if current_trade_obj.status == "open_filled" and current_trade_obj.exit_price is None:
                        logger.warning(f"[UserHubCallback] Trade {order_id} (Strat: {strategy_name}) 'Closed' without recorded exit. PnL not calculated. Current state: {current_trade_obj.to_dict()}")
    except Exception as e:
        logger.error(f"[UserHubCallback] Trade handler error: {e}", exc_info=True)

async def check_and_close_active_trade(strategy_name_of_trade: str):
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

    current_market_price = fetch_current_price(symbol_id=trade_strategy_cfg.get("PROJECTX_CONTRACT_ID")) # Use symbol_id
    if current_market_price is None:
        logger.warning(f"Could not fetch current market price for {trade_strategy_cfg.get('PROJECTX_CONTRACT_ID')} to check trade.")
        return

    points_pnl = (current_market_price - current_trade_obj.entry_price) * (1 if current_trade_obj.direction == 'long' else -1)
    point_value = 20 if trade_strategy_cfg.get("CONTRACT_SYMBOL", "NQ") == "NQ" else 50
    total_dollar_pnl = points_pnl * point_value * current_trade_obj.size
    exit_reason = None
    if total_dollar_pnl >= trade_strategy_cfg.get("MAX_TRADE_PROFIT", float('inf')): exit_reason = "Max Trade Profit Hit"
    elif total_dollar_pnl <= trade_strategy_cfg.get("MAX_TRADE_LOSS", float('-inf')): exit_reason = "Max Trade Loss Hit"

    if exit_reason:
        logger.info(f"[{strategy_name_of_trade}] Attempting to close trade. Reason: {exit_reason}. Current PnL: ${total_dollar_pnl:.2f}")
        try:
            current_trade_obj.status = "closing"
            close_response = await close_position_projectx(trade_strategy_cfg, current_trade_obj.direction, current_trade_obj.account_id, current_trade_obj.size) # Pass size
            if close_response.get("success"):
                logger.info(f"[{strategy_name_of_trade}] Close order placed. Order ID: {close_response.get('orderId')}. Waiting for fill via UserHub.")
            else:
                current_trade_obj.status = "open_filled" 
                logger.error(f"[{strategy_name_of_trade}] Failed to place close order: {close_response.get('errorMessage')}")
                log_event(ALERT_LOG_PATH, {"event": "Error Closing Position", "strategy": strategy_name_of_trade, "error": close_response.get('errorMessage')})
        except Exception as e:
            current_trade_obj.status = "open_filled" 
            logger.error(f"Error during automated closing of position for {strategy_name_of_trade}: {e}", exc_info=True)
            log_event(ALERT_LOG_PATH, {"event": "Exception Closing Position", "strategy": strategy_name_of_trade, "error": str(e)})

    current_daily_pnl = state.get("daily_pnl", 0.0)
    effective_daily_pnl = current_daily_pnl + total_dollar_pnl
    if effective_daily_pnl >= trade_strategy_cfg.get("MAX_DAILY_PROFIT", float('inf')) or effective_daily_pnl <= trade_strategy_cfg.get("MAX_DAILY_LOSS", float('-inf')):
        limit_type = "MAX_DAILY_PROFIT" if effective_daily_pnl >= trade_strategy_cfg.get("MAX_DAILY_PROFIT", float('inf')) else "MAX_DAILY_LOSS"
        logger.info(f"[{strategy_name_of_trade}] Daily PnL limit ({limit_type}) hit or exceeded with current trade. Effective PnL: ${effective_daily_pnl:.2f}. Limit: {trade_strategy_cfg.get(limit_type)}")
        if state.get("trade_active"): 
            logger.info(f"Forcing close of active trade for strategy {strategy_name_of_trade} due to daily PnL limit.")
            try:
                current_trade_obj.status = "closing" 
                close_response = await close_position_projectx(trade_strategy_cfg, current_trade_obj.direction, current_trade_obj.account_id, current_trade_obj.size) # Pass size
                if close_response.get("success"):
                    logger.info(f"[{strategy_name_of_trade}] Daily limit force-close order placed. Order ID: {close_response.get('orderId')}. Waiting for fill.")
                else:
                    current_trade_obj.status = "open_filled" 
                    logger.error(f"[{strategy_name_of_trade}] Failed to place force-close order for daily limit: {close_response.get('errorMessage')}")
            except Exception as e:
                 current_trade_obj.status = "open_filled" 
                 logger.error(f"Error force-closing position for {strategy_name_of_trade} (daily limit): {e}", exc_info=True)
        state["trading_halted_today"] = True
        logger.info(f"Trading halted for the day for strategy {strategy_name_of_trade} due to PnL limits.")

@app.post("/webhook/{strategy_webhook_name}")
async def receive_alert_strategy(strategy_webhook_name: str, alert: SignalAlert, background_tasks: BackgroundTasks):
    global trade_states, ACCOUNT_ID, strategies 
    log_event(ALERT_LOG_PATH, {"event": "Webhook Received", "strategy": strategy_webhook_name, "signal": alert.signal, "ticker": alert.ticker})
    if strategy_webhook_name not in strategies:
        return {"status": "error", "reason": f"Strategy '{strategy_webhook_name}' not found"}

    strategy_cfg = strategies[strategy_webhook_name]
    state = trade_states.setdefault(strategy_webhook_name, {"trade_active": False, "daily_pnl": 0.0, "current_trade": None, "trading_halted_today": False})
    if state.get("trading_halted_today"):
        log_event(ALERT_LOG_PATH, {"event": "Trading Halted (Daily Limit)", "strategy": strategy_webhook_name})
        return {"status": "halted", "reason": "daily PnL limit previously reached for this strategy"}
    if state["trade_active"]:
        log_event(ALERT_LOG_PATH, {"event": "Trade Ignored (Already Active)", "strategy": strategy_webhook_name})
        return {"status": "ignored", "reason": f"trade already active for strategy {strategy_webhook_name}"}
    if not ACCOUNT_ID: 
        logger.error("ACCOUNT_ID not available for order placement.")
        return {"status": "error", "reason": "Server error: Account ID not configured."}

    logger.info(f"[{strategy_webhook_name}] Received {alert.signal} signal for {alert.ticker} at {alert.time if alert.time else 'N/A'}, OrderType: {alert.order_type}")
    try:
        result = await place_order_projectx(alert, strategy_cfg) # alert.ticker is symbol_id
        if result.get("success") and result.get("orderId"):
            order_id = result["orderId"]
            signal_dir = "long" if alert.signal.lower() == "long" else "short"
            
            # Ensure symbol_id for Trade object comes from where place_order_projectx determined it
            # which is alert.ticker or strategy_cfg.get("PROJECTX_CONTRACT_ID")
            trade_symbol_id = alert.ticker or strategy_cfg.get("PROJECTX_CONTRACT_ID")

            state["current_trade"] = Trade(
                strategy_name=strategy_webhook_name, order_id=order_id, direction=signal_dir,
                account_id=alert.account_id, symbol_id=trade_symbol_id, # Use determined symbol_id
                entry_price=None, size=alert.quantity if alert.quantity is not None else strategy_cfg.get("TRADE_SIZE", 1) # Use determined size
            )
            state["trade_active"] = True 
            log_event(TRADE_LOG_PATH, {"event": "entry_order_placed", "strategy": strategy_webhook_name, "signal": alert.signal, "ticker": alert.ticker, "projectx_order_id": order_id, "timestamp": datetime.utcnow().isoformat()})
            
            symbol_to_subscribe = alert.ticker or strategy_cfg.get("PROJECTX_CONTRACT_ID") # This is symbol_id
            if symbol_to_subscribe:
                background_tasks.add_task(ensure_market_stream_for_contract, symbol_to_subscribe)
                logger.info(f"Scheduled MarketDataStream check/start for symbol {symbol_to_subscribe} in background.")
            else:
                logger.error(f"No symbol ID available to ensure market stream for strategy {strategy_webhook_name}.")
            return {"status": "trade_placed", "projectx_order_id": order_id}
        else:
            error_msg = result.get("errorMessage", "Unknown error placing order.")
            log_event(ALERT_LOG_PATH, {"event": "Order Placement Failed", "strategy": strategy_webhook_name, "error": error_msg, "response": result})
            return {"status": "error", "reason": f"Failed to place order: {error_msg}"}
    except Exception as e:
        logger.error(f"Error processing webhook for {strategy_webhook_name}: {e}", exc_info=True)
        log_event(ALERT_LOG_PATH, {"event": "Error Processing Webhook", "strategy": strategy_webhook_name, "error": str(e)})
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

    # OrderRequest is an alias for PlaceOrderRequest
    order_req = OrderRequest(
        account_id=params.account_id,
        symbol_id=params.contract_id, # Changed from contract_id to symbol_id (ManualTradeParams.contract_id is symbol_id)
        position_size=params.size,    # Changed from size to position_size (ManualTradeParams.size is position_size)
        side=order_side_int,
        type=OrderType.Market.value
    )

    try:
        result_details: PlaceOrderResponse = await api_client.place_order(order_req)
        if result_details and result_details.success and result_details.order_id is not None:
            msg = f"Market order placed successfully. Order ID: {result_details.order_id}"
            logger.info(f"[Manual Trade] {msg}")
            log_event(TRADE_LOG_PATH, {"event": "manual_market_order_placed", "params": params.dict(by_alias=True), "result": result_details.dict(by_alias=True)})
            background_tasks.add_task(ensure_market_stream_for_contract, params.contract_id) # params.contract_id is symbol_id
            return {"success": True, "message": msg, "details": result_details.dict(by_alias=True)}
        else:
            err_msg = result_details.error_message if result_details else "Market order placement failed"
            error_code_val = result_details.error_code.value if result_details and result_details.error_code else "N/A"
            logger.error(f"[Manual Trade] {err_msg} (Code: {error_code_val}). API Response: {result_details.dict(by_alias=True) if result_details else 'N/A'}")
            log_event(ALERT_LOG_PATH, {"event": "manual_market_order_failed", "error": err_msg, "details": result_details.dict(by_alias=True) if result_details else None})
            return {"success": False, "message": err_msg, "details": result_details.dict(by_alias=True) if result_details else None}
    except Exception as e:
        logger.error(f"[Manual Trade] Exception placing market order: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.post("/manual/trailing_stop_order", summary="Place a manual trailing stop order")
async def post_manual_trailing_stop_order(params: ManualTradeParams, background_tasks: BackgroundTasks):
    if not api_client: 
        return {"success": False, "message": "APIClient not initialized."}
    
    logger.info(f"[Manual Trade] Received trailing stop order request: {params.dict(by_alias=True)}")

    if params.trailingDistance is None or params.trailingDistance <= 0: 
        return {"success": False, "message": "Trailing distance must be a positive value."}

    order_side_int: int
    if params.side.lower() == "long":
        order_side_int = OrderSide.Bid.value
    elif params.side.lower() == "short":
        order_side_int = OrderSide.Ask.value
    else:
        return {"success": False, "message": f"Invalid side: {params.side}."}

    # OrderRequest is an alias for PlaceOrderRequest
    order_req = OrderRequest( 
        account_id=params.account_id,
        symbol_id=params.contract_id,       # Changed from contract_id (ManualTradeParams.contract_id is symbol_id)
        position_size=params.size,          # Changed from size (ManualTradeParams.size is position_size)
        side=order_side_int, 
        type=OrderType.TrailingStop.value, 
        trail_distance=params.trailingDistance # Changed from trail_price (ManualTradeParams.trailingDistance is trail_distance)
    )

    try:
        result_details: PlaceOrderResponse = await api_client.place_order(order_req)
        if result_details and result_details.success and result_details.order_id is not None:
            msg = f"Trailing stop order placed. ID: {result_details.order_id}"
            logger.info(f"[Manual Trade] {msg}")
            log_event(TRADE_LOG_PATH, {"event": "manual_trailing_stop_placed", "params": params.dict(by_alias=True), "result": result_details.dict(by_alias=True)})
            background_tasks.add_task(ensure_market_stream_for_contract, params.contract_id) # params.contract_id is symbol_id
            return {"success": True, "message": msg, "details": result_details.dict(by_alias=True)}
        else:
            err_msg = result_details.error_message if result_details else "Trailing stop order failed"
            error_code_val = result_details.error_code.value if result_details and result_details.error_code else "N/A"
            logger.error(f"[Manual Trade] {err_msg} (Code: {error_code_val}). API Response: {result_details.dict(by_alias=True) if result_details else 'N/A'}")
            log_event(ALERT_LOG_PATH, {"event": "manual_trailing_stop_failed", "error": err_msg, "details": result_details.dict(by_alias=True) if result_details else None})
            return {"success": False, "message": err_msg, "details": result_details.dict(by_alias=True) if result_details else None}
    except Exception as e:
        logger.error(f"[Manual Trade] Exception placing trailing stop: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.post("/manual/cancel_all_orders", summary="Cancel all open orders for an account (Simulated)")
async def post_manual_cancel_all_orders(params: AccountActionParams):
    if not api_client:
        return {"success": False, "message": "APIClient not initialized."}

    logger.info(f"[Manual Action] Received Cancel All Orders request for account: {params.account_id}")
    cancelled_count = 0
    errors = []
    try:
        open_orders = await api_client.get_open_orders(params.account_id) # Returns List[OrderModel]
        if not open_orders:
            logger.info(f"[Manual Action] No open orders found to cancel for account {params.account_id}.")
            return {"success": True, "message": "No open orders found to cancel.", "cancelled_count": 0, "errors": []}

        for order_detail in open_orders: # order_detail is OrderModel
            try:
                # cancel_order expects order_id and account_id. OrderModel has id and account_id.
                cancel_response = await api_client.cancel_order(order_id=order_detail.id, account_id=order_detail.account_id)
                if cancel_response.success:
                    logger.info(f"[Manual Action] Order {order_detail.id} for account {order_detail.account_id} cancelled successfully.")
                    cancelled_count += 1
                else:
                    err_msg = cancel_response.error_message or f"Failed to cancel order {order_detail.id}"
                    logger.warning(f"[Manual Action] {err_msg}. API Response: {cancel_response.dict(by_alias=True)}")
                    errors.append(err_msg)
            except Exception as e_cancel:
                logger.error(f"[Manual Action] Exception cancelling order {order_detail.id}: {e_cancel}", exc_info=True)
                errors.append(f"Exception cancelling order {order_detail.id}: {str(e_cancel)}")
        msg = f"Cancel All Orders for account {params.account_id} processed. Cancelled: {cancelled_count}. Errors: {len(errors)}."
        log_event(ALERT_LOG_PATH, {"event": "manual_cancel_all_processed", "params": params.dict(by_alias=True), "cancelled_count": cancelled_count, "errors": errors})
        return {"success": True, "message": msg, "cancelled_count": cancelled_count, "errors": errors}
    except Exception as e:
        logger.error(f"[Manual Action] Exception in Cancel All Orders for account {params.account_id}: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.post("/manual/flatten_all_trades", summary="Flatten all open positions for an account")
async def post_manual_flatten_all_trades(params: AccountActionParams):
    if not api_client: 
        return {"success": False, "message": "APIClient not initialized."}
    
    logger.info(f"[Manual Action] Received Flatten All Trades request for account: {params.account_id}")
    flattened_count = 0
    errors = []
    try:
        positions: List[PositionModel] = await api_client.get_positions(params.account_id) # Returns List[PositionModel]
        if not positions:
            logger.info(f"[Manual Action] No open positions found for account {params.account_id}.")
            return {"success": True, "message": "No open positions found.", "flattened_count": 0, "errors": []}
        
        for pos in positions: # pos is PositionModel
            try:
                opposing_side_int: int
                if pos.type == PositionType.Long: 
                    opposing_side_int = OrderSide.Ask.value 
                elif pos.type == PositionType.Short:
                    opposing_side_int = OrderSide.Bid.value  
                else:
                    logger.warning(f"[Manual Action] Unknown position type for {pos.contract_id}: {pos.type}. Skipping.") # PositionModel has contract_id
                    errors.append(f"Unknown position type for {pos.contract_id}")
                    continue
                
                # OrderRequest is an alias for PlaceOrderRequest
                order_req = OrderRequest(
                    account_id=pos.account_id, 
                    symbol_id=pos.contract_id,    # PositionModel has contract_id, API expects symbol_id for new order.
                    position_size=abs(int(pos.size)), # PositionModel has size
                    side=opposing_side_int,    
                    type=OrderType.Market.value 
                )
                logger.info(f"[Manual Action] Flattening {pos.contract_id} (Qty: {pos.size}, Side: {pos.type.name}) with: {order_req.dict(by_alias=True)}")

                result_details: PlaceOrderResponse = await api_client.place_order(order_req)
                if result_details and result_details.success and result_details.order_id is not None:
                    logger.info(f"[Manual Action] Flatten order for {pos.contract_id} placed. ID: {result_details.order_id}")
                    flattened_count += 1
                else:
                    err_msg = result_details.error_message if result_details else f"Failed to flatten {pos.contract_id}."
                    error_code_val = result_details.error_code.value if result_details and result_details.error_code else "N/A"
                    logger.warning(f"[Manual Action] {err_msg} (Code: {error_code_val}). API Response: {result_details.dict(by_alias=True) if result_details else 'N/A'}")
                    errors.append(err_msg)
            except Exception as e_flatten:
                logger.error(f"[Manual Action] Exception flattening {pos.contract_id}: {e_flatten}", exc_info=True)
                errors.append(f"Exception flattening {pos.contract_id}: {str(e_flatten)}")

        msg = f"Flatten All processed. Flatten orders: {flattened_count}. Errors: {len(errors)}."
        log_event(ALERT_LOG_PATH, {"event": "manual_flatten_all", "params": params.dict(by_alias=True), "flattened": flattened_count, "errors": errors})
        return {"success": True, "message": msg, "flattened_count": flattened_count, "errors": errors}
    except Exception as e:
        logger.error(f"[Manual Action] Flatten All exception: {e}", exc_info=True)
        return {"success": False, "message": str(e)}
        
@app.get("/check_trade_status")
async def check_trade_status_endpoint(strategy_name: Optional[str] = None): # Allow optional strategy_name query param
    active_trades_summary = []
    strategies_to_check = [strategy_name] if strategy_name and strategy_name in trade_states else trade_states.keys()

    for strat_name in strategies_to_check:
        state = trade_states.get(strat_name)
        if state and state.get("trade_active") and state.get("current_trade"):
            await check_and_close_active_trade(strat_name) 
            current_trade_obj: Optional[Trade] = state.get("current_trade") # Re-fetch after check
            if current_trade_obj: # Check if trade is still active
                active_trades_summary.append({
                    "strategy": strat_name, "status": "checked_still_active",
                    "entry_price": current_trade_obj.entry_price, "signal": current_trade_obj.direction,
                    "order_id": current_trade_obj.order_id, "symbol_id": current_trade_obj.symbol_id
                })
            else: # Trade was closed by check_and_close_active_trade
                 active_trades_summary.append({"strategy": strat_name, "status": "checked_now_closed"})
        elif state: # Strategy exists but no active trade
            active_trades_summary.append({"strategy": strat_name, "status": "no_active_trade"})


    if not active_trades_summary and strategy_name:
        return {"status": f"no_active_trade_found_for_strategy_{strategy_name}"}
    elif not active_trades_summary:
        return {"status": "no_active_trades_found_globally"}
        
    return {"active_trades_status": active_trades_summary}

@app.get("/live_feed", response_class=HTMLResponse)
async def live_feed_page():
    def format_data(item_list, event_type):
        html_rows = ""
        for item_group in reversed(item_list[-20:]): 
            for item in item_group: 
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

@app.get("/status_ui", response_model=StatusResponse) 
async def get_status_endpoint():
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
    if not api_client: return {"error": "APIClient not initialized"}
    try:
        accounts_data = await api_client.get_accounts(only_active=False)
        contracts_data = await api_client.search_contracts(search_text="ENQ", live=True) 
        return {
            "session_token_active": bool(api_client._session_token), 
            "accounts": [acc.dict(by_alias=True) for acc in accounts_data],
            "sample_contract_search_ENQ": [c.dict(by_alias=True) for c in contracts_data]
        }
    except Exception as e:
        logger.error(f"Error in /debug_account_info: {e}", exc_info=True)
        return {"error": str(e)}

@app.get("/", response_class=HTMLResponse)
async def config_dashboard_with_selection_get(strategy_selected: str = None): 
    global current_strategy_name, active_strategy_config 
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
    active_strategy_config = strategies[current_strategy_name] 
    return await config_dashboard_page()

@app.get("/admin/clear_logs")
async def clear_logs():
    try:
        for f_name in [TRADE_LOG_PATH, ALERT_LOG_PATH]: 
            if os.path.exists(f_name): os.remove(f_name)
        return {"status": "success", "message": "Log files deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/contract_search", response_class=HTMLResponse)
async def contract_search_page(search_query: str = ""):
    results_html = ""
    if search_query and api_client:
        try:
            contracts_result: List[Contract] = await api_client.search_contracts(search_text=search_query, live=False) # Contract is alias for ContractModel
            for contract_item in contracts_result: 
                results_html += f"""
                <tr>
                    <td>{contract_item.id}</td><td>{contract_item.name}</td><td>{contract_item.description or ""}</td>
                    <td>{contract_item.tick_size}</td><td>{contract_item.tick_value}</td>
                    <td><button onclick="copyToConfig('{contract_item.id}')">Copy</button></td></tr>"""
        except Exception as e: results_html = f"<tr><td colspan='6'>Error: {str(e)}</td></tr>"
    elif not api_client: results_html = "<tr><td colspan='6'>API Client not initialized.</td></tr>"
    html = f"""
    <html><head><title>Contract Search</title>{COMMON_CSS}</head>
    <body><div class="container"><h1>Contract Search</h1>
        <form method="get"><label for="search_query">Search Term:</label>
            <input type="text" id="search_query" name="search_query" value="{search_query}" />
            <button type="submit">Search</button></form>
        <p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
        <table><thead><tr><th>ID (Symbol ID)</th><th>Name</th><th>Description</th><th>Tick Size</th><th>Tick Value</th><th>Action</th></tr></thead>
            <tbody>{results_html}</tbody></table></div>
    <script>
        function copyToConfig(symbolId) {{ // Changed contractId to symbolId
            const input = document.getElementById('projectx_contract_id'); // This is PROJECTX_CONTRACT_ID input
            if (input) {{ input.value = symbolId; alert("Symbol ID copied to config input."); }}
            else {{ alert("ProjectX Contract ID input field not found in the strategy config form."); }}
        }}
    </script></body></html>"""
    return HTMLResponse(content=html)
    
@app.head("/", response_class=HTMLResponse) 
async def config_dashboard_with_selection_head(strategy_selected: str = None):
    global current_strategy_name
    if strategy_selected and strategy_selected in strategies:
        current_strategy_name = strategy_selected
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
    strategy_runtime_state = trade_states.get(current_strategy_name, {})
    active_trade_for_current_strategy: Optional[Trade] = strategy_runtime_state.get("current_trade")
    trade_active_display = strategy_runtime_state.get('trade_active', False)
    entry_price_display = active_trade_for_current_strategy.entry_price if active_trade_for_current_strategy else "None"
    trade_time_display = active_trade_for_current_strategy.entry_time.isoformat() if active_trade_for_current_strategy and active_trade_for_current_strategy.entry_time else "None"
    current_signal_display = active_trade_for_current_strategy.direction if active_trade_for_current_strategy else "None"
    daily_pnl_display = f"{strategy_runtime_state.get('daily_pnl', 0.0):.2f}"
    trade_opened_by_display = current_strategy_name if trade_active_display else "N/A"
    html_content = f"""
    <html><head><title>Trading Bot Dashboard</title>{COMMON_CSS}</head><body><div class="container">
        <h1>Trading Bot Configuration</h1>
        <form action="/update_strategy_config" method="post">
            <label for="strategy_select">Displaying/Editing Strategy:</label>
            <select name="strategy_to_display" id="strategy_select">{strategy_options_html}</select>
            <h2>Configure Strategy: '{current_strategy_name}'</h2>
            <input type="hidden" name="strategy_name_to_update" value="{current_strategy_name}">
            <label for="symbol">Contract Symbol (e.g., NQ):</label>
            <input name="contract_symbol" id="symbol" value="{displayed_config.get('CONTRACT_SYMBOL', '')}" />
            <label for="projectx_contract_id">ProjectX Contract ID (Symbol ID):</label>
            <input name="projectx_contract_id" id="projectx_contract_id" value="{displayed_config.get('PROJECTX_CONTRACT_ID', '')}" />
            <label for="size">Trade Size (Position Size):</label>
            <input name="trade_size" id="size" type="number" value="{displayed_config.get('TRADE_SIZE', 1)}" />
            <label for="max_trade_loss">Max Trade Loss ($):</label>
            <input name="max_trade_loss" id="max_trade_loss" type="number" step="any" value="{displayed_config.get('MAX_TRADE_LOSS', -350.0)}" />
            <label for="max_trade_profit">Max Trade Profit ($):</label>
            <input name="max_trade_profit" id="max_trade_profit" type="number" step="any" value="{displayed_config.get('MAX_TRADE_PROFIT', 450.0)}" />
            <label for="max_daily_loss">Max Daily Loss ($):</label>
            <input name="max_daily_loss" id="max_daily_loss" type="number" step="any" value="{displayed_config.get('MAX_DAILY_LOSS', -1200.0)}" />
            <label for="max_daily_profit">Max Daily Profit ($):</label>
            <input name="max_daily_profit" id="max_daily_profit" type="number" step="any" value="{displayed_config.get('MAX_DAILY_PROFIT', 2000.0)}" />
            <button type="submit">Update Config for '{current_strategy_name}'</button></form>
        <h2>Available Strategies</h2>
        <table><thead><tr><th>Name</th><th>Actions</th></tr></thead><tbody>{strategy_rows_html}</tbody></table>
        <form action="/create_new_strategy_action" method="post" style="margin-top: 20px;">
            <label for="new_strategy_name_input">New Strategy Name:</label>
            <input name="new_strategy_name_input" id="new_strategy_name_input" type="text" placeholder="Enter new strategy name" required />
            <button type="submit">Create New Strategy (from Default)</button></form><hr>
        <h2>Live Bot Status (Strategy: {current_strategy_name})</h2>
        <div class="status-section" id="status-container">
            <p><strong>Trade Active:</strong> {trade_active_display}</p>
            <p><strong>Entry Price:</strong> {entry_price_display}</p>
            <p><strong>Trade Time (UTC):</strong> {trade_time_display}</p>
            <p><strong>Current Signal Direction:</strong> {current_signal_display}</p>
            <p><strong>Daily PnL for {current_strategy_name}:</strong> {daily_pnl_display}</p>
            <p><a href="/check_trade_status?strategy_name={current_strategy_name}" class="button-link">Manually Check Active Trade for {current_strategy_name}</a></p></div>
        <p><a href="/dashboard_menu_page" class="button-link">Go to Main Menu</a></p></div>
    <script>
        document.getElementById('strategy_select').addEventListener('change', function() {{
            window.location.href = '/?strategy_selected=' + this.value; }});</script></body></html>"""
    return HTMLResponse(content=html_content)

@app.post("/update_strategy_config")
async def update_strategy_config_action(strategy_name_to_update: str = Form(...), contract_symbol: str = Form(...), projectx_contract_id: str = Form(...), trade_size: int = Form(...), max_trade_loss: float = Form(...), max_trade_profit: float = Form(...), max_daily_loss: float = Form(...), max_daily_profit: float = Form(...)):
    global current_strategy_name 
    if strategy_name_to_update in strategies:
        strategies[strategy_name_to_update].update({
            "CONTRACT_SYMBOL": contract_symbol, "PROJECTX_CONTRACT_ID": projectx_contract_id, # PROJECTX_CONTRACT_ID is symbol_id
            "TRADE_SIZE": trade_size, "MAX_TRADE_LOSS": max_trade_loss, "MAX_TRADE_PROFIT": max_trade_profit,
            "MAX_DAILY_LOSS": max_daily_loss, "MAX_DAILY_PROFIT": max_daily_profit})
        save_strategies_to_file() 
        current_strategy_name = strategy_name_to_update
    return RedirectResponse(url=f"/?strategy_selected={strategy_name_to_update}", status_code=303)

@app.post("/create_new_strategy_action")
async def create_new_strategy_action_endpoint(new_strategy_name_input: str = Form(...)):
    global current_strategy_name
    if new_strategy_name_input and new_strategy_name_input.strip() and new_strategy_name_input not in strategies:
        new_name = new_strategy_name_input.strip()
        strategies[new_name] = dict(strategies["default"])
        trade_states[new_name] = {"trade_active": False, "daily_pnl": 0.0, "current_trade": None, "trading_halted_today": False} 
        save_strategies_to_file()
        current_strategy_name = new_name
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/delete_strategy_action")
async def delete_strategy_action_endpoint(strategy_to_delete: str):
    global current_strategy_name
    if strategy_to_delete in strategies and strategy_to_delete != "default":
        del strategies[strategy_to_delete]
        if strategy_to_delete in trade_states: del trade_states[strategy_to_delete]
        save_strategies_to_file()
        if current_strategy_name == strategy_to_delete: current_strategy_name = "default"
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/clone_strategy_action")
async def clone_strategy_action_endpoint(strategy_to_clone: str):
    global current_strategy_name
    if strategy_to_clone in strategies:
        new_name_base = f"{strategy_to_clone}_copy"; new_name = new_name_base; count = 1
        while new_name in strategies: new_name = f"{new_name_base}{count}"; count += 1
        strategies[new_name] = dict(strategies[strategy_to_clone])
        trade_states[new_name] = {"trade_active": False, "daily_pnl": 0.0, "current_trade": None, "trading_halted_today": False}
        save_strategies_to_file()
        current_strategy_name = new_name
        return RedirectResponse(url=f"/?strategy_selected={current_strategy_name}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/manual_trade", response_class=HTMLResponse)
async def manual_trade_page():
    html_content = f"""
    <html><head><title>Manual Trade Entry</title>{COMMON_CSS}
        <style>.form-grid {{ display: grid; grid-template-columns: auto 1fr; gap: 10px 20px; align-items: center; }}
               .form-grid label {{ text-align: right; }} .button-group button {{ margin-right: 10px; margin-bottom: 10px; }}
               #manual_trade_response {{ margin-top: 20px; padding: 10px; border: 1px solid #434651; border-radius: 4px; background-color: #2A2E39; white-space: pre-wrap; }}</style></head>
    <body><div class="container"><h1>Manual Trade Entry</h1><p><a href="/dashboard_menu_page" class="button-link">Back to Menu</a></p>
        <form id="manual_trade_form"><div class="form-grid">
            <label for="accountId">Account ID:</label><input type="number" id="accountId" name="accountId" required value="{ACCOUNT_ID if ACCOUNT_ID else ''}">
            <label for="contractId">Symbol ID (Contract ID):</label><input type="text" id="contractId" name="contractId" required placeholder="e.g., CON.F.US.ENQ.M25">
            <label for="side">Side:</label><select id="side" name="side"><option value="long">Long</option><option value="short">Short</option></select>
            <label for="size">Position Size:</label><input type="number" id="size" name="size" min="1" value="1" required>
            <label for="trailingDistance">Trail Distance (Ticks, for Trailing Stop):</label><input type="number" id="trailingDistance" name="trailingDistance" min="0">
        </div></form>
        <div class="button-group" style="margin-top: 20px;">
            <button type="button" onclick="submitManualOrder('market')">Place Market Order</button>
            <button type="button" onclick="submitManualOrder('trailing_stop')">Place Trailing Stop Order</button>
            <button type="button" onclick="submitAccountAction('cancel_all')">Cancel All Orders (Account)</button>
            <button type="button" onclick="submitAccountAction('flatten_all')">Flatten All Trades (Account)</button></div>
        <h2>Response:</h2><pre id="manual_trade_response">No action taken yet.</pre></div>
    <script>
        async function submitManualOrder(orderType) {{
            const form = document.getElementById('manual_trade_form'); const responseArea = document.getElementById('manual_trade_response');
            const formData = {{ accountId: parseInt(form.elements.accountId.value), contractId: form.elements.contractId.value, 
                               side: form.elements.side.value, size: parseInt(form.elements.size.value),
                               trailingDistance: form.elements.trailingDistance.value ? parseFloat(form.elements.trailingDistance.value) : null }}; // Ensure float for distance
            if (!formData.accountId || !formData.contractId || !formData.side || !formData.size) {{ responseArea.textContent = "Error: Account ID, Symbol ID, Side, and Size are required."; return; }}
            if (orderType === 'trailing_stop' && (!formData.trailingDistance || formData.trailingDistance <= 0)) {{ responseArea.textContent = "Error: Trail Distance must be positive for trailing stop."; return; }}
            let endpoint = (orderType === 'market') ? '/manual/market_order' : '/manual/trailing_stop_order';
            if (!endpoint) {{ responseArea.textContent = 'Error: Unknown order type.'; return; }}
            responseArea.textContent = 'Submitting ' + orderType + ' order...';
            try {{ const response = await fetch(endpoint, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(formData) }});
                  const result = await response.json(); responseArea.textContent = JSON.stringify(result, null, 2);
            }} catch (error) {{ console.error('Error submitting manual order:', error); responseArea.textContent = 'Error: ' + error.message; }} }}
        async function submitAccountAction(actionType) {{
            const form = document.getElementById('manual_trade_form'); const responseArea = document.getElementById('manual_trade_response');
            const accountIdValue = form.elements.accountId.value;
            if (!accountIdValue) {{ responseArea.textContent = "Error: Account ID is required."; return; }}
            const accountData = {{ accountId: parseInt(accountIdValue) }};
            let endpoint = (actionType === 'cancel_all') ? '/manual/cancel_all_orders' : (actionType === 'flatten_all') ? '/manual/flatten_all_trades' : '';
            if (!endpoint) {{ responseArea.textContent = 'Error: Unknown account action.'; return; }}
            responseArea.textContent = 'Submitting ' + actionType.replace('_', ' ') + ' for account ' + accountData.accountId + '...';
            try {{ const response = await fetch(endpoint, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(accountData) }});
                  const result = await response.json(); responseArea.textContent = JSON.stringify(result, null, 2);
            }} catch (error) {{ console.error('Error submitting account action:', error); responseArea.textContent = 'Error: ' + error.message; }} }}
    </script></body></html>"""
    return HTMLResponse(content=html_content)
    
@app.get("/dashboard_menu_page", response_class=HTMLResponse)
async def dashboard_menu_html_page():
    return HTMLResponse(f"""
    <html><head><title>Bot Dashboard Menu</title>{COMMON_CSS}</head><body><div class="container">
        <h1>Topstep Bot Dashboard</h1><ul class="menu-list">
            <li><a href="/">Configuration & Status</a></li><li><a href="/contract_search">Contract Search (Symbol ID)</a></li>
            <li><a href="/logs/trades">Trade History</a></li><li><a href="/logs/alerts">Alert Log</a></li>
            <li><a href="/toggle_trading_status">Toggle Trading (View Status)</a></li>
            <li><a href="/debug_account_info">Debug Account Info</a></li><li><a href="/live_feed">Live Market Feed</a></li>
            <li><a href="/manual_trade">Manual Trade Entry</a></li></ul></div></body></html>""")

bot_trading_enabled = True 
@app.get("/toggle_trading_status", response_class=HTMLResponse)
async def toggle_trading_view():
    global bot_trading_enabled; status_message = "ENABLED" if bot_trading_enabled else "DISABLED"
    button_text = "Disable Trading (Global)" if bot_trading_enabled else "Enable Trading (Global)"
    return HTMLResponse(f"""<html><head><title>Toggle Trading</title>{COMMON_CSS}</head><body><div class="container">
        <h1>Toggle Bot Trading (Global)</h1><p>Current Bot Trading Status: <strong>{status_message}</strong></p>
        <p>Note: This is a global toggle. Individual strategy PnL limits still apply.</p>
        <form action="/toggle_trading_action" method="post"><button type="submit">{button_text}</button></form>
        <p><a href="/dashboard_menu_page" class='button-link'>Back to Menu</a></p></div></body></html>""")

@app.post("/toggle_trading_action")
async def toggle_trading_action_endpoint():
    global bot_trading_enabled; bot_trading_enabled = not bot_trading_enabled
    status_message = "ENABLED" if bot_trading_enabled else "DISABLED"
    logger.info(f"Global bot trading status changed to: {status_message}")
    log_event(ALERT_LOG_PATH, {"event": "Global Trading Status Changed", "status": status_message})
    return RedirectResponse(url="/toggle_trading_status", status_code=303)

@app.get("/logs/trades", response_class=HTMLResponse)
async def trade_log_view_page():
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "r") as f: trades_log_entries = json.load(f)
    else: trades_log_entries = []
    summary_by_strategy = {}
    for t_entry in trades_log_entries:
        if t_entry.get("event", "").startswith("exit"): 
            strat = t_entry.get("strategy", "Unknown")
            summary = summary_by_strategy.setdefault(strat, {"wins": 0, "losses": 0, "total_pnl": 0, "count": 0, "best": float('-inf'), "worst": float('inf')})
            pnl = t_entry.get("pnl", 0.0)
            if pnl is not None: 
                summary["wins"] += 1 if pnl > 0 else 0; summary["losses"] += 1 if pnl <= 0 else 0
                summary["total_pnl"] += pnl; summary["count"] += 1
                summary["best"] = max(summary["best"] if summary["best"] != float('-inf') else pnl, pnl)
                summary["worst"] = min(summary["worst"] if summary["worst"] != float('inf') else pnl, pnl)
    summary_html = "<h2>Performance Summary by Strategy</h2>"
    for s, d in summary_by_strategy.items():
        avg_pnl_html = f"${d['total_pnl']/d['count']:.2f}" if d['count'] > 0 else "N/A"
        best_pnl_html = f"${d['best']:.2f}" if d['best'] != float('-inf') else "N/A"
        worst_pnl_html = f"${d['worst']:.2f}" if d['worst'] != float('inf') else "N/A"
        summary_html += (f"<h3>{s}</h3><ul><li>Total Trades Logged (Exits): {d['count']}</li><li>Wins: {d['wins']} | Losses: {d['losses']}</li>"
                         f"<li>Total PnL: ${d['total_pnl']:.2f}</li><li>Average PnL per Trade: {avg_pnl_html}</li>"
                         f"<li>Best Trade: {best_pnl_html} | Worst Trade: {worst_pnl_html}</li></ul>")
    if not summary_by_strategy: summary_html += "<p>No completed trades with PnL found in logs.</p>"
    rows_html = "".join([f"<tr><td>{t.get('timestamp','')}</td><td>{t.get('strategy','')}</td><td>{t.get('event','')}</td>"
                         f"<td>{t.get('projectx_order_id', t.get('orderId',''))}</td>"
                         f"<td>{t.get('entry_price','')}</td><td>{t.get('exit_price','')}</td><td>{t.get('pnl','')}</td>"
                         f"<td>{t.get('signal','')}</td><td>{t.get('ticker', t.get('symbol_id',''))}</td></tr>" # Added symbol_id as fallback for ticker
                         for t in reversed(trades_log_entries)])
    return HTMLResponse(f"""<html><head><title>Trade History</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Trade History</h1><p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>{summary_html}
    <h2>Detailed Trade Log</h2><a href='/download/trades.csv' class='button-link'>Download CSV</a>
    <table><thead><tr><th>Time</th><th>Strategy</th><th>Event</th><th>Order ID</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Signal</th><th>Symbol/Ticker</th></tr></thead>
    <tbody>{rows_html}</tbody></table></div></body></html>""")

@app.get("/download/trades.csv")
async def download_trade_log():
    import io 
    if not os.path.exists(TRADE_LOG_PATH): return HTMLResponse("Trade log not found", status_code=404)
    with open(TRADE_LOG_PATH, "r") as f: trades_log_entries = json.load(f)
    def generate_csv_data():
        output = io.StringIO()
        fieldnames = ["timestamp", "strategy", "event", "projectx_order_id", "orderId", "symbol_id", "ticker", "entry_price", "exit_price", "pnl", "signal", "error", "detail", "response"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore') 
        writer.writeheader()
        for entry in trades_log_entries: writer.writerow(entry)
        yield output.getvalue()
    return StreamingResponse(generate_csv_data(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trade_log_full.csv"})

@app.get("/logs/alerts", response_class=HTMLResponse)
async def alert_log_view_page():
    if os.path.exists(ALERT_LOG_PATH):
        with open(ALERT_LOG_PATH, "r") as f: alerts = json.load(f)
    else: alerts = []
    rows_html = "".join([f"<tr><td>{a.get('timestamp','')}</td><td>{a.get('strategy','')}</td><td>{a.get('signal','')}</td><td>{a.get('ticker',a.get('symbol_id',''))}</td><td>{a.get('event','')}</td><td>{a.get('error',a.get('detail', json.dumps(a.get('response')) if a.get('response') else ''))}</td></tr>" for a in reversed(alerts)])
    return HTMLResponse(f"""<html><head><title>Alert History</title>{COMMON_CSS}</head><body><div class='container'>
    <h1>Alert History</h1><p><a href='/dashboard_menu_page' class='button-link'>Back to Menu</a></p>
    <table><thead><tr><th>Time</th><th>Strategy</th><th>Signal</th><th>Symbol/Ticker</th><th>Event</th><th>Details/Error</th></tr></thead>
    <tbody>{rows_html}</tbody></table></div></body></html>""")

logger.info("main.py updated with new OrderRequest fields. Application ready.")
