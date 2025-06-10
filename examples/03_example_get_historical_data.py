import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone # Added timezone
from typing import List, Optional, Any, Union # Added Union

# Ensure the main project directory is on the path if running from examples folder
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topstep_client import (
    APIClient,
    Contract,
    HistoricalBarsResponse,
    BarData,
    AuthenticationError,
    APIRequestError,
    APIResponseParsingError,
    TopstepAPIError,
    ContractNotFoundError
)

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s [%(levelname)s]: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Helper to get current contract (simplified for example)
async def get_current_front_month_contract(api_client: APIClient, symbol_root: str) -> Optional[Contract]:
    logger.info(f"Attempting to find current front-month contract for {symbol_root}...")
    try:
        # Search for non-live (includes expired) and live contracts to find a pattern
        # This is a heuristic and might need adjustment based on actual naming conventions
        contracts = await api_client.search_contracts(search_text=symbol_root, live=False)
        if not contracts:
            logger.warning(f"No contracts found for symbol root {symbol_root}.")
            return None

        # Filter for futures (e.g., name contains ' FUTR') and sort by a hypothetical expiry date
        # This part is highly dependent on how TopStep names contracts and if expiry is easily parsable
        # For a robust solution, specific parsing of contract names/descriptions for expiry is needed.
        # For this example, we'll just pick the first one that looks like a future and contains the symbol root.
        # A real implementation would need to parse month/year codes (e.g., M25 for June 2025).

        potential_contracts = []
        for c in contracts:
            if symbol_root in c.name and "FUTR" in c.name.upper(): # Basic filter
                potential_contracts.append(c)

        if not potential_contracts:
            logger.warning(f"No potential futures contracts found for {symbol_root} after filtering.")
            return None

        # Simplistic: return the first one found. A real version needs expiry sorting.
        # Or, if the API provides an 'instrumentId' that's numeric, that's often preferred for history.
        # And if 'live=True' in search_contracts gives the current one, that's easier.
        # Let's try searching for live contracts for the symbol root
        live_contracts = await api_client.search_contracts(search_text=symbol_root, live=True)
        if live_contracts:
            logger.info(f"Found live contract for {symbol_root}: {live_contracts[0].id}")
            return live_contracts[0] # Assume the first live one is the current front-month

        logger.warning(f"Could not definitively determine current live contract for {symbol_root} via search. Using first found non-live future.")
        return potential_contracts[0]

    except TopstepAPIError as e: # Changed from APIError to TopstepAPIError for consistency
        logger.error(f"API Error while trying to determine current contract for {symbol_root}: {e}")
        return None

async def main():
    logger.info("--- Example 03: Get Historical Data ---")
    api_client: Optional[APIClient] = None
    try:
        logger.info("Initializing APIClient...")
        api_client = APIClient()
        await api_client.authenticate()
        logger.info("APIClient authenticated.")

        # --- Determine Contract ID for History ---
        symbol_root_to_fetch = "NQ" # Example: E-mini NASDAQ
        # Default to an environment variable or a known recent contract if dynamic lookup fails
        default_contract_id_for_history = os.getenv("DEFAULT_HISTORY_CONTRACT_ID", "CON.F.US.NQ.M25") # Example
        numeric_instrument_id_for_history: Optional[Union[str, int]] = None

        # Attempt to get current contract details (simplified)
        current_contract = await get_current_front_month_contract(api_client, symbol_root_to_fetch)

        if current_contract:
            logger.info(f"Using dynamically found contract: ID={current_contract.id}, InstrumentID={current_contract.instrument_id}")
            # Prefer numeric instrument_id if available and API requires it
            if current_contract.instrument_id:
                numeric_instrument_id_for_history = current_contract.instrument_id
            else:
                numeric_instrument_id_for_history = current_contract.id # Fallback to string ID if numeric not found
        else:
            logger.warning(f"Could not dynamically determine contract for {symbol_root_to_fetch}. Falling back to default: {default_contract_id_for_history}")
            # If using default_contract_id_for_history, we might need to fetch its details to get numeric ID
            # For simplicity, if default is a string ID, we'll try using it directly or assume user sets a numeric one if needed.
            # To get numeric ID for a string ID:
            temp_contract_search = await api_client.search_contracts(search_text=default_contract_id_for_history, live=False)
            if temp_contract_search and temp_contract_search[0].instrument_id:
                numeric_instrument_id_for_history = temp_contract_search[0].instrument_id
                logger.info(f"Using numeric instrumentId {numeric_instrument_id_for_history} for default contract {default_contract_id_for_history}")
            else:
                 numeric_instrument_id_for_history = default_contract_id_for_history # Use string ID as last resort
                 logger.info(f"Using string ID {numeric_instrument_id_for_history} for default contract. Numeric ID not found.")

        if not numeric_instrument_id_for_history:
            logger.error("No contract ID available to fetch history. Exiting.")
            return

        # --- Fetch Historical Data ---
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=1) # Fetch 1 hour of 1-minute bars

        # TopStepX API bar type: 0=Second, 1=Minute, 2=Hour, 3=Day
        # TopStepX API period: for Minute type, this is the number of minutes (e.g., 1, 5, 15)
        bar_type_enum = 1 # Minute bars
        bar_period_val = 1  # 1-minute bars

        logger.info(f"Fetching {bar_period_val}-minute bars for contract/instrument ID '{numeric_instrument_id_for_history}'")
        logger.info(f"Time window: {start_time.isoformat()} to {end_time.isoformat()}")

        try:
            historical_response: HistoricalBarsResponse = await api_client.get_historical_bars(
                contract_id=numeric_instrument_id_for_history, # Use the determined ID
                start_time_iso=start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time_iso=end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                bar_type=bar_type_enum,
                bar_period=bar_period_val
            )

            if historical_response and historical_response.bars:
                logger.info(f"Successfully fetched {len(historical_response.bars)} bars.")
                for i, bar_data in enumerate(historical_response.bars[:5]): # Display first 5
                    logger.info(
                        f"  Bar {i + 1}: T={bar_data.t.isoformat()}, O={bar_data.o:.2f}, "
                        f"H={bar_data.h:.2f}, L={bar_data.l:.2f}, C={bar_data.c:.2f}, V={bar_data.v}"
                    )
                if len(historical_response.bars) > 5:
                    logger.info("  ...")
            else:
                logger.info("No bars returned for the period and contract.")

        except ContractNotFoundError as e_cnf:
            logger.error(f"History fetch failed: Contract (param: {numeric_instrument_id_for_history}) not found: {e_cnf}")
        except APIResponseParsingError as e_parse:
            logger.error(f"API RESPONSE PARSING ERROR for history ({numeric_instrument_id_for_history}): {e_parse}. Raw: {e_parse.raw_response_text}")
        except APIRequestError as e_req:
            logger.error(f"API REQUEST ERROR for history ({numeric_instrument_id_for_history}): {e_req}. Status: {e_req.status_code}, Response: {e_req.response_text}")
        except TopstepAPIError as e_api:
            logger.error(f"API Error during history fetch for ({numeric_instrument_id_for_history}): {e_api}")

    except AuthenticationError as e:
        logger.error(f"AUTHENTICATION FAILED: {e}")
    except APIRequestError as e_req: # Catch errors from initial contract search etc.
        logger.error(f"API REQUEST ERROR: {e_req}")
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
