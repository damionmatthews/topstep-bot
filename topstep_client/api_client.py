import httpx
import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Type, TypeVar, Any, Dict, Union, List
from pydantic import BaseModel, ValidationError # Ensure BaseModel is imported

# Updated Schema Imports to use new names primarily
from .schemas import (
    TokenResponse, LoginErrorCode,
    TradingAccountModel, SearchAccountResponse,
    ContractModel, SearchContractResponse,
    PlaceOrderRequest, PlaceOrderResponse,
    OrderModel, SearchOrderResponse,
    ModifyOrderRequest, ModifyOrderResponse,
    CancelOrderRequest, CancelOrderResponse,
    PositionModel, SearchPositionResponse,
    AggregateBarModel, RetrieveBarRequest, RetrieveBarResponse,
    APIResponse, ErrorDetail, BaseSchema, OpenOrderSchema,
    # Enums that might be used directly in client logic
    OrderSide, OrderType, OrderStatus, PositionType, AggregateBarUnit, PlaceOrderErrorCode
)
from .exceptions import AuthenticationError, APIRequestError, APIResponseParsingError, TopstepAPIError

logger = logging.getLogger(__name__)

T = TypeVar('T', bound='BaseSchema')

DEFAULT_API_BASE_URL = "https://api.topstepx.com"
TOKEN_EXPIRY_MARGIN_MINUTES = 5

class APIClient:
    def __init__(
        self,
        username: Optional[str] = None,
        api_key: Optional[str] = None,
        initial_token: Optional[str] = None,
        base_url: str = DEFAULT_API_BASE_URL,
        httpx_client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = base_url
        self._username = username or os.getenv("TOPSTEP_USERNAME")
        self._api_key = api_key or os.getenv("TOPSTEP_API_KEY")
        self._session_token_details: Optional[TokenResponse] = None

        if initial_token:
            self._session_token_details = TokenResponse(
                success=True, token=initial_token, acquired_at=datetime.utcnow() # acquired_at is auto-set
            )

        self._client = httpx_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

        if not self._session_token_details and (not self._username or not self._api_key):
            logger.warning("APIClient initialized without token or full credentials. Authentication will be required.")

    @property
    def _session_token(self) -> Optional[str]:
        if self._session_token_details and self._session_token_details.token:
            # TODO: Add token expiry check here if acquired_at and token lifetime are known
            # For now, assuming token is valid if it exists.
            # A more robust check would compare self._session_token_details.acquired_at 
            # with current time and a known token lifetime.
            # Example:
            # if datetime.utcnow() < self._session_token_details.acquired_at + timedelta(hours=1): # Assuming 1hr lifetime
            #    return self._session_token_details.token
            # else:
            #    logger.info("Token may have expired based on acquisition time.")
            #    return None 
            return self._session_token_details.token
        return None

    async def _get_headers(self, requires_auth: bool = True) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if requires_auth:
            if not self._session_token:
                logger.info("Session token is missing or potentially expired, attempting to authenticate.")
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
            # Ensure acquired_at is set if not present in response (it should be by default_factory now)
            parsed_token_response = TokenResponse.parse_obj(response_json) 
            
            if not parsed_token_response.success or not parsed_token_response.token:
                error_msg = parsed_token_response.error_message or "Unknown authentication error"
                # Access .value if error_code is an enum, otherwise it's None
                error_code_val = parsed_token_response.error_code.value if parsed_token_response.error_code else "N/A"
                logger.error(f"Authentication failed via API: {error_msg} (Code: {error_code_val})")
                self._session_token_details = parsed_token_response 
                raise AuthenticationError(f"Authentication failed: {error_msg} (Code: {error_code_val})", response_text=response.text)

            self._session_token_details = parsed_token_response
            # acquired_at is now set by default_factory in TokenResponse if not in API response
            logger.info(f"Authentication successful for user {self._username}. Token acquired at {self._session_token_details.acquired_at}.")
            return self._session_token_details

        except httpx.HTTPStatusError as e:
            logger.error(f"Authentication HTTP error: {e.response.status_code} - {e.response.text}")
            try:
                err_data = e.response.json()
                msg = err_data.get("errorMessage", e.response.text)
            except Exception:
                msg = e.response.text
            raise AuthenticationError(f"HTTP {e.response.status_code}: {msg}", status_code=e.response.status_code, response_text=e.response.text) from e
        except httpx.RequestError as e:
            logger.error(f"Authentication request error: {e}")
            raise APIRequestError(f"Request error during authentication: {e}") from e
        except ValidationError as e:
            logger.error(f"Pydantic validation error during authentication response parsing: {e}")
            raw_text = response.text if 'response' in locals() else None
            raise APIResponseParsingError("Failed to parse authentication response.", raw_response_text=raw_text, original_exception=e) from e
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
            raise TopstepAPIError(f"An unexpected error occurred during authentication: {str(e)}") from e

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Union[Dict[str, Any], BaseModel]] = None,
        params: Optional[Dict[str, Any]] = None,
        response_model: Optional[Type[T]] = None,
        requires_auth: bool = True
    ) -> Union[T, List[T], Dict[str, Any], str]:
        headers = await self._get_headers(requires_auth=requires_auth)
        url = f"{self.base_url}{endpoint}"
        json_payload = None
        if payload:
            if isinstance(payload, BaseModel):
                json_payload = payload.dict(by_alias=True, exclude_none=True) # Pydantic v1
            else:
                json_payload = payload
        
        logger.debug(f"Request: {method} {url} | Headers: {headers} | Payload: {json_payload} | Params: {params}")
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
                return response_data # Return plain text if no model expected
            
            logger.debug(f"Response: {response.status_code} | Data: {response_data}")

            if response_model:
                try:
                    # Handling List[ModelType]
                    if hasattr(response_model, '__origin__') and response_model.__origin__ == list:
                        item_type = response_model.__args__[0]
                        if not issubclass(item_type, BaseModel):
                             raise APIResponseParsingError(f"Item type {item_type} in List is not a Pydantic model for {endpoint}.")
                        if isinstance(response_data, list):
                            return [item_type.parse_obj(item) for item in response_data]
                        else: # If API returns a single object but List[Model] was expected (should not happen with good Swagger)
                            # This case might indicate an API inconsistency or wrong response_model usage.
                            # Depending on strictness, could raise error or try to parse as single item if item_type matches.
                            # For now, strict:
                            raise APIResponseParsingError(
                                f"Expected a list for {response_model} but received {type(response_data)} for {endpoint}.",
                                raw_response_text=str(response_data)
                            )
                    
                    # Handling ModelType
                    if not issubclass(response_model, BaseModel):
                        raise APIResponseParsingError(f"Response model {response_model} is not a Pydantic model for {endpoint}.")
                    return response_model.parse_obj(response_data)
                except ValidationError as e:
                    logger.error(f"Pydantic validation error for {endpoint}: {e}. Raw response: {response_data}")
                    raise APIResponseParsingError(
                        f"Failed to parse response for {endpoint} into {response_model.__name__}.",
                        raw_response_text=str(response_data),
                        original_exception=e
                    ) from e
            return response_data # Return dict/list if no model, or parsed text from above
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {method} {url}: {e.response.status_code} - {e.response.text}")
            error_message = e.response.text
            status_code = e.response.status_code
            try:
                error_json = e.response.json()
                if isinstance(error_json, dict) and 'errorMessage' in error_json:
                     error_message = error_json['errorMessage']
                # Attempt to parse with ErrorDetail if it's a known error structure
                # else: ErrorDetail.parse_obj(error_json) ? - careful not to double-raise or mask
            except Exception: 
                pass # Keep original text if JSON parsing here fails
            raise APIRequestError(f"API request failed: {error_message}", status_code=status_code, response_text=e.response.text) from e
        except httpx.RequestError as e:
            logger.error(f"Request error for {method} {url}: {e}")
            raise APIRequestError(f"Request to {endpoint} failed: {e}") from e
        except APIResponseParsingError: # Already logged, just re-raise
            raise
        except Exception as e: # Catch-all for other unexpected errors
            logger.error(f"Unexpected error during request to {endpoint}: {e}", exc_info=True)
            raise TopstepAPIError(f"An unexpected error occurred while processing request to {endpoint}: {str(e)}") from e

    async def close(self):
        await self._client.aclose()

