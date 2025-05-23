from signalrcore.hub_connection_builder import HubConnectionBuilder
import logging
import asyncio
import threading

logger = logging.getLogger(__name__)

rtc_connection = None
connection_started = False
quote_data = []
trade_data = []
depth_data = []

# Callback that will be set by main.py to trigger trade checks
def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

background_loop = asyncio.new_event_loop()
t = threading.Thread(target=start_background_loop, args=(background_loop,), daemon=True)
t.start()

trade_callback = None

def register_trade_callback(cb):
    global trade_callback
    trade_callback = cb

def setupSignalRConnection(authToken, contractId):
    global rtc_connection, connection_started

    if not authToken or not contractId:
        logger.error("[SignalR] Missing authToken or contractId.")
        return

    if connection_started:
        logger.info("[SignalR] WebSocket is already connected. Skipping initialization.")
        return

    marketHubUrl = f"https://rtc.topstepx.com/hubs/market?access_token={authToken}"
    rtc_connection = HubConnectionBuilder()\
        .with_url(marketHubUrl)\
        .with_automatic_reconnect({"keep_alive_interval": 10, "reconnect_interval": 5})\
        .build()

    rtc_connection.on_open(lambda: on_open_handler(contractId))
    rtc_connection.on_close(on_close_handler)
    rtc_connection.on("GatewayQuote", handle_quote_event)
    rtc_connection.on("GatewayTrade", handle_trade_event)
    rtc_connection.on("GatewayDepth", handle_depth_event)

    try:
        rtc_connection.start()
        connection_started = True
        logger.info("[SignalR] ✅ Connected successfully.")
    except Exception as e:
        logger.error(f"[SignalR] ❌ Connection error: {e}")
        connection_started = False

def on_open_handler(contractId):
    logger.info(f"[SignalR] WebSocket opened. Subscribing to contract {contractId}")
    try:
        rtc_connection.send("SubscribeContractQuotes", [contractId])
        rtc_connection.send("SubscribeContractTrades", [contractId])
        rtc_connection.send("SubscribeContractMarketDepth", [contractId])
    except Exception as e:
        logger.error(f"[SignalR] Subscription error: {e}")

def on_close_handler():
    global connection_started
    connection_started = False
    logger.warning("[SignalR] WebSocket connection closed.")

def handle_quote_event(args):
    try:
        quote_data.append(args)
        logger.debug(f"[Quote] {args}")
    except Exception as e:
        logger.error(f"[SignalR] Quote handler error: {e}")

def handle_trade_event(args):
    try:
        trade_data.append(args)
        logger.debug(f"[Trade] {args}")
        if trade_callback:
            asyncio.run_coroutine_threadsafe(trade_callback(args), background_loop)
    except Exception as e:
        logger.error(f"[SignalR] Trade handler error: {e}")

def handle_depth_event(args):
    try:
        depth_data.append(args)
        logger.debug(f"[Depth] {args}")
    except Exception as e:
        logger.error(f"[SignalR] Depth handler error: {e}")

def closeSignalRConnection():
    global rtc_connection, connection_started
    if rtc_connection:
        rtc_connection.stop()
        logger.info("[SignalR] Connection closed.")
        rtc_connection = None
        connection_started = False

def get_event_data():
    return {
        "quotes": quote_data[-100:],  # last 100 items
        "trades": trade_data[-100:],
        "depth": depth_data[-100:]
    }

# Module exports
__all__ = [
    "setupSignalRConnection",
    "closeSignalRConnection",
    "get_event_data",
    "register_trade_callback"
]
