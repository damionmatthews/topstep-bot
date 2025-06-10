import asyncio
import logging
import threading
from enum import Enum
from typing import Optional, Callable, Any, List

from signalrcore.hub_connection_builder import HubConnectionBuilder

from .api_client import APIClient # Assuming APIClient manages token
from .exceptions import TopstepAPIError, APIRequestError, AuthenticationError

logger = logging.getLogger(__name__)

class StreamConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"
    STOPPING = "STOPPING"

class BaseStream:
    def __init__(
        self,
        api_client: APIClient,
        hub_name: str,
        on_state_change_callback: Optional[Callable[[StreamConnectionState], None]] = None,
        debug: bool = False
    ):
        self._api_client = api_client
        self._hub_name = hub_name
        self._base_rtc_url = "wss://rtc.topstepx.com/hubs/"
        self._connection: Optional[HubConnectionBuilder] = None
        self._current_token: Optional[str] = None
        self._state = StreamConnectionState.DISCONNECTED
        self._on_state_change_callback = on_state_change_callback
        self._debug = debug
        self._logger = logging.getLogger(self.__class__.__name__)
        if self._debug:
            self._logger.setLevel(logging.DEBUG)
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5 # Example
        self._initial_connection_lock = asyncio.Lock()
        self._stop_requested = False

    def _update_state(self, new_state: StreamConnectionState):
        if self._state != new_state:
            self._state = new_state
            self._logger.info(f"Stream state changed to: {new_state.value}")
            if self._on_state_change_callback:
                try:
                    self._on_state_change_callback(new_state)
                except Exception as e:
                    self._logger.error(f"Error in on_state_change_callback: {e}")

    async def _ensure_token(self) -> str:
        # This assumes APIClient has a method to get current token or re-authenticate
        # For simplicity, let's assume self._api_client._session_token exists and is kept fresh.
        # In a real scenario, you might need to call a method on api_client that refreshes if needed.
        if not self._api_client._session_token: # Accessing protected member for example
            self._logger.info("Token missing, attempting to authenticate via APIClient.")
            await self._api_client.authenticate()
        if not self._api_client._session_token:
            raise AuthenticationError("Failed to obtain token for stream.")
        return self._api_client._session_token

    async def _build_hub_url(self) -> str:
        self._current_token = await self._ensure_token()
        return f"{self._base_rtc_url}{self._hub_name}?access_token={self._current_token}"

    def _setup_handlers(self):
        """Abstract method to be implemented by subclasses to set up specific message handlers."""
        if not self._connection:
            return
        self._connection.on_open(self._on_open)
        self._connection.on_close(self._on_close)
        self._connection.on_error(self._on_error) # Generic error handler for the connection itself

    def _on_open(self):
        self._logger.info(f"Successfully connected to {self._hub_name}.")
        self._update_state(StreamConnectionState.CONNECTED)
        self._reconnect_attempts = 0
        # Subclasses should override to send subscription messages

    def _on_close(self):
        self._logger.warning(f"{self._hub_name} connection closed.")
        if not self._stop_requested:
            self._update_state(StreamConnectionState.RECONNECTING)
            # Consider implementing a reconnect strategy here if signalrcore's isn't sufficient
        else:
            self._update_state(StreamConnectionState.DISCONNECTED)

    def _on_error(self, error_data: Any):
        self._logger.error(f"{self._hub_name} connection error: {error_data}")
        # If it's a list, it might be from a message handler error
        if isinstance(error_data, list) and len(error_data) > 0:
            error_msg = str(error_data[0]) if error_data[0] else "Unknown SignalR error"
        elif isinstance(error_data, Exception):
            error_msg = str(error_data)
        else:
            error_msg = str(error_data)

        # Check for token expiry indication (example, adjust based on actual messages)
        if "401" in error_msg or "Unauthorized" in error_msg:
            self._logger.warning("Stream reported unauthorized, likely token expired. Attempting reconnect with new token.")
            self._api_client._session_token = None # Force token refresh
            if not self._stop_requested:
                 asyncio.create_task(self.start()) # Re-start the connection process
            return

        self._update_state(StreamConnectionState.FAILED)
        # Potentially trigger a reconnect attempt or notify user

    async def start(self) -> bool:
        async with self._initial_connection_lock:
            if self._state == StreamConnectionState.CONNECTED:
                self._logger.info(f"{self._hub_name} stream is already connected.")
                return True
            if self._state == StreamConnectionState.CONNECTING:
                self._logger.info(f"{self._hub_name} stream is already connecting.")
                return True # Or wait for completion if preferred

            self._stop_requested = False
            self._update_state(StreamConnectionState.CONNECTING)

            try:
                hub_url = await self._build_hub_url()
                self._logger.info(f"Connecting to {self._hub_name} at {hub_url.split('?')[0]}...") # Hide token from log

                self._connection = HubConnectionBuilder() \
                    .with_url(hub_url, options={
                        "access_token_factory": lambda: self._ensure_token(),
                        "skip_negotiation": True # As per tsxapi4py
                    }) \
                    .with_automatic_reconnect({
                        "type": "interval",
                        "keep_alive_interval": 10,
                        "intervals": [0, 2, 5, 10, 20, 30] # seconds
                    }) \
                    .build()

                self._setup_handlers() # Call before start

                # Running self._connection.start() in a separate thread
                # because it's a blocking call in the signalrcore library
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._connection.start)
                # Note: _on_open will set state to CONNECTED if successful

                # A small delay to allow connection to establish and _on_open to fire
                # This is a bit of a workaround for the blocking start call.
                # A more robust solution might involve futures or other sync primitives.
                await asyncio.sleep(2)

                if self._state != StreamConnectionState.CONNECTED:
                    self._logger.error(f"Failed to connect to {self._hub_name} after start attempt. Current state: {self._state}")
                    self._update_state(StreamConnectionState.FAILED) # Ensure state is FAILED if not connected
                    return False
                return True

            except AuthenticationError as e:
                self._logger.error(f"Authentication failed during {self._hub_name} stream start: {e}")
                self._update_state(StreamConnectionState.FAILED)
                return False
            except Exception as e:
                self._logger.error(f"Error starting {self._hub_name} stream: {e}", exc_info=True)
                self._update_state(StreamConnectionState.FAILED)
                return False

    async def stop(self):
        self._logger.info(f"Stopping {self._hub_name} stream...")
        self._stop_requested = True
        self._update_state(StreamConnectionState.STOPPING)
        if self._connection:
            try:
                # Running self._connection.stop() in a separate thread as it can block
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._connection.stop)
                self._logger.info(f"{self._hub_name} stream stopped.")
            except Exception as e:
                self._logger.error(f"Error stopping {self._hub_name} stream: {e}")
            finally:
                self._connection = None
        self._update_state(StreamConnectionState.DISCONNECTED)

    @property
    def current_state(self) -> StreamConnectionState:
        return self._state

    async def update_token(self, new_token: Optional[str] = None):
        """Updates the token for the stream. If new_token is None, it will try to fetch from APIClient."""
        self._logger.info(f"Updating token for {self._hub_name} stream.")
        if new_token:
            self._current_token = new_token
            self._api_client._session_token = new_token # Keep APIClient in sync if stream sets it
        else:
            try:
                self._current_token = await self._ensure_token()
            except AuthenticationError as e:
                self._logger.error(f"Failed to refresh token for stream: {e}")
                self._update_state(StreamConnectionState.FAILED) # Or some other appropriate state
                return

        if self._connection and self._state == StreamConnectionState.CONNECTED:
            # SignalR typically requires restart for new token if using direct WebSocket (skip_negotiation=True)
            self._logger.info("Reconnecting stream to apply new token...")
            await self.stop() # Stop current connection
            await self.start() # Re-start with new token via _build_hub_url
        elif self._connection and self._state != StreamConnectionState.DISCONNECTED:
             self._logger.info("Stream not connected, new token will be used on next connection attempt.")

