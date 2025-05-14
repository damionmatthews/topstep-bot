# Improved signalRClient.py
from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.transport.websockets.websocket_transport import WebsocketTransport
from threading import Event
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variables
signalrEvents = {}
connection_started = False
rtc_connection = None
shutdown_event = Event()

# Setup connection
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

    # Event Handlers
    rtc_connection.on_open(lambda: on_open_handler(contractId))
    rtc_connection.on_close(on_close_handler)
    rtc_connection.on("GatewayQuote", handle_quote_event)
    rtc_connection.on("GatewayTrade", handle_trade_event)
    rtc_connection.on("GatewayDepth", handle_depth_event)

    try:
        rtc_connection.start()
        connection_started = True
        if rtc_connection.connected:
            logger.info("[SignalR] ✅ Connected successfully.")
        else:
            logger.error("[SignalR] ❌ Failed to connect.")
    except Exception as e:
        logger.error(f"[SignalR] ❌ Connection error: {e}")
        connection_started = False

# Subscription logic
def on_open_handler(contractId):
    global rtc_connection
    logger.info("[SignalR] 📡 Subscribing to channels...")
    rtc_connection.send("SubscribeContractQuotes", [contractId])
    rtc_connection.send("SubscribeContractTrades", [contractId])
    rtc_connection.send("SubscribeContractMarketDepth", [contractId])

def on_close_handler():
    global connection_started
    logger.warning("[SignalR] 🔌 Disconnected. Attempting to reconnect...")
    connection_started = False

# Event Handlers
def handle_quote_event(args):
    logger.info(f"[Quote Event] {args}")
    signalrEvents["quote"] = args

def handle_trade_event(args):
    logger.info(f"[Trade Event] {args}")
    signalrEvents["trade"] = args

def handle_depth_event(args):
    logger.info(f"[Depth Event] {args}")
    signalrEvents["depth"] = args

# Graceful shutdown
def closeSignalRConnection():
    global rtc_connection, connection_started
    if rtc_connection:
        rtc_connection.stop()
        connection_started = False
        logger.info("[SignalR] 🔌 Connection stopped.")
    else:
        logger.warning("[SignalR] Connection was not active.")

# Access event data
def get_event_data(event_type):
    return signalrEvents.get(event_type, None)

# Module exports
__all__ = [
    "setupSignalRConnection",
    "closeSignalRConnection",
    "get_event_data",
    "signalrEvents"
]
