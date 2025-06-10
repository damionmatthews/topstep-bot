import asyncio
import logging
import os
from typing import List, Optional

# Ensure the main project directory is on the path if running from examples folder
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topstep_client import (
    APIClient,
    Contract,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    TopstepAPIError,
    ContractNotFoundError # Assuming we might want this, though search might just return empty
)

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s [%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("--- Example 02: Search Contracts ---")
    api_client: Optional[APIClient] = None
    try:
        # 1. Initialize APIClient
        logger.info("Initializing APIClient...")
        api_client = APIClient() # Credentials from environment variables

        # 2. Authenticate (implicitly handled by first request if token not present)
        # For clarity, we can call it explicitly.
        await api_client.authenticate()
        logger.info("Authentication successful (or token already valid).")

        # --- Search by text ---
        search_query = "NQ" # Example: Search for NQ futures
        logger.info(f"\nSearching for contracts with text: '{search_query}' (live=False)...")

        contracts_by_text: List[Contract] = await api_client.search_contracts(
            search_text=search_query,
            live=False
        )

        if contracts_by_text:
            logger.info(f"Found {len(contracts_by_text)} contract(s) for '{search_query}':")
            for contract in contracts_by_text[:5]: # Display first 5
                logger.info(
                    f"  ID: {contract.id}, Name: {contract.name}, "
                    f"Desc: {contract.description if contract.description else 'N/A'}, "
                    f"TickSize: {contract.tick_size}, TickValue: {contract.tick_value}"
                )
        else:
            logger.info(f"No contracts found for '{search_query}'.")

        # --- Search by a specific known ID ---
        # Use an ID from the previous search if available, otherwise a default.
        known_contract_id = "CON.F.US.NQ.M25" # Default example, might be expired
        if contracts_by_text and contracts_by_text[0].id:
            known_contract_id = contracts_by_text[0].id
            logger.info(f"Using contract ID from previous search for specific lookup: {known_contract_id}")
        else:
            logger.info(f"Using default contract ID for specific lookup: {known_contract_id}")

        logger.info(f"\nSearching for contract by ID (using search_text): '{known_contract_id}'...")
        # Our current APIClient.search_contracts uses search_text.
        # The TopStep API might interpret a full contract ID in searchText as an ID search.
        contracts_by_id: List[Contract] = await api_client.search_contracts(
            search_text=known_contract_id,
            live=False # Typically, specific ID lookups don't need 'live' if it's a general lookup
        )

        if contracts_by_id:
            logger.info(f"Found {len(contracts_by_id)} contract(s) for ID '{known_contract_id}':")
            for contract in contracts_by_id:
                logger.info(
                    f"  ID: {contract.id}, Name: {contract.name}, "
                    f"Desc: {contract.description if contract.description else 'N/A'}, "
                    f"TickSize: {contract.tick_size:.4f}, TickValue: {contract.tick_value:.2f}, "
                    f"Currency: {contract.currency}"
                )
        else:
            logger.info(f"No contract found for ID '{known_contract_id}'. This might be expected if the ID is invalid/expired or not matched by text search.")

    except AuthenticationError as e:
        logger.error(f"AUTHENTICATION FAILED: {e} (Status: {e.status_code}, Response: {e.response_text})")
    except APIResponseParsingError as e_parse:
        logger.error(f"API RESPONSE PARSING ERROR: {e_parse}")
        if e_parse.raw_response_text:
            logger.error(f"Raw problematic response text (preview): {e_parse.raw_response_text[:500]}")
    except APIRequestError as e_req:
        logger.error(f"API REQUEST ERROR: {e_req} (Status: {e_req.status_code}, Response: {e_req.response_text})")
    except ContractNotFoundError as e_contract_nf: # Example if we had specific error for ID not found
        logger.error(f"CONTRACT NOT FOUND: {e_contract_nf}")
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
    try:
        from dotenv import load_dotenv
        dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path)
            logger.info(f".env file loaded from {dotenv_path}")
        else:
            logger.info(".env file not found at project root, relying on environment variables being set externally.")
    except ImportError:
        logger.info("dotenv library not found, relying on environment variables being set externally.")

    asyncio.run(main())
