from signalrcore.hub_connection_builder import HubConnectionBuilder
from threading import Thread
import logging
from main import check_and_close_active_trade, strategy_that_opened_trade
import asyncio

logger = logging.getLogger(__name__)

rtc_connection = None
connection_started = False
quote_data = []
trade_data = []
depth_data = []

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
        if strategy_that_opened_trade:
            asyncio.create_task(check_and_close_active_trade(strategy_that_opened_trade))
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

def handle_trade_event(args):
    try:
        trade_data.append(args)
        logger.debug(f"[Trade] {args}")
        if strategy_that_opened_trade:
            asyncio.create_task(check_and_close_active_trade(strategy_that_opened_trade))
    except Exception as e:
        logger.error(f"[SignalR] Trade handler error: {e}")
        
# Module exports
__all__ = [
    "setupSignalRConnection",
    "closeSignalRConnection",
    "get_event_data",
    "signalrEvents"
]
