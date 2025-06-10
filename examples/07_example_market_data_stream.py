import asyncio
import logging
import os
import time
import pprint
import argparse
from typing import Any, Optional, List

# Ensure the main project directory is on the path if running from examples folder
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topstep_client import (
    APIClient,
    MarketDataStream,
    StreamConnectionState,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    TopstepAPIError
)
# from topstep_client.schemas import Contract # If needed for contract details

# --- Configure Logging ---
# Logging setup is done globally near the end in if __name__ == "__main__"
logger = logging.getLogger(__name__) # Will be configured by basicConfig later

# --- Default Configuration (as per user feedback for testing) ---
DEFAULT_ACCOUNT_ID = 8027309
DEFAULT_CONTRACT_ID = "CON.F.US.ENQ.M25" # E-mini NASDAQ

# --- Global for Callback context (optional, but helpful for logging) ---
current_contract_streaming: Optional[str] = None

# --- MarketDataStream Callbacks ---
def handle_live_quote_callback(quote_data_list: List[Any]):
    logger.info(f"--- LIVE QUOTE ({current_contract_streaming or 'N/A'}) ---")
    for quote_data in quote_data_list: # Stream sends a list of updates
        # Assuming quote_data is a dict with keys like 'bp', 'bs', 'ap', 'as', 'price', 'volume', 'timestamp'
        logger.info(
            f"  Bid: {quote_data.get('bp')} @ {quote_data.get('bs')} | "
            f"Ask: {quote_data.get('ap')} @ {quote_data.get('as')} | "
            f"Last: {quote_data.get('price')} @ {quote_data.get('volume')} "
            f"(ts: {quote_data.get('timestamp')})"
        )
        # logger.debug(pprint.pformat(quote_data))

def handle_live_trade_callback(trade_data_list: List[Any]):
    logger.info(f"--- LIVE TRADE ({current_contract_streaming or 'N/A'}) ---")
    for trade_data in trade_data_list: # Stream sends a list of updates
        # Assuming trade_data is a dict with keys like 'price', 'size', 'aggressorSide', 'timestamp'
        logger.info(
            f"  Price: {trade_data.get('price')} | Size: {trade_data.get('size')} | "
            f"Aggressor: {trade_data.get('aggressorSide')} (ts: {trade_data.get('timestamp')})"
        )
        # logger.debug(pprint.pformat(trade_data))

def handle_live_depth_callback(depth_data_list: List[Any]):
    logger.info(f"--- LIVE DEPTH ({current_contract_streaming or 'N/A'}) ---")
    for depth_data in depth_data_list: # Stream sends a list of updates
        # Assuming depth_data is a dict with keys like 'bids': [[price, size], ...], 'asks': [[price, size], ...]
        bids = depth_data.get('bids', [])
        asks = depth_data.get('asks', [])
        logger.info(f"  Top 3 Bids: {bids[:3]}")
        logger.info(f"  Top 3 Asks: {asks[:3]}")
        # logger.debug(pprint.pformat(depth_data))

def handle_market_stream_state_change_callback(state: StreamConnectionState):
    logger.info(f"MarketDataStream for '{current_contract_streaming or 'N/A'}' state changed to: {state.value}")
    # If state is CONNECTED, this is a good place to ensure subscriptions are active
    # The MarketDataStream's _on_open method already handles resubscribing from its self._subscriptions set.

# Our BaseStream's _on_error handles generic connection errors.
# A specific on_error_callback could be added to MarketDataStream for data processing errors if needed.

