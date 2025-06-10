import asyncio
import logging
import os
import time
from typing import Optional, List

# Ensure the main project directory is on the path if running from examples folder
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topstep_client import (
    APIClient,
    OrderRequest,
    OrderDetails,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    TopstepAPIError,
    OrderPlacementError
)
from topstep_client.schemas import Contract # For type hinting if needed

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s [%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Configuration (as per user feedback) ---
ACCOUNT_ID = 8027309
CONTRACT_ID = "CON.F.US.ENQ.M25" # E-mini NASDAQ
TRAIL_TICKS = 100 # Trailing distance in ticks, e.g., 100 ticks for NQ = 25 points

async def print_order_details_example(order_details: Optional[OrderDetails], context: str = ""):
    if order_details:
        logger.info(f"{context} Order Details for ID {order_details.id}:")
        logger.info(f"  Status: {order_details.status}")
        logger.info(f"  Contract ID: {order_details.contract_id}")
        logger.info(f"  Side: {order_details.side}, Type: {order_details.type}")
        logger.info(f"  Quantity: {order_details.quantity}, FilledQty: {order_details.filled_quantity}")

        limit_price_info = 'N/A'
        if hasattr(order_details, 'limit_price') and order_details.limit_price is not None:
            limit_price_info = f"{order_details.limit_price:.2f}"
        logger.info(f"  Limit Price: {limit_price_info}")

        stop_price_info = 'N/A'
        if hasattr(order_details, 'stop_price') and order_details.stop_price is not None:
            stop_price_info = f"{order_details.stop_price:.2f}"
        logger.info(f"  Stop Price: {stop_price_info}")

        trailing_distance_info = 'N/A'
        if hasattr(order_details, 'trailing_distance') and order_details.trailing_distance is not None:
            trailing_distance_info = f"{order_details.trailing_distance}"
        logger.info(f"  Trailing Distance: {trailing_distance_info}")

        logger.info(f"  Avg Fill Price: {order_details.average_fill_price if order_details.average_fill_price is not None else 'N/A'}")
    else:
        logger.warning(f"{context} Order details not found or could not be retrieved.")


async def main():
    logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    logger.warning("!!! WARNING: THIS SCRIPT WILL PLACE A LIVE TRAILING STOP ORDER!            !!!")
    logger.warning("!!! ENSURE YOU ARE USING A DEMO ACCOUNT OR UNDERSTAND THE RISK!          !!!")
    logger.warning(f"!!! Account ID: {ACCOUNT_ID}, Contract ID: {CONTRACT_ID}, Trail: {TRAIL_TICKS} ticks !!!")
    logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

    confirm = input("Type 'YES_PLACE_TRAILING_STOP' to continue: ")
    if confirm != "YES_PLACE_TRAILING_STOP":
        logger.info("Trailing stop order placement cancelled by user.")
        return

    logger.info(f"--- Example 08: Place Trailing Stop Order for Acct {ACCOUNT_ID} on {CONTRACT_ID} ---")
    api_client: Optional[APIClient] = None
    placed_order_id: Optional[int] = None
    polled_details: Optional[OrderDetails] = None # Initialize for finally block

    try:
        logger.info("Initializing APIClient...")
        api_client = APIClient() # Credentials from environment variables
        await api_client.authenticate()
        logger.info("APIClient authenticated.")

        # --- Place a Trailing Stop SELL Order ---
        # For a SELL trailing stop, it's placed below the current market to protect a long position,
        # or to enter a short position if the market breaks downwards and then retraces by the trail amount.

        logger.info(f"Attempting to place SELL TrailingStop order, 1 lot of {CONTRACT_ID}, trailing by {TRAIL_TICKS} ticks...")

        trailing_stop_order_request = OrderRequest(
            account_id=ACCOUNT_ID,
            contract_id=CONTRACT_ID,
            qty=1,
            side="sell",
            type="TrailingStop",
            trailing_distance=TRAIL_TICKS
            # stop_price might be needed as an initial trigger/activation for some trailing stop implementations,
            # but TopStepX API sample for TrailingStop only showed trailingDistance.
            # If API requires it, it would be: stop_price=SOME_INITIAL_ACTIVATION_PRICE (e.g. current market - X ticks)
        )

        initial_order_details: Optional[OrderDetails] = None
        try:
            initial_order_details = await api_client.place_order(trailing_stop_order_request)
            if initial_order_details and initial_order_details.id:
                placed_order_id = initial_order_details.id
                logger.info(f"SELL TrailingStop order submitted successfully! Order ID: {placed_order_id}")
                await print_order_details_example(initial_order_details, "Initial Placement")
            else:
                logger.error("Failed to submit TrailingStop order or no Order ID received. Exiting.")
                return
        except (OrderPlacementError, APIRequestError) as e:
            logger.error(f"Failed to place TrailingStop order: {e}")
            if hasattr(e, 'response_text') and e.response_text: logger.error(f"Raw: {e.response_text[:200]}")
            return

        logger.info("Waiting 10 seconds for order to be processed and potentially start trailing...")
        await asyncio.sleep(10)

        # --- Poll Order Status ---
        if not placed_order_id: return # Should not happen

        logger.info(f"\nPolling for status of order {placed_order_id}...")
        polled_details = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
        await print_order_details_example(polled_details, "Post-Placement Poll")

        if polled_details and polled_details.status not in ["Filled", "Cancelled", "Rejected", "Expired"]:
            logger.info(f"Order {placed_order_id} is active ({polled_details.status}). Consider cancelling it manually if not testing fills, or let example auto-cancel.")

            # Auto-cancel for this example after a further delay
            logger.info("Waiting another 20 seconds before auto-cancelling for cleanup...")
            await asyncio.sleep(20)

            # Re-poll before cancel to ensure it's still cancellable
            current_status_before_cancel = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
            if current_status_before_cancel and current_status_before_cancel.status in ["Working", "Accepted", "PendingNew"]: # Typical cancellable statuses
                logger.info(f"Attempting to cancel order {placed_order_id} (Status: {current_status_before_cancel.status})...")
                cancel_success = await api_client.cancel_order(order_id=placed_order_id, account_id=ACCOUNT_ID)
                if cancel_success:
                    logger.info(f"Order {placed_order_id} cancellation request successful.")
                else:
                    logger.warning(f"Order {placed_order_id} cancellation request failed (API returned false).")
                await asyncio.sleep(3) # Wait for cancellation to process
                final_details = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
                await print_order_details_example(final_details, "After Cancel Attempt")
            elif current_status_before_cancel:
                logger.info(f"Order {placed_order_id} status is {current_status_before_cancel.status}, not attempting auto-cancel.")
            else:
                logger.info(f"Could not get status for order {placed_order_id} before auto-cancel attempt.")


    except AuthenticationError as e:
        logger.error(f"AUTHENTICATION FAILED: {e}")
    except APIResponseParsingError as e_parse:
        logger.error(f"API RESPONSE PARSING ERROR: {e_parse}")
        if e_parse.raw_response_text:
            logger.error(f"Raw problematic response text (preview): {e_parse.raw_response_text[:500]}")
    except APIRequestError as e_req:
        logger.error(f"API REQUEST ERROR: {e_req}")
        if e_req.response_text:
            logger.error(f"Raw response: {e_req.response_text[:500]}")
    except TopstepAPIError as e_gen_api:
        logger.error(f"GENERAL API ERROR: {e_gen_api}")
    except Exception as e_gen:
        logger.error(f"AN UNEXPECTED ERROR OCCURRED: {e_gen}", exc_info=True)
    finally:
        if api_client:
            # Safety cancel if order was placed and might be active
            if placed_order_id:
                # Check final status if polled_details was ever set
                final_status_check = polled_details.status if polled_details else None
                if 'final_details' in locals() and locals()['final_details'] is not None: # if cancel was attempted
                    final_status_check = locals()['final_details'].status

                if final_status_check not in ["Filled", "Cancelled", "Rejected", "Expired"]:
                     logger.warning(f"Order {placed_order_id} might still be active (last known status: {final_status_check}). Attempting cleanup cancel in finally block.")
                     try:
                         # Re-poll one last time before cleanup
                         last_check = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
                         if last_check and last_check.status not in ["Filled", "Cancelled", "Rejected", "Expired"]:
                            await api_client.cancel_order(order_id=placed_order_id, account_id=ACCOUNT_ID)
                            logger.info(f"Cleanup cancel for order {placed_order_id} attempted.")
                         elif last_check:
                             logger.info(f"Order {placed_order_id} already in terminal state ({last_check.status}) during cleanup.")
                         else:
                             logger.info(f"Order {placed_order_id} not found during cleanup, assuming processed.")
                     except Exception as e_clean:
                         logger.error(f"Error during cleanup cancel: {e_clean}")

            logger.info("Closing APIClient session...")
            await api_client.close()
            logger.info("APIClient session closed.")

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path)
            logger.info(f".env file loaded from {dotenv_path}")
        else:
            logger.info(".env file not found at project root. Relying on environment variables.")
    except ImportError:
        logger.info("dotenv library not found. Relying on environment variables.")

    asyncio.run(main())
