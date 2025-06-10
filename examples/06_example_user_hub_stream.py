import asyncio
import logging
import os
import time
import pprint # For pretty printing the received data
from typing import Any, Optional, List, Dict

# Ensure the main project directory is on the path if running from examples folder
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topstep_client import (
    APIClient,
    UserHubStream,
    StreamConnectionState,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    TopstepAPIError
)
# Schemas might be useful for type hinting inside callbacks if we parse the data
# from topstep_client import OrderDetails, Account, Position, Trade # etc.

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s [%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Configuration (as per user feedback) ---
ACCOUNT_ID_TO_MONITOR = 8027309
# CONTRACT_ID is not directly used by UserHubStream for subscriptions but might be relevant for context
# CONTRACT_ID = "CON.F.US.ENQ.M25"

# --- Callback Function Definitions ---
# Note: The stream passes a List[Any] to these callbacks, where Any is usually a dict.
def handle_user_order_update_callback(order_data_list: List[Any]):
    logger.info(f"--- USER ORDER Update Received (Account: {ACCOUNT_ID_TO_MONITOR}) ---")
    for order_data in order_data_list:
        logger.info(pprint.pformat(order_data, indent=2, width=120))
        # Example: Further processing with Pydantic models if desired
        # try:
        #     from topstep_client import OrderDetails # Import here to avoid circular if not used elsewhere
        #     order = OrderDetails.parse_obj(order_data)
        #     logger.info(f"Parsed Order ID: {order.id}, Status: {order.status}")
        # except Exception as e:
        #     logger.error(f"Error parsing order data: {e}")

def handle_user_position_update_callback(position_data_list: List[Any]):
    logger.info(f"--- USER POSITION Update Received (Account: {ACCOUNT_ID_TO_MONITOR}) ---")
    for position_data in position_data_list:
        logger.info(pprint.pformat(position_data, indent=2, width=120))
        # Example: Further processing with Pydantic models
        # try:
        #     from topstep_client import Position # Assuming a Position schema exists
        #     position = Position.parse_obj(position_data)
        #     logger.info(f"Parsed Position for {position.contract_id}: Qty={position.quantity}")
        # except Exception as e:
        #     logger.error(f"Error parsing position data: {e}")


def handle_user_trade_execution_callback(trade_data_list: List[Any]):
    logger.info(f"--- USER TRADE Execution Received (Account: {ACCOUNT_ID_TO_MONITOR}) ---")
    for trade_data in trade_data_list:
        logger.info(pprint.pformat(trade_data, indent=2, width=120))
        # Example: Further processing with Pydantic models
        # try:
        #     from topstep_client import Trade # Assuming a Trade schema exists
        #     trade = Trade.parse_obj(trade_data)
        #     logger.info(f"Parsed Trade ID: {trade.id}, Price: {trade.price}")
        # except Exception as e:
        #     logger.error(f"Error parsing trade data: {e}")

def handle_user_stream_state_change_callback(state: StreamConnectionState):
    logger.info(f"User Hub Stream state changed to: {state.value} for account {ACCOUNT_ID_TO_MONITOR}")
    # You could add logic here, e.g., if state becomes CONNECTED, log it or perform actions

# Note: Our BaseStream's _on_error handles generic connection errors.
# If UserHubStream needs a more specific error callback for data processing errors,
# it would need to be added to the UserHubStream class itself.

async def main():
    logger.info(f"--- Example 06: Real-Time User Data for Account ID: {ACCOUNT_ID_TO_MONITOR} ---") # Updated example number
    api_client: Optional[APIClient] = None
    stream: Optional[UserHubStream] = None

    try:
        logger.info("Initializing APIClient...")
        api_client = APIClient() # Credentials from environment variables
        await api_client.authenticate()
        logger.info("APIClient authenticated.")

        logger.info(f"Initializing UserHubStream for Account ID: {ACCOUNT_ID_TO_MONITOR}...")
        stream = UserHubStream(
            api_client=api_client,
            account_id_to_watch=ACCOUNT_ID_TO_MONITOR, # Passed for context, not strictly for subscription filtering by client
            on_user_trade_callback=handle_user_trade_execution_callback,
            on_user_order_callback=handle_user_order_update_callback,
            on_user_position_callback=handle_user_position_update_callback,
            on_state_change_callback=handle_user_stream_state_change_callback,
            debug=True # Enable more verbose logging from the stream
        )
        logger.info("UserHubStream initialized.")

        if not await stream.start():
            logger.error(f"Failed to start UserHubStream (current status: {stream.current_state.name}). Exiting example.")
            return

        logger.info("User Hub stream started. Monitoring for 60 seconds... Press Ctrl+C to stop earlier.")
        logger.info("Perform some actions on your account (e.g., place/cancel an order) in the trading platform to see updates.")

        main_loop_duration = 60  # seconds
        token_update_interval = 1800 # seconds (30 minutes), less frequent as stream should handle token internally
        last_token_update_time = time.monotonic()

        for i in range(main_loop_duration):
            if stream.current_state != StreamConnectionState.CONNECTED:
                logger.warning(f"UserHubStream is not connected (State: {stream.current_state.name}). Waiting for reconnect...")

            # Periodically check if token needs manual update (less critical with access_token_factory)
            current_time = time.monotonic()
            if (current_time - last_token_update_time) > token_update_interval:
                logger.info("Attempting periodic token update for UserHubStream...")
                try:
                    await stream.update_token() # Will re-fetch from APIClient if necessary
                    logger.info("UserHubStream token update call made.")
                except Exception as e_token_update:
                    logger.error(f"Error during UserHubStream token update: {e_token_update}")
                last_token_update_time = current_time

            await asyncio.sleep(1) # Keep main thread alive and check status
            if i % 10 == 0: # Log status every 10 seconds
                 logger.info(f"UserHubStream running... Current state: {stream.current_state.name}")


        logger.info("Example duration finished.")

    # ConfigurationError is not a defined exception in the client, so this will be a NameError
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
        logger.info("Keyboard interrupt received. Shutting down...")
    except Exception as e_generic:
        logger.error(f"AN UNEXPECTED ERROR OCCURRED: {e_generic}", exc_info=True)
    finally:
        if stream:
            logger.info(f"Stopping User Hub stream (current state: {stream.current_state.name})...")
            await stream.stop()
        if api_client:
            logger.info("Closing APIClient session...")
            await api_client.close()
        logger.info("User Hub data example finished.")


if __name__ == "__main__":
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

    asyncio.run(main())