async def run_market_data_example(
    contract_id_to_stream: str,
    sub_quotes: bool,
    sub_trades: bool,
    sub_depth: bool,
    duration_seconds: int
):
    global current_contract_streaming
    current_contract_streaming = contract_id_to_stream

    logger.info(f"--- Example 07: Dedicated Real-Time Market Data Stream ---") # Updated example number
    logger.info(f"Contract: {contract_id_to_stream}")
    logger.info(f"Subscriptions: Quotes={sub_quotes}, Trades={sub_trades}, Depth={sub_depth}")
    logger.info(f"Running for {duration_seconds} seconds.")

    api_client: Optional[APIClient] = None
    stream: Optional[MarketDataStream] = None

    try:
        logger.info("Initializing APIClient...")
        api_client = APIClient() # Credentials from environment variables
        await api_client.authenticate()
        logger.info("APIClient authenticated.")

        logger.info(f"Initializing MarketDataStream for contract: {contract_id_to_stream}")
        stream = MarketDataStream(
            api_client=api_client,
            on_state_change_callback=handle_market_stream_state_change_callback,
            on_quote_callback=handle_live_quote_callback if sub_quotes else None,
            on_trade_callback=handle_live_trade_callback if sub_trades else None,
            on_depth_callback=handle_live_depth_callback if sub_depth else None,
            debug=True # Enable more verbose logging from the stream
        )
        logger.info("MarketDataStream initialized.")

        if not await stream.start():
            logger.error(f"Failed to start MarketDataStream (current status: {stream.current_state.name}). Exiting.")
            return

        logger.info("MarketDataStream start initiated. Attempting to subscribe...")
        # Subscribe to the contract after the stream is started and ideally connected.
        # The stream's on_open method will also try to re-establish subscriptions if they were set before connecting.
        stream.subscribe_contract(contract_id_to_stream)

        logger.info(f"Monitoring for market data on {contract_id_to_stream} for {duration_seconds} seconds... Press Ctrl+C to stop earlier.")

        end_loop_time = time.monotonic() + duration_seconds
        token_update_interval = 1800 # seconds (30 minutes)
        last_token_update_time = time.monotonic()

        while time.monotonic() < end_loop_time:
            if stream.current_state != StreamConnectionState.CONNECTED:
                logger.warning(f"MarketDataStream is not connected (State: {stream.current_state.name}). Waiting for reconnect...")

            current_time = time.monotonic()
            if (current_time - last_token_update_time) > token_update_interval:
                logger.info("Attempting periodic token update for MarketDataStream...")
                try:
                    await stream.update_token() # Will re-fetch from APIClient if necessary
                    logger.info("MarketDataStream token update call made.")
                except Exception as e_token_update:
                    logger.error(f"Error during MarketDataStream token update: {e_token_update}")
                last_token_update_time = current_time

            await asyncio.sleep(1) # Keep main thread alive
            # Corrected modulo check for logging status: should be against loop counter or elapsed time, not monotonic time
            # For simplicity, let's use a simple counter approach for logging interval
            if int(time.monotonic() - (end_loop_time - duration_seconds)) % 10 == 0:
                 logger.info(f"MarketDataStream running... Current state: {stream.current_state.name} (approx. {int(end_loop_time - time.monotonic())}s remaining)")


        logger.info("Example duration finished.")

    # ConfigurationError is not a defined exception in the client
    # except ConfigurationError as e_conf:
    #     logger.error(f"CONFIGURATION ERROR: {e_conf}")
    except AuthenticationError as e_auth:
        logger.error(f"AUTHENTICATION FAILED: {e_auth}")
    except APIResponseParsingError as e_parse:
        logger.error(f"API RESPONSE PARSING ERROR: {e_parse}")
        if hasattr(e_parse, 'raw_response_text') and e_parse.raw_response_text:
            logger.error(f"Raw problematic response text (preview): {e_parse.raw_response_text[:500]}")
    except APIRequestError as e_api:
        logger.error(f"API REQUEST ERROR: {e_api}")
    except TopstepAPIError as e_lib:
        logger.error(f"TOPSTEP API LIBRARY ERROR: {e_lib}")
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down stream...")
    except Exception as e_generic:
        logger.error(f"AN UNEXPECTED ERROR OCCURRED: {e_generic}", exc_info=True)
    finally:
        if stream:
            logger.info(f"Stopping MarketDataStream (current state: {stream.current_state.name})...")
            await stream.stop()
        if api_client:
            logger.info("Closing APIClient session...")
            await api_client.close()
        logger.info("Market data stream example finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream real-time market data for a TopStep contract.")
    parser.add_argument(
        "--contract_id",
        type=str,
        default=DEFAULT_CONTRACT_ID,
        help=f"Contract ID to subscribe to (e.g., CON.F.US.NQ.M25). Default: {DEFAULT_CONTRACT_ID}"
    )
    parser.add_argument(
        "--no_quotes",
        action="store_false",
        dest="subscribe_quotes",
        help="Disable real-time quote (best bid/ask) updates. (Quotes enabled by default)"
    )
    parser.set_defaults(subscribe_quotes=True) # Quotes are subscribed by default
    parser.add_argument(
        "--trades",
        action="store_true",
        help="Subscribe to real-time trade execution updates."
    )
    parser.add_argument(
        "--depth",
        action="store_true",
        help="Subscribe to real-time market depth (DOM) updates."
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60, # Reduced default duration for quicker test
        help="How long (in seconds) to run the stream. Default: 60"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG level logging for the example script and topstep_client."
    )
    args = parser.parse_args()

    # Setup logging level based on debug flag
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s [%(levelname)s]: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True # Override any previous basicConfig by libraries
    )
    # Ensure our script's logger respects the level if force=True isn't enough for root logger
    logger.setLevel(log_level)
    logger.info(f"Logging level set to: {logging.getLevelName(logger.getEffectiveLevel())}")

    # Set logging for topstep_client components if debug is enabled
    if args.debug:
        logging.getLogger("topstep_client").setLevel(logging.DEBUG)
        # More specific components if needed, assuming they use child loggers
        logging.getLogger("topstep_client.api_client").setLevel(logging.DEBUG)
        logging.getLogger("topstep_client.streams").setLevel(logging.DEBUG)
        # Or specific stream classes if they have their own named loggers
        logging.getLogger("MarketDataStream").setLevel(logging.DEBUG)
        logging.getLogger("UserHubStream").setLevel(logging.DEBUG)
        logging.getLogger("BaseStream").setLevel(logging.DEBUG)
        # APIClient logger is usually topstep_client.api_client, already covered by getLogger("topstep_client")


    if not args.contract_id:
        logger.error("A contract_id must be provided via --contract_id or ensure DEFAULT_CONTRACT_ID is set.")
        sys.exit(1)

    if not (args.subscribe_quotes or args.trades or args.depth):
        logger.warning("No data types selected for subscription (quotes, trades, depth). Stream will connect but show no market data events other than state changes.")

    # Load .env file from project root
    try:
        from dotenv import load_dotenv
        dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path)
            logger.info(f".env file loaded from {dotenv_path}")
        else:
            logger.info(".env file not found at project root. Relying on environment variables being set externally.")
    except ImportError:
        logger.info("dotenv library not found. Relying on environment variables being set externally.")

    asyncio.run(run_market_data_example(
        contract_id_to_stream=args.contract_id,
        sub_quotes=args.subscribe_quotes,
        sub_trades=args.trades,
        sub_depth=args.depth,
        duration_seconds=args.duration
    ))
