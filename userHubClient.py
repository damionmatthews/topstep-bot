from signalrcore.hub_connection_builder import HubConnectionBuilder
import logging
import os
import requests

logger = logging.getLogger(__name__)

def get_fresh_topstep_token():
    username = os.getenv("TOPSTEP_USERNAME")
    api_key = os.getenv("TOPSTEP_API_KEY")

    if not username or not api_key:
        raise ValueError("TOPSTEP_USERNAME or TOPSTEP_API_KEY is missing from environment.")

    response = requests.post(
        "https://api.topstepx.com/api/Auth/loginKey",
        json={"userName": username, "apiKey": api_key}
    )

    if response.status_code != 200:
        raise RuntimeError(f"Auth failed: {response.status_code}, {response.text}")

    token = response.json().get("token")
    if not token:
        raise RuntimeError("Auth succeeded but no token returned.")

    return token
    
user_connection = None
user_connection_started = False
user_trade_events = []
user_order_events = []
user_position_events = []

entry_price = None
current_trade_id = None

# Callback handler for trade updates
trade_event_callback = None

def register_trade_event_callback(callback):
    global trade_event_callback
    logger.info("[UserHub] Registering trade event callback.")
    trade_event_callback = callback

def setupUserHubConnection(authToken):
    global user_connection, user_connection_started

    if user_connection_started:
        logger.info("[UserHub] Already connected.")
        return

    userHubUrl = f"https://rtc.topstepx.com/hubs/user?access_token={authToken}"
    user_connection = HubConnectionBuilder() \
        .with_url(userHubUrl) \
        .with_automatic_reconnect({"keep_alive_interval": 10, "reconnect_interval": 5}) \
        .build()

    user_connection.on_open(lambda: logger.info("[UserHub] Connection opened."))
    user_connection.on_close(lambda: logger.warning("[UserHub] Connection closed."))
    user_connection.on("GatewayUserTrade", handle_user_trade)
    logger.info("[UserHub] Subscribed to GatewayUserTrade")
    user_connection.on("GatewayUserOrder", handle_user_order)
    user_connection.on("GatewayUserPosition", handle_user_position)
    try:
        user_connection.start()
        user_connection_started = True
        logger.info("[UserHub] Connection started successfully.")
    except Exception as e:
        logger.error(f"[UserHub] Connection error: {e}")

def handle_user_trade(args):
    user_trade_events.append(args)
    logger.info(f"[UserHub] Trade Event: {args}")
    if trade_event_callback:
        logger.info("[UserHub] Invoking registered trade event callback.")
        trade_event_callback(args)
    else:
        logger.warning("[UserHub] No trade event callback registered.")

def handle_user_order(args):
    user_order_events.append(args)
    logger.info(f"[UserHub] Order Event: {args}")

def handle_user_position(args):
    user_position_events.append(args)
    logger.info(f"[UserHub] Position Event: {args}")

def closeUserHubConnection():
    global user_connection, user_connection_started
    if user_connection:
        user_connection.stop()
        user_connection = None
        user_connection_started = False
        logger.info("[UserHub] Connection closed.")

def get_userhub_events():
    return {
        "trades": user_trade_events[-50:],
        "orders": user_order_events[-50:],
        "positions": user_position_events[-50:]
    }

def start_userhub_connection():
    try:
        token = get_fresh_topstep_token()
        setupUserHubConnection(token)
    except Exception as e:
        logger.error(f"[UserHub] Failed to start connection: {e}")
