import asyncio
import logging
import os
from typing import List, Optional

# Ensure the main project directory is on the path if running from examples folder
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topstep_client import (
    APIClient,
    Account,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    TopstepAPIError
)

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s [%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("--- Example 01: Authenticate and Get Accounts ---")
    api_client: Optional[APIClient] = None
    try:
        # 1. Initialize APIClient
        # Credentials (TOPSTEP_USERNAME, TOPSTEP_API_KEY) are read from environment variables by default
        logger.info("Initializing APIClient...")
        api_client = APIClient() # Username and API key loaded from env vars

        # 2. Authenticate (APIClient's _get_headers will call authenticate if needed)
        # For this example, we can explicitly call it or let the first API call trigger it.
        # To be explicit and ensure token is fetched:
        await api_client.authenticate()
        logger.info("Authentication successful (or token already valid).")

        # 3. Get Account Details
        logger.info("Fetching all accounts (including inactive)...")
        all_accounts: List[Account] = await api_client.get_accounts(only_active=False)

        if all_accounts:
            logger.info(f"Found {len(all_accounts)} total accounts:")
            for acc in all_accounts:
                logger.info(
                    f"  ID: {acc.id}, Name: {acc.name}, Balance: {acc.balance:.2f}, "
                    f"Type: {acc.account_type}, Active: {acc.active}"
                )
        else:
            logger.info("No accounts found (when fetching all).")

        logger.info("\nFetching only active accounts...")
        active_accounts: List[Account] = await api_client.get_accounts(only_active=True)
        if active_accounts:
            logger.info(f"Found {len(active_accounts)} active accounts:")
            for acc in active_accounts:
                logger.info(
                    f"  ID: {acc.id}, Name: {acc.name}, Balance: {acc.balance:.2f}, "
                    f"Type: {acc.account_type}, Active: {acc.active}"
                )
        else:
            logger.info("No active accounts found.")

    except AuthenticationError as e:
        logger.error(f"AUTHENTICATION FAILED: {e} (Status: {e.status_code}, Response: {e.response_text})")
        logger.error("Please ensure TOPSTEP_USERNAME and TOPSTEP_API_KEY are correctly set in your environment.")
    except APIResponseParsingError as e_parse:
        logger.error(f"API RESPONSE PARSING ERROR: {e_parse}")
        if e_parse.raw_response_text:
            logger.error(f"Raw problematic response text (preview): {e_parse.raw_response_text[:500]}")
    except APIRequestError as e_req:
        logger.error(f"API REQUEST ERROR: {e_req} (Status: {e_req.status_code}, Response: {e_req.response_text})")
    except TopstepAPIError as e_gen_api:
        logger.error(f"GENERAL API ERROR: {e_gen_api}")
    except Exception as e_gen: # pylint: disable=broad-exception-caught
        logger.error(f"AN UNEXPECTED ERROR OCCURRED: {e_gen}", exc_info=True)
    finally:
        if api_client:
            logger.info("Closing APIClient session...")
            await api_client.close()
            logger.info("APIClient session closed.")

if __name__ == "__main__":
    # Create a .env file in the parent directory (project root) with:
    # TOPSTEP_USERNAME="your_username"
    # TOPSTEP_API_KEY="your_api_key"
    # For the example to run, you might need to load dotenv if not running from a context where it's already loaded
    try:
        from dotenv import load_dotenv
        # Load .env from the parent directory if it exists
        dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path)
            logger.info(f".env file loaded from {dotenv_path}")
        else:
            logger.info(".env file not found at project root, relying on environment variables being set externally.")
    except ImportError:
        logger.info("dotenv library not found, relying on environment variables being set externally.")

    asyncio.run(main())
