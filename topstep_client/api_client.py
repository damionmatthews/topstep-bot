import httpx
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Type, TypeVar, Any, Dict, Union, List
from pydantic import BaseModel, ValidationError # Ensure BaseModel is imported if used in type hints

from .schemas import TokenResponse, Account, Contract, OrderRequest, OrderDetails, APIResponse, ErrorDetail, BaseSchema, BarData, HistoricalBarsResponse
from .exceptions import AuthenticationError, APIRequestError, APIResponseParsingError, TopstepAPIError

logger = logging.getLogger(__name__)

T = TypeVar('T', bound='BaseSchema') # For Pydantic model typing

DEFAULT_API_BASE_URL = "https://api.topstepx.com"
TOKEN_EXPIRY_MARGIN_MINUTES = 5

class APIClient:
    def __init__(
        self,
        username: Optional[str] = None,
        api_key: Optional[str] = None,
        initial_token: Optional[str] = None,
        token_acquired_at: Optional[datetime] = None,
        base_url: str = DEFAULT_API_BASE_URL,
        httpx_client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = base_url
        self._username = username or os.getenv("TOPSTEP_USERNAME")
        self._api_key = api_key or os.getenv("TOPSTEP_API_KEY")

        self._session_token: Optional[str] = initial_token
        self._token_acquired_at: Optional[datetime] = token_acquired_at

        self._client = httpx_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

        if not self._session_token and (not self._username or not self._api_key):
            logger.warning("APIClient initialized without token or full credentials. Authentication will be required.")

    async def _get_headers(self, requires_auth: bool = True) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if requires_auth:
            if not self._session_token:
                logger.info("Session token is missing, attempting to authenticate.")
                await self.authenticate()
            if self._session_token:
                 headers["Authorization"] = f"Bearer {self._session_token}"
            else:
                raise AuthenticationError("Authentication required, but no token available after attempting to authenticate.")
        return headers

    async def authenticate(self) -> TokenResponse:
        if not self._username or not self._api_key:
            raise AuthenticationError("Username and API key are required for authentication.")

        auth_url = f"{self.base_url}/api/Auth/loginKey"
        payload = {"userName": self._username, "apiKey": self._api_key}

        logger.info(f"Attempting authentication to {auth_url} for user {self._username}...")
        try:
            response = await self._client.post(auth_url, json=payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
            response.raise_for_status()

            response_json = response.json()
            token_str = response_json.get("token")
            if not token_str:
                err_msg = response_json.get("errorMessage", "Token not found in response")
                raise AuthenticationError(f"Authentication failed: {err_msg}", response_text=response.text)

            token_data = {
                "token": token_str,
                "userId": response_json.get("userId", 0),
                "acquired_at": datetime.utcnow()
            }

            parsed_token_response = TokenResponse(**token_data)

            self._session_token = parsed_token_response.token
            self._token_acquired_at = parsed_token_response.acquired_at
            logger.info(f"Authentication successful for user {self._username}. Token acquired.")
            return parsed_token_response

        except httpx.HTTPStatusError as e:
            logger.error(f"Authentication HTTP error: {e.response.status_code} - {e.response.text}")
            raise AuthenticationError(f"HTTP {e.response.status_code}: {e.response.text}", status_code=e.response.status_code, response_text=e.response.text) from e
        except httpx.RequestError as e:
            logger.error(f"Authentication request error: {e}")
            raise APIRequestError(f"Request error during authentication: {e}") from e
        except ValidationError as e:
            logger.error(f"Pydantic validation error during authentication response parsing: {e}")
            raise APIResponseParsingError("Failed to parse authentication response.", raw_response_text=response.text if 'response' in locals() else None, original_exception=e) from e
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            raise TopstepAPIError(f"An unexpected error occurred during authentication: {str(e)}") from e

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Union[Dict[str, Any], BaseModel]] = None, # Use BaseModel from Pydantic
        params: Optional[Dict[str, Any]] = None,
        response_model: Optional[Type[T]] = None,
        requires_auth: bool = True
    ) -> Union[T, List[T], Dict[str, Any], str]: # Added List[T] for list responses

        headers = await self._get_headers(requires_auth=requires_auth)
        url = f"{self.base_url}{endpoint}"

        json_payload = None
        if payload:
            if isinstance(payload, BaseModel): # Check against Pydantic's BaseModel
                json_payload = payload.dict(by_alias=True, exclude_none=True)
            else:
                json_payload = payload

        logger.debug(f"Request: {method} {url} | Payload: {json_payload} | Params: {params}")

        try:
            response = await self._client.request(
                method, url, json=json_payload, params=params, headers=headers
            )
            response.raise_for_status()

            try:
                response_data = response.json()
            except Exception:
                response_data = response.text
                if response_model:
                    raise APIResponseParsingError(f"Expected JSON response but got text for {endpoint}.", raw_response_text=response.text)
                return response_data

            logger.debug(f"Response: {response.status_code} | Data: {response_data}")

            if response_model:
                try:
                    # Handle case where response_model is for a list of items e.g. List[Account]
                    if hasattr(response_model, '__origin__') and response_model.__origin__ == list:
                        item_type = response_model.__args__[0]
                        if isinstance(response_data, list):
                            return [item_type.parse_obj(item) for item in response_data]
                        else:
                            # If API wraps list in a dict, e.g. {"accounts": [...]}
                            # This part needs to be handled by the calling method (e.g. get_accounts)
                            # or by expecting a response_model that matches the wrapper dict.
                            raise APIResponseParsingError(
                                f"Expected a list for {response_model.__name__} but received {type(response_data)}.",
                                raw_response_text=str(response_data)
                            )
                    # Otherwise, parse as a single object
                    return response_model.parse_obj(response_data)
                except ValidationError as e:
                    logger.error(f"Pydantic validation error for {endpoint}: {e}. Raw response: {response_data}")
                    raise APIResponseParsingError(
                        f"Failed to parse response for {endpoint} into {response_model.__name__}.",
                        raw_response_text=str(response_data),
                        original_exception=e
                    ) from e
            return response_data

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {method} {url}: {e.response.status_code} - {e.response.text}")
            error_message = e.response.text
            try:
                error_json = e.response.json()
                if 'errorMessage' in error_json:
                    error_message = error_json['errorMessage']
            except Exception:
                pass
            raise APIRequestError(f"API request failed: {error_message}", status_code=e.response.status_code, response_text=e.response.text) from e
        except httpx.RequestError as e:
            logger.error(f"Request error for {method} {url}: {e}")
            raise APIRequestError(f"Request to {endpoint} failed: {e}") from e
        except APIResponseParsingError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error during request to {endpoint}: {e}")
            raise TopstepAPIError(f"An unexpected error occurred: {str(e)}") from e

    async def get_accounts(self, only_active: bool = True) -> List[Account]:
        payload = {"live": only_active}
        # Assuming the API returns a dict like {"accounts": [...]} or just a list [...]
        response_data = await self._request("POST", "/api/Account/search", payload=payload)

        accounts_list_raw = []
        if isinstance(response_data, list):
            accounts_list_raw = response_data
        elif isinstance(response_data, dict) and 'accounts' in response_data and isinstance(response_data['accounts'], list):
            accounts_list_raw = response_data['accounts']
        else:
            raise APIResponseParsingError(f"Unexpected response structure for get_accounts. Expected list or dict with 'accounts' key. Got: {type(response_data)} ({str(response_data)[:100]})", raw_response_text=str(response_data))

        try:
            return [Account.parse_obj(acc) for acc in accounts_list_raw]
        except ValidationError as e:
            raise APIResponseParsingError("Failed to parse account data.", raw_response_text=str(accounts_list_raw), original_exception=e) from e

    async def search_contracts(self, search_text: str, live: bool = False) -> List[Contract]:
        payload = {"live": live, "searchText": search_text}
        response_data = await self._request("POST", "/api/Contract/search", payload=payload)

        contracts_list_raw = []
        if isinstance(response_data, dict) and 'contracts' in response_data and isinstance(response_data['contracts'], list):
            contracts_list_raw = response_data['contracts']
        elif isinstance(response_data, list):
             contracts_list_raw = response_data
        else:
            raise APIResponseParsingError("Unexpected response structure for search_contracts. Expected list or dict with 'contracts' key.", raw_response_text=str(response_data))

        try:
            return [Contract.parse_obj(c) for c in contracts_list_raw]
        except ValidationError as e:
            raise APIResponseParsingError("Failed to parse contract data.", raw_response_text=str(contracts_list_raw), original_exception=e) from e

    async def place_order(self, order_request: OrderRequest) -> OrderDetails:
        response_data = await self._request("POST", "/api/Order/place", payload=order_request, response_model=OrderDetails)
        # The _request method with response_model=OrderDetails should handle parsing.
        # However, the actual API response for order placement needs to be confirmed.
        # If it returns something like {"success": true, "orderId": 123, ...},
        # then OrderDetails model needs to match that, or response_data needs pre-processing here.
        # The current OrderDetails model expects 'id', 'accountId', etc.
        # Let's assume _request and OrderDetails model are aligned for now or _request handles parsing to OrderDetails.
        return response_data # This should be an OrderDetails instance if parsing was successful

    async def get_historical_bars(
        self,
        contract_id: Union[str, int], # Can be string ID like CON.F.US.XXX or numeric instrumentId
        start_time_iso: str, # ISO 8601 format string e.g., "2023-01-01T00:00:00Z"
        end_time_iso: str,   # ISO 8601 format string
        bar_type: int, # 0=Second, 1=Minute, 2=Hour, 3=Day, ...
        bar_period: int, # e.g., 1 for 1-minute bars if type=1 (Minute)
        # limit: Optional[int] = None # /api/History/range doesn't seem to use limit, it uses the date range
    ) -> HistoricalBarsResponse:
        """Fetches historical bar data for a given contract and time range.

        Args:
            contract_id: The contract identifier (string like CON.F.US.XXX or numeric instrumentId).
                         The API documentation should clarify which one is expected by /api/History/range.
                         Let's assume it expects numeric instrumentId based on tsxapipy's preference.
            start_time_iso: Start of the time range in ISO 8601 format (UTC).
            end_time_iso: End of the time range in ISO 8601 format (UTC).
            bar_type: Type of bar (0=Second, 1=Minute, 2=Hour, 3=Day, 4=Week, 5=Month, 6=Year).
            bar_period: Period for the bar type (e.g., 1 for 1-minute bars when bar_type is Minute).
        """
        endpoint = "/api/History/range"
        # The API expects instrumentId (numeric).
        # If a string contract ID (e.g. "CON.F.US.NQ.M25") is passed,
        # we might need to resolve it to its numeric instrumentId first.
        # For now, assuming the caller provides the correct numeric ID if required by the API.
        # If the API can take the string ID directly for history, this is simpler.
        # Let's assume for now the API takes 'instrumentId' as the parameter name for the contract.

        # Attempt to convert contract_id to int if it's a string that looks like an int
        # otherwise, we might need a lookup if the API strictly requires numeric ID.
        # For now, we'll pass it as is and let the API decide, or rely on user providing numeric ID.
        # A better approach would be to have a helper in APIClient or contract_utils
        # to get the numeric ID if only string ID is known.

        payload = {
            "instrumentId": contract_id, # This is a guess, could be 'contractId' or other
            "type": bar_type,
            "period": bar_period,
            "startDateTime": start_time_iso,
            "endDateTime": end_time_iso
        }

        logger.info(f"Fetching historical bars for {contract_id} from {start_time_iso} to {end_time_iso}")
        # The API response for historical data is often a direct list of bars, not nested under 'data'.
        # Or it might be under a key like 'bars' or 'data.bars'.
        # We will try to parse it directly as HistoricalBarsResponse, which expects a 'bars' key.
        # If the API returns a raw list, HistoricalBarsResponse needs adjustment or pre-processing.

        # Assuming the API returns a JSON object like: {"bars": [...]} or directly [...] for the list of bars.
        # If it's just a list, our HistoricalBarsResponse schema needs to be just `bars: List[BarData]` and we'd parse List[BarData].
        # Let's assume the response is `{"bars": [...]}` to match the HistoricalBarsResponse schema.
        try:
            raw_response = await self._request(
                "POST",
                endpoint,
                payload=payload,
                response_model=None # Get raw dict/list first
            )

            if isinstance(raw_response, list):
                # If API returns a direct list of bars
                return HistoricalBarsResponse(bars=raw_response)
            elif isinstance(raw_response, dict) and "bars" in raw_response:
                # If API returns {"bars": [...]}
                return HistoricalBarsResponse.parse_obj(raw_response) # Use parse_obj for dicts
            elif isinstance(raw_response, dict) and "data" in raw_response and isinstance(raw_response["data"], dict) and "bars" in raw_response["data"]:
                 # If API returns {"data": {"bars": [...]}}
                 return HistoricalBarsResponse.parse_obj(raw_response["data"]) # Pass the inner dict to parse_obj
            else:
                logger.error(f"Unexpected response structure for historical bars: {raw_response}")
                raise APIResponseParsingError("Unexpected response structure for historical bars", raw_response_text=str(raw_response))

        except ValidationError as e:
            logger.error(f"Pydantic validation error parsing historical bars: {e}. Raw response: {raw_response if 'raw_response' in locals() else 'N/A'}")
            raise APIResponseParsingError("Failed to parse historical bars response.", raw_response_text=str(raw_response if 'raw_response' in locals() else 'N/A'), original_exception=e) from e

    async def close(self):
        await self._client.aclose()

async def get_authenticated_client(username: Optional[str] = None, api_key: Optional[str] = None) -> APIClient:
    client = APIClient(username=username, api_key=api_key)
    await client.authenticate()
    return client
