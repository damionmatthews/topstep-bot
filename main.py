from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from datetime import datetime
import httpx
import os
import json

app = FastAPI()

# --- ENVIRONMENT CONFIG ---
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
# It's good practice to also get your username for ProjectX login here
PROJECTX_USERNAME = os.getenv("PROJECTX_USERNAME", "default_user") # Add your ProjectX username to env

# --- GLOBAL STATE & PATHS ---
# Strategy storage path
STRATEGY_PATH = "strategies.json"
TRADE_LOG_PATH = "trades.json"
ALERT_LOG_PATH = "alerts.json"

# Initialize log files if they don't exist
for path in [STRATEGY_PATH, TRADE_LOG_PATH, ALERT_LOG_PATH]:
    if not os.path.exists(path) and path.endswith(".json"):
        with open(path, "w") as f:
            if path == STRATEGY_PATH:
                # Initial default strategy if strategies.json is missing
                json.dump({
                    "default": {
                        "MAX_DAILY_LOSS": -1200.0,
                        "MAX_DAILY_PROFIT": 2000.0,
                        "MAX_TRADE_LOSS": -350.0,
                        "MAX_TRADE_PROFIT": 450.0,
                        "CONTRACT_SYMBOL": "NQ", # This will need to be mapped to contractId
                        "TRADE_SIZE": 1,
                        "PROJECTX_CONTRACT_ID": "CON.F.US.MNQ.H25" # Example, fetch dynamically
                    }
                }, f, indent=2)
            else:
                json.dump([], f, indent=2) # Initialize log files as empty lists


# Load strategies
with open(STRATEGY_PATH, "r") as f:
    strategies = json.load(f)

current_strategy_name = "default" # Renamed to avoid confusion with 'config' object
# Ensure 'default' strategy exists, if not, create a basic one
if "default" not in strategies:
    strategies["default"] = {
        "MAX_DAILY_LOSS": -1200.0, "MAX_DAILY_PROFIT": 2000.0,
        "MAX_TRADE_LOSS": -350.0, "MAX_TRADE_PROFIT": 450.0,
        "CONTRACT_SYMBOL": "NQ", "TRADE_SIZE": 1,
        "PROJECTX_CONTRACT_ID": "CON.F.US.MNQ.H25" # Placeholder
    }
    # Save immediately if default was missing
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)


active_strategy_config = strategies[current_strategy_name] # Config for the currently selected strategy for webhook

# Runtime state (consider moving to a more robust per-strategy state if needed)
# For now, these will reflect the state of the *last triggered strategy's trade*
daily_pnl = 0.0 # This should ideally be per-strategy as well
trade_active = False
entry_price = None
trade_time = None
current_signal_direction = None # 'long' or 'short'
current_trade_id = None # To track the ProjectX orderId

# --- ProjectX API Session Token ---
projectx_session_token = None
PROJECTX_BASE_URL = "https://gateway-api.projectx.com/api/" # Use demo first!
# PROJECTX_BASE_URL = "https://api.topstepx.com/api" # Production

# --- DATA MODELS ---
class SignalAlert(BaseModel):
    signal: str # 'long' or 'short'
    ticker: str # e.g., "NQ"
    time: str   # ISO format time string

class StatusResponse(BaseModel):
    trade_active: bool
    entry_price: float | None
    trade_time: str | None
    current_signal_direction: str | None
    daily_pnl: float
    current_strategy_name: str
    active_strategy_config: dict


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
def save_strategies_to_file(): # Renamed to be more explicit
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