class MarketDataStream(BaseStream):
    def __init__(
        self,
        api_client: APIClient,
        on_state_change_callback: Optional[Callable[[StreamConnectionState], None]] = None,
        on_quote_callback: Optional[Callable[[List[Any]], None]] = None,
        on_trade_callback: Optional[Callable[[List[Any]], None]] = None,
        on_depth_callback: Optional[Callable[[List[Any]], None]] = None,
        debug: bool = False
    ):
        super().__init__(api_client, "market", on_state_change_callback, debug)
        self._on_quote_callback = on_quote_callback
        self._on_trade_callback = on_trade_callback
        self._on_depth_callback = on_depth_callback
        self._subscriptions = set() # Set of contract IDs

    def _setup_handlers(self):
        super()._setup_handlers()
        if not self._connection: return
        self._connection.on("GatewayQuote", self._handle_quote)
        self._connection.on("GatewayTrade", self._handle_trade)
        self._connection.on("GatewayDepth", self._handle_depth)

    def _on_open(self):
        super()._on_open()
        # Resubscribe to all contracts if connection is re-established
        if self._subscriptions:
            self._logger.info(f"Resubscribing to {len(self._subscriptions)} contracts on (re)connect.")
            for contract_id in list(self._subscriptions):
                self.subscribe_contract(contract_id, is_resubscribe=True)

    def _handle_quote(self, data: List[Any]):
        if self._debug: self._logger.debug(f"Quote data received: {data}")
        if self._on_quote_callback: self._on_quote_callback(data)

    def _handle_trade(self, data: List[Any]):
        if self._debug: self._logger.debug(f"Trade data received: {data}")
        if self._on_trade_callback: self._on_trade_callback(data)

    def _handle_depth(self, data: List[Any]):
        if self._debug: self._logger.debug(f"Depth data received: {data}")
        if self._on_depth_callback: self._on_depth_callback(data)

    def subscribe_contract(self, contract_id: str, is_resubscribe:bool=False):
        if not contract_id:
            self._logger.warning("Contract ID is required to subscribe.")
            return

        if not is_resubscribe:
             self._subscriptions.add(contract_id)

        if self._state == StreamConnectionState.CONNECTED and self._connection:
            try:
                self._logger.info(f"Subscribing to {contract_id}...")
                self._connection.send("SubscribeContractQuotes", [contract_id])
                self._connection.send("SubscribeContractTrades", [contract_id])
                self._connection.send("SubscribeContractMarketDepth", [contract_id])
                self._logger.info(f"Successfully sent subscription requests for {contract_id}.")
            except Exception as e:
                self._logger.error(f"Error subscribing to {contract_id}: {e}")
        else:
            self._logger.info(f"Stream not connected. Subscription for {contract_id} will occur upon connection.")

    def unsubscribe_contract(self, contract_id: str):
        if contract_id in self._subscriptions:
            self._subscriptions.remove(contract_id)
            if self._state == StreamConnectionState.CONNECTED and self._connection:
                try:
                    self._logger.info(f"Unsubscribing from {contract_id}...")
                    self._connection.send("UnsubscribeContractQuotes", [contract_id])
                    self._connection.send("UnsubscribeContractTrades", [contract_id])
                    self._connection.send("UnsubscribeContractMarketDepth", [contract_id])
                    self._logger.info(f"Successfully sent unsubscribe requests for {contract_id}.")
                except Exception as e:
                    self._logger.error(f"Error unsubscribing from {contract_id}: {e}")
            else:
                self._logger.info(f"Stream not connected. Unsubscription for {contract_id} noted.")
        else:
            self._logger.info(f"Not subscribed to {contract_id}, no action to unsubscribe.")