async def get_authenticated_client(username: Optional[str] = None, api_key: Optional[str] = None) -> APIClient:
    client = APIClient(username=username, api_key=api_key)
    await client.authenticate()
    return client

    # Updated method signatures (implementation details to follow in next steps)
    # These methods use the new schema names in their type hints.
    # Their internal logic (payloads, specific endpoint details, full response parsing)
    # will be refined in subsequent, more focused subtasks.

    async def get_accounts(self, only_active: bool = True) -> List[TradingAccountModel]:
        # Placeholder - actual implementation to be refined.
        payload = {"onlyActiveAccounts": only_active}
        # Assuming the actual API returns a wrapper object like SearchAccountResponse
        response_wrapper = await self._request("POST", "/api/Account/search", payload=payload, response_model=SearchAccountResponse)
        if response_wrapper.success and response_wrapper.accounts is not None:
            return response_wrapper.accounts
        # Handle error case based on actual API contract for SearchAccountResponse
        elif not response_wrapper.success:
             raise APIRequestError(f"Failed to get accounts: {response_wrapper.error_message} (Code: {response_wrapper.error_code})", response_text=str(response_wrapper)) # Add .value for enum
        return []


    async def search_contracts(self, search_text: str, live: bool = False) -> List[ContractModel]:
        # Placeholder - actual implementation to be refined.
        payload = {"live": live, "searchText": search_text}
        response_wrapper = await self._request("POST", "/api/Contract/search", payload=payload, response_model=SearchContractResponse)
        if response_wrapper.success and response_wrapper.contracts is not None:
            return response_wrapper.contracts
        elif not response_wrapper.success:
            raise APIRequestError(f"Failed to search contracts: {response_wrapper.error_message} (Code: {response_wrapper.error_code})", response_text=str(response_wrapper))
        return []

    async def place_order(self, order_request: PlaceOrderRequest) -> PlaceOrderResponse:
        # Placeholder - actual implementation to be refined.
        # Note: PlaceOrderRequest is already the correct payload schema.
        return await self._request("POST", "/api/Order/place", payload=order_request, response_model=PlaceOrderResponse)

    async def get_order_details(self, order_id: int, account_id: int) -> Optional[OrderModel]:
        # Placeholder - requires knowing the actual endpoint and payload for fetching a single order.
        # This might involve a SearchOrder-like request with specific filters.
        # For now, returning None as a placeholder.
        logger.warning(f"get_order_details for {order_id} needs full review against Swagger for endpoint/payload.")
        # Example: Search for the order
        # search_payload = {"accountId": account_id, "orderIdFilter": order_id} # Fictional payload
        # response = await self._request("POST", "/api/Order/search", payload=search_payload, response_model=SearchOrderResponse)
        # if response.success and response.orders and len(response.orders) == 1:
        #     return response.orders[0]
        return None


    async def modify_order(
        self,
        order_id: int,
        account_id: int,
        new_quantity: Optional[int] = None,
        new_limit_price: Optional[float] = None,
        new_stop_price: Optional[float] = None,
        new_trail_price: Optional[float] = None # Added from schema
    ) -> ModifyOrderResponse:
        # Placeholder - actual implementation to be refined.
        endpoint = "/api/Order/modify"
        payload = ModifyOrderRequest(
            accountId=account_id, 
            orderId=order_id, 
            size=new_quantity, # Ensure names match ModifyOrderRequest
            limitPrice=new_limit_price, 
            stopPrice=new_stop_price,
            trailPrice=new_trail_price
        )
        return await self._request("POST", endpoint, payload=payload, response_model=ModifyOrderResponse)

    async def cancel_order(self, order_id: int, account_id: int) -> CancelOrderResponse:
        # Placeholder - actual implementation to be refined.
        endpoint = "/api/Order/cancel"
        payload = CancelOrderRequest(accountId=account_id, orderId=order_id)
        return await self._request("POST", endpoint, payload=payload, response_model=CancelOrderResponse)

    async def get_historical_bars(
        self, contract_id: str, start_time: datetime, end_time: datetime,
        unit: AggregateBarUnit, unit_number: int, live: bool = False, # Matched RetrieveBarRequest
        limit: Optional[int] = None, include_partial_bar: bool = False # Matched RetrieveBarRequest
    ) -> RetrieveBarResponse: # Changed from List[AggregateBarModel] to RetrieveBarResponse
        # Placeholder - actual implementation to be refined.
        endpoint = "/api/History/retrieveBars"
        payload = RetrieveBarRequest(
            contractId=contract_id, live=live, startTime=start_time, endTime=end_time,
            unit=unit, unitNumber=unit_number, limit=limit, includePartialBar=include_partial_bar
        )
        # The _request method will handle parsing into RetrieveBarResponse.
        # If successful, the bars themselves would be on response.bars
        return await self._request("POST", endpoint, payload=payload, response_model=RetrieveBarResponse)


    async def get_open_orders(self, account_id: int) -> List[OrderModel]: # Return type is List[OrderModel]
        # Placeholder - actual implementation to be refined.
        endpoint = "/api/Order/searchOpen" # Assuming this is the correct endpoint for open orders
        payload = {"accountId": account_id} # Assuming simple payload
        # This should parse into SearchOrderResponse, then extract orders
        response_wrapper = await self._request("POST", endpoint, payload=payload, response_model=SearchOrderResponse)
        if response_wrapper.success and response_wrapper.orders is not None:
            return response_wrapper.orders
        elif not response_wrapper.success:
             raise APIRequestError(f"Failed to get open orders: {response_wrapper.error_message} (Code: {response_wrapper.error_code})", response_text=str(response_wrapper)) # Add .value for enum
        return []


    async def get_positions(self, account_id: int) -> List[PositionModel]: # Return type is List[PositionModel]
        # Placeholder - actual implementation to be refined.
        endpoint = "/api/Position/searchOpen" # Assuming this is for open positions
        payload = {"accountId": account_id} # Assuming simple payload
        response_wrapper = await self._request("POST", endpoint, payload=payload, response_model=SearchPositionResponse)
        if response_wrapper.success and response_wrapper.positions is not None:
            return response_wrapper.positions
        elif not response_wrapper.success:
            raise APIRequestError(f"Failed to get positions: {response_wrapper.error_message} (Code: {response_wrapper.error_code})", response_text=str(response_wrapper)) # Add .value for enum
        return []
