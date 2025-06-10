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

# !!! IMPORTANT: ADJUST THESE PRICES TO BE REALISTIC FOR YOUR TESTING !!!
# For NQ futures (tick size 0.25), if current market is ~18000:
# A BUY limit order should be placed BELOW the current market price.
# A SELL limit order should be placed ABOVE the current market price.
# These prices are placeholders and will likely cause immediate fill or rejection
# if not set appropriately relative to the current market.
EXAMPLE_LIMIT_PRICE = 17000.00  # Adjust this to be well below current NQ market for a BUY LIMIT
EXAMPLE_MODIFIED_PRICE = 16950.00 # Adjust this to be even further below

# Order statuses that typically allow modification/cancellation
MODIFIABLE_STATUSES = ["Working", "PendingNew", "New", "Accepted", "PendingReplace", "Replaced"] # Add more as needed based on API

async def print_order_details(order_details: Optional[OrderDetails], context: str = ""):
    if order_details:
        logger.info(f"{context} Order Details for ID {order_details.id}:")
        logger.info(f"  Status: {order_details.status}")
        logger.info(f"  Contract ID: {order_details.contract_id}")
        logger.info(f"  Side: {order_details.side}, Type: {order_details.type}")
        logger.info(f"  Quantity: {order_details.quantity}, FilledQty: {order_details.filled_quantity}")
        # Check if limit_price attribute exists before trying to access it
        limit_price_info = 'N/A'
        if hasattr(order_details, 'limit_price') and order_details.limit_price is not None:
            limit_price_info = f"{order_details.limit_price:.2f}"
        elif hasattr(order_details, 'type') and order_details.type.lower() == 'market':
             limit_price_info = 'Market Order'
        logger.info(f"  Limit Price: {limit_price_info}")
        logger.info(f"  Avg Fill Price: {order_details.average_fill_price if order_details.average_fill_price is not None else 'N/A'}")
    else:
        logger.warning(f"{context} Order details not found or could not be retrieved.")