class UserHubStream(BaseStream):
    def __init__(
        self,
        api_client: APIClient,
        account_id_to_watch: Optional[int] = None, # Though TopStepX UserHub doesn't require explicit account subscription
        on_state_change_callback: Optional[Callable[[StreamConnectionState], None]] = None,
        on_user_trade_callback: Optional[Callable[[List[Any]], None]] = None,
        on_user_order_callback: Optional[Callable[[List[Any]], None]] = None,
        on_user_position_callback: Optional[Callable[[List[Any]], None]] = None,
        debug: bool = False
    ):
        super().__init__(api_client, "user", on_state_change_callback, debug)
        self._on_user_trade_callback = on_user_trade_callback
        self._on_user_order_callback = on_user_order_callback
        self._on_user_position_callback = on_user_position_callback
        # self.account_id_to_watch = account_id_to_watch # Not directly used for subscriptions in TopStepX UserHub

    def _setup_handlers(self):
        super()._setup_handlers()
        if not self._connection: return
        self._connection.on("GatewayUserTrade", self._handle_user_trade)
        self._connection.on("GatewayUserOrder", self._handle_user_order)
        self._connection.on("GatewayUserPosition", self._handle_user_position)

    # _on_open for UserHub doesn't need to send specific subscriptions usually,
    # as the user hub sends data based on the authenticated user token.

    def _handle_user_trade(self, data: List[Any]):
        if self._debug: self._logger.debug(f"User trade data received: {data}")
        if self._on_user_trade_callback: self._on_user_trade_callback(data)

    def _handle_user_order(self, data: List[Any]):
        if self._debug: self._logger.debug(f"User order data received: {data}")
        if self._on_user_order_callback: self._on_user_order_callback(data)

    def _handle_user_position(self, data: List[Any]):
        if self._debug: self._logger.debug(f"User position data received: {data}")
        if self._on_user_position_callback: self._on_user_position_callback(data)