async def get_projectx_token():
    global projectx_session_token
    if projectx_session_token: # Basic check, add expiry logic later
        return projectx_session_token
    
    if not PROJECTX_USERNAME or not TOPSTEP_API_KEY:
        print("Error: ProjectX Username or API Key not configured.")
        return None

    async with httpx.AsyncClient() as client:
        try:
            print("Attempting to log in to ProjectX...")
            response = await client.post(
                f"{PROJECTX_BASE_URL}/Auth/loginKey",
                json={"userName": PROJECTX_USERNAME, "apiKey": TOPSTEP_API_KEY}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("success") and data.get("token"):
                projectx_session_token = data["token"]
                print("ProjectX Login successful.")
                return projectx_session_token
            else:
                print(f"ProjectX Login failed: {data.get('errorMessage')}")
                projectx_session_token = None
                return None
        except Exception as e:
            print(f"Error during ProjectX login: {e}")
            projectx_session_token = None
            return None

# Call on startup
@app.on_event("startup")
async def startup_event():
    await get_projectx_token()
    # Add logic to fetch contract details if needed, e.g.
    # for strategy_name, strat_config in strategies.items():
    #     if "CONTRACT_SYMBOL" in strat_config and "PROJECTX_CONTRACT_ID" not in strat_config:
    #         # Fetch and store PROJECTX_CONTRACT_ID
    #         pass


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
                "event": "exit", "strategy": strategy_name_of_trade, "signal": current_signal_direction,
                "entry_price": entry_price, "exit_price": current_market_price, "pnl": total_dollar_pnl,
                "reason": exit_reason, "projectx_order_id": current_trade_id
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
async def trade_log_view_page(): # Renamed
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "r") as f:
            try:
                trades = json.load(f)
            except json.JSONDecodeError:
                trades = []
    else:
        trades = []
    
    # Performance Summary
    exited_trades = [t for t in trades if t.get("event", "").startswith("exit")]
    total_trades_for_summary = len(exited_trades)
    wins = sum(1 for t in exited_trades if t.get("pnl", 0) > 0)
    losses = sum(1 for t in exited_trades if t.get("pnl", 0) <= 0)
    total_pnl_sum = sum(t.get("pnl", 0) for t in exited_trades)
    avg_pnl = round(total_pnl_sum / max(1, total_trades_for_summary), 2) if total_trades_for_summary > 0 else 0.0

    summary = {
        "total_exited_trades": total_trades_for_summary,
        "wins": wins,
        "losses": losses,
        "total_pnl": round(total_pnl_sum, 2),
        "average_pnl_per_trade": avg_pnl
    }
    rows_html = "".join([
        f"<tr><td>{t.get('timestamp','N/A')}</td><td>{t.get('strategy','N/A')}</td><td>{t.get('event','N/A')}</td><td>{t.get('signal','N/A')}</td><td>{t.get('entry_price_estimate', t.get('entry_price','N/A'))}</td><td>{t.get('exit_price','N/A')}</td><td>{t.get('pnl','N/A')}</td><td>{t.get('reason','N/A')}</td></tr>"
        for t in reversed(trades) # Show newest first
    ])
    return HTMLResponse(f"""
    <html><head><title>Trade History</title>{COMMON_CSS}
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    </head><body><div class="container">
        <h1>Trade History</h1>
        <p><a href="/dashboard_menu_page" class="button-link" style="margin-bottom:20px;">Back to Menu</a></p>
        <h2>Performance Summary</h2><pre>{json.dumps(summary, indent=2)}</pre>
        <table><thead><tr><th>Time (UTC)</th><th>Strategy</th><th>Event</th><th>Signal</th><th>Entry Est.</th><th>Exit</th><th>PnL ($)</th><th>Reason</th></tr></thead><tbody>{rows_html}</tbody></table>
    </div></body></html>""")

@app.get("/logs/alerts", response_class=HTMLResponse)
async def alert_log_view_page(): # Renamed
    if os.path.exists(ALERT_LOG_PATH):
        with open(ALERT_LOG_PATH, "r") as f:
            try:
                alerts = json.load(f)
            except json.JSONDecodeError:
                alerts = []
    else:
        alerts = []
    rows_html = "".join([
        f"<tr><td>{a.get('timestamp','N/A')}</td><td>{a.get('event','N/A')}</td><td>{a.get('strategy','N/A')}</td><td>{a.get('signal','N/A')}</td><td>{a.get('ticker','N/A')}</td><td>{a.get('error',a.get('detail','N/A'))}</td></tr>"
        for a in reversed(alerts) # Show newest first
    ])
    return HTMLResponse(f"""
    <html><head><title>Alert Log</title>{COMMON_CSS}
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
    </head><body><div class="container">
        <h1>Alert Log</h1>
        <p><a href="/dashboard_menu_page" class="button-link" style="margin-bottom:20px;">Back to Menu</a></p>
        <table><thead><tr><th>Time (UTC)</th><th>Event</th><th>Strategy</th><th>Signal</th><th>Ticker</th><th>Details/Error</th></tr></thead><tbody>{rows_html}</tbody></table>
    </div></body></html>""")