async def main():
    logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    logger.warning("!!! WARNING: THIS SCRIPT WILL PLACE, MODIFY, AND CANCEL LIVE ORDERS!       !!!")
    logger.warning("!!! ENSURE YOU ARE USING A DEMO ACCOUNT OR UNDERSTAND THE RISK!          !!!")
    logger.warning(f"!!! Account ID: {ACCOUNT_ID}, Contract ID: {CONTRACT_ID}                  !!!")
    logger.warning(f"!!! Using Limit Price: {EXAMPLE_LIMIT_PRICE}, Modified Price: {EXAMPLE_MODIFIED_PRICE} !!!")
    logger.warning("!!! THESE PRICES ARE PLACEHOLDERS. ADJUST THEM TO BE FAR OUT-OF-THE-MONEY  !!!")
    logger.warning("!!! FOR THE CURRENT MARKET PRICE OF THE CONTRACT TO AVOID IMMEDIATE FILL.  !!!")
    logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

    confirm = input("Type 'YES_MANAGE_ORDERS' to continue: ")
    if confirm != "YES_MANAGE_ORDERS":
        logger.info("Order management example cancelled by user.")
        return

    logger.info(f"--- Example 05: Place, Modify, Cancel Limit Order for Acct {ACCOUNT_ID} on {CONTRACT_ID} ---")
    api_client: Optional[APIClient] = None
    placed_order_id: Optional[int] = None
    final_details: Optional[OrderDetails] = None # Defined for finally block

    try:
        logger.info("Initializing APIClient...")
        api_client = APIClient()
        await api_client.authenticate()
        logger.info("APIClient authenticated.")

        # --- 1. Place a Limit BUY Order ---
        logger.info(f"Attempting to place LIMIT BUY order, 1 lot of {CONTRACT_ID} at {EXAMPLE_LIMIT_PRICE:.2f}...")
        limit_order_request = OrderRequest(
            account_id=ACCOUNT_ID,
            contract_id=CONTRACT_ID,
            qty=1,
            side="buy",
            type="limit",
            limit_price=EXAMPLE_LIMIT_PRICE
        )

        initial_order_details: Optional[OrderDetails] = None
        try:
            initial_order_details = await api_client.place_order(limit_order_request)
            if initial_order_details and initial_order_details.id:
                placed_order_id = initial_order_details.id
                logger.info(f"LIMIT BUY order submitted successfully! Order ID: {placed_order_id}")
                await print_order_details(initial_order_details, "Initial Placement")
            else:
                logger.error("Failed to submit LIMIT BUY order or no Order ID received. Exiting.")
                return
        except (OrderPlacementError, APIRequestError) as e:
            logger.error(f"Failed to place LIMIT BUY order: {e}")
            if hasattr(e, 'response_text') and e.response_text: logger.error(f"Raw: {e.response_text[:200]}")
            return

        logger.info("Waiting 5 seconds for order to be processed on the exchange...")
        await asyncio.sleep(5)

        # --- 2. Check Order Status (Polling) ---
        if not placed_order_id: return # Should not happen if above succeeded

        logger.info(f"\nPolling for status of order {placed_order_id}...")
        polled_details = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
        await print_order_details(polled_details, "Post-Placement Poll")

        if not polled_details or polled_details.status not in MODIFIABLE_STATUSES:
            logger.warning(f"Order {placed_order_id} is not in a modifiable state (Status: {polled_details.status if polled_details else 'Unknown'}). Skipping modify/cancel.")
            return

        # --- 3. Modify the Limit Order ---
        logger.info(f"\nAttempting to modify order {placed_order_id} to new limit price {EXAMPLE_MODIFIED_PRICE:.2f}...")
        modified_order_details: Optional[OrderDetails] = None
        try:
            modified_order_details = await api_client.modify_order(
                order_id=placed_order_id,
                account_id=ACCOUNT_ID,
                new_limit_price=EXAMPLE_MODIFIED_PRICE,
                new_quantity=1 # Explicitly state quantity, some APIs require it for modify
            )
            if modified_order_details:
                 logger.info(f"Order {placed_order_id} modify request submitted successfully.")
                 await print_order_details(modified_order_details, "Post-Modification Attempt")
            else: # Should be caught by exception usually
                logger.error(f"Order modification for {placed_order_id} did not return details or failed.")
        except (OrderPlacementError, APIRequestError) as e: # modify_order raises OrderPlacementError on failure
            logger.error(f"Failed to modify order {placed_order_id}: {e}")
            if hasattr(e, 'response_text') and e.response_text: logger.error(f"Raw: {e.response_text[:200]}")
            # Continue to cancellation attempt even if modification fails

        logger.info("Waiting 3 seconds for modification to process...")
        await asyncio.sleep(3)

        logger.info(f"\nPolling for status of (potentially modified) order {placed_order_id}...")
        polled_after_modify = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
        await print_order_details(polled_after_modify, "Post-Modification Poll")

        # Re-check status before cancel, in case it got filled after modification
        if polled_after_modify and polled_after_modify.status not in MODIFIABLE_STATUSES and polled_after_modify.status != "Cancelled":
            logger.warning(f"Order {placed_order_id} is now {polled_after_modify.status}. Skipping cancellation.")
            return
        elif not polled_after_modify: # If order not found after modify (e.g. it was replaced with new ID not handled here)
            logger.warning(f"Could not get status for order {placed_order_id} after modify. It might have been replaced with a new ID. Proceeding with original ID for cancel with caution.")


        # --- 4. Cancel the Order ---
        logger.info(f"\nAttempting to cancel order {placed_order_id}...")
        cancel_successful = False
        try:
            cancel_successful = await api_client.cancel_order(order_id=placed_order_id, account_id=ACCOUNT_ID)
            if cancel_successful:
                logger.info(f"Order {placed_order_id} cancel request reported as successful by API.")
            else:
                logger.warning(f"Order {placed_order_id} cancel request reported as failed by API. It might have already been filled/cancelled.")
        except APIRequestError as e:
             logger.error(f"API error during cancel request for order {placed_order_id}: {e}")

        logger.info("Waiting 3 seconds for cancellation to process...")
        await asyncio.sleep(3)

        logger.info(f"\nPolling for final status of order {placed_order_id} after cancel request...")
        final_details = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
        await print_order_details(final_details, "Final Poll")
        if final_details and final_details.status == "Cancelled":
            logger.info(f"Order {placed_order_id} confirmed CANCELLED.")
        elif final_details:
            logger.warning(f"Order {placed_order_id} final status is {final_details.status}, not Cancelled as expected.")
        else: # Order might not be found if successfully cancelled and removed from searchable orders
            logger.info(f"Could not retrieve final details for order {placed_order_id} (may have been fully processed and archived or already cancelled). Assuming cancellation was effective if API reported success.")

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
            # Safety net: if an order was placed and might still be active, try to cancel it
            # Check if final_details exists and indicates order is not in a terminal state
            order_still_potentially_active = False
            if placed_order_id:
                if 'final_details' in locals() and final_details is not None:
                    if final_details.status not in ["Filled", "Cancelled", "Rejected", "Expired"]:
                        order_still_potentially_active = True
                else:
                    # If final_details is None (e.g. order not found after cancel), it might be okay,
                    # but if it was never polled (e.g. error before final poll), then it's unknown.
                    # For simplicity, if we have a placed_order_id and final_details was not set or was None, try cleanup.
                    order_still_potentially_active = True


            if order_still_potentially_active:
                logger.warning(f"Order {placed_order_id} might still be active or its state is unknown. Attempting cleanup cancel in finally block.")
                try:
                    # Re-poll before cleanup cancel just in case
                    current_status_before_cleanup = await api_client.get_order_details(order_id=placed_order_id, account_id=ACCOUNT_ID)
                    if current_status_before_cleanup and current_status_before_cleanup.status in MODIFIABLE_STATUSES:
                        cleanup_cancelled = await api_client.cancel_order(order_id=placed_order_id, account_id=ACCOUNT_ID)
                        if cleanup_cancelled:
                            logger.info(f"Cleanup cancel for order {placed_order_id} successful.")
                        else:
                            logger.warning(f"Cleanup cancel for order {placed_order_id} failed. Final status was {current_status_before_cleanup.status}.")
                    elif current_status_before_cleanup:
                        logger.info(f"Order {placed_order_id} status before cleanup was {current_status_before_cleanup.status}, no cleanup cancel needed.")
                    else:
                        logger.info(f"Order {placed_order_id} not found before cleanup cancel, assuming it's already processed.")

                except Exception as e_cleanup:
                    logger.error(f"Error during cleanup cancel for order {placed_order_id}: {e_cleanup}")

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
