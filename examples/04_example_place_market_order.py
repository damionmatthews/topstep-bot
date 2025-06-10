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
# For testing, use accountId 8027309 and contractId CON.F.US.ENQ.M25
ACCOUNT_ID = 8027309
CONTRACT_ID = "CON.F.US.ENQ.M25" # E-mini NASDAQ

async def main():
    logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    logger.warning("!!! WARNING: THIS SCRIPT WILL PLACE A LIVE MARKET ORDER!   !!!")
    logger.warning("!!! ENSURE YOU ARE USING A DEMO ACCOUNT OR UNDERSTAND RISK!  !!!")
    logger.warning(f"!!! Account ID: {ACCOUNT_ID}, Contract ID: {CONTRACT_ID} !!!")
    logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

    confirm = input("Type 'YES_PLACE_ORDER' to continue: ")
    if confirm != "YES_PLACE_ORDER":
        logger.info("Order placement cancelled by user.")
        return

    logger.info(f"--- Example 04: Place Market Order for Account {ACCOUNT_ID} on {CONTRACT_ID} ---")
    api_client: Optional[APIClient] = None

    try:
        # 1. Initialize APIClient
        logger.info("Initializing APIClient...")
        api_client = APIClient() # Credentials from environment variables

        # 2. Authenticate
        await api_client.authenticate()
        logger.info("Authentication successful.")

        # 3. Place a Market BUY order
        logger.info(f"Attempting to place MARKET BUY order, 1 lot of {CONTRACT_ID} for account {ACCOUNT_ID}...")

        order_request = OrderRequest(
            account_id=ACCOUNT_ID,
            contract_id=CONTRACT_ID,
            qty=1,
            side="buy", # "buy" or "sell"
            type="market" # "market", "limit", "stop"
            # For limit/stop orders, you'd also set limit_price or stop_price
        )

        placed_order_response: Optional[OrderDetails] = None
        try:
            # Our APIClient.place_order is designed to return OrderDetails directly if successful
            # or raise an exception (e.g. OrderPlacementError, APIRequestError)
            placed_order_response = await api_client.place_order(order_request)

            if placed_order_response and placed_order_response.id:
                logger.info(f"MARKET BUY order submitted successfully! Order ID: {placed_order_response.id}")
                logger.info(f"Initial Order Status from placement: {placed_order_response.status}")

                # 4. Optionally poll for order status
                logger.info("Waiting for 5 seconds before polling for order status...")
                await asyncio.sleep(5)

                logger.info(f"Polling for status of order {placed_order_response.id}...")
                polled_order_details: Optional[OrderDetails] = await api_client.get_order_details(
                    order_id=placed_order_response.id,
                    account_id=ACCOUNT_ID
                )

                if polled_order_details:
                    logger.info(f"Polled Order Details for {polled_order_details.id}:")
                    logger.info(f"  Status: {polled_order_details.status}")
                    logger.info(f"  Contract ID: {polled_order_details.contract_id}")
                    logger.info(f"  Quantity: {polled_order_details.quantity}, FilledQty: {polled_order_details.filled_quantity}")
                    logger.info(f"  Avg Fill Price: {polled_order_details.average_fill_price if polled_order_details.average_fill_price is not None else 'N/A'}")
                    # logger.info(f"  Full Model JSON: {polled_order_details.model_dump_json(indent=2, by_alias=True)}")
                else:
                    logger.warning(f"Could not retrieve details for order {placed_order_response.id} via polling shortly after placement.")
            else:
                # This case should ideally be covered by exceptions from place_order
                logger.error(f"Market BUY order submission did not return a valid Order ID. Response: {placed_order_response}")

        except OrderPlacementError as ope: # Specific error for order placement issues
            logger.error(f"ORDER PLACEMENT FAILED: {ope}")
            if ope.response_text: logger.error(f"Raw response: {ope.response_text[:500]}")
        except APIRequestError as e_req: # Catch other request errors during order placement/polling
            logger.error(f"API REQUEST ERROR during order operation: {e_req}")
            if e_req.response_text: logger.error(f"Raw response: {e_req.response_text[:500]}")
        except APIResponseParsingError as e_parse:
            logger.error(f"API RESPONSE PARSING ERROR during order operation: {e_parse}")
            if e_parse.raw_response_text: logger.error(f"Raw response: {e_parse.raw_response_text[:500]}")


    except AuthenticationError as e:
        logger.error(f"AUTHENTICATION FAILED: {e}")
        logger.error("Please ensure TOPSTEP_USERNAME and TOPSTEP_API_KEY are correctly set in your environment.")
    except TopstepAPIError as e_gen_api:
        logger.error(f"GENERAL API ERROR: {e_gen_api}")
    except Exception as e_gen:
        logger.error(f"AN UNEXPECTED ERROR OCCURRED: {e_gen}", exc_info=True)
    finally:
        if api_client:
            logger.info("Closing APIClient session...")
            await api_client.close()
            logger.info("APIClient session closed.")

if __name__ == "__main__":
    # Load .env file from project root
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
