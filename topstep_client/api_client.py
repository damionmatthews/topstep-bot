import httpx
import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone # Added timezone
from typing import Optional, Type, TypeVar, Any, Dict, Union, List
from pydantic import BaseModel, ValidationError

from .schemas import (
    TokenResponse, LoginErrorCode,
    TradingAccountModel, SearchAccountResponse,
    ContractModel, SearchContractResponse,
    PlaceOrderRequest, PlaceOrderResponse,
    OrderModel, SearchOrderRequest, SearchOrderResponse, # Added SearchOrderRequest
    ModifyOrderRequest, ModifyOrderResponse,
    CancelOrderRequest, CancelOrderResponse,
    PositionModel, SearchPositionResponse,
    AggregateBarModel, RetrieveBarRequest, RetrieveBarResponse,
    APIResponse, ErrorDetail, BaseSchema, OpenOrderSchema,
    OrderSide, OrderType, OrderStatus, PositionType, AggregateBarUnit,
    PlaceOrderErrorCode, SearchAccountErrorCode
)
from .exceptions import AuthenticationError, APIRequestError, APIResponseParsingError, TopstepAPIError

logger = logging.getLogger(__name__)

T = TypeVar('T', bound='BaseSchema')

DEFAULT_API_BASE_URL = "https://api.topstepx.com"
DEFAULT_ORDER_SEARCH_TIMESPAN_HOURS = 72 # For get_order_details fallback

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
                success=True, token=initial_token, acquired_at=datetime.now(timezone.utc)
            )
        self._client = httpx_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        if not self._session_token_details and (not self._username or not self._api_key):
            logger.warning("APIClient initialized without token or full credentials. Authentication will be required.")

    @property
    def _session_token(self) -> Optional[str]:
        if self._session_token_details and self._session_token_details.token:
            return self._session_token_details.token
        return None

    async def _get_headers(self, requires_auth: bool = True) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if requires_auth:
            if not self._session_token:
                await self.authenticate()
            if self._session_token:
                 headers["Authorization"] = f"Bearer {self._session_token}"
            else:
                raise AuthenticationError("Authentication required, but no token available.")
        return headers

    async def authenticate(self) -> TokenResponse:
        if not self._username or not self._api_key:
            raise AuthenticationError("Username and API key are required.")
        auth_url = f"{self.base_url}/api/Auth/loginKey"
        payload = {"userName": self._username, "apiKey": self._api_key}
        logger.info(f"Authenticating to {auth_url} for user {self._username}...")
        try:
            response = await self._client.post(auth_url, json=payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
            response.raise_for_status()
            response_json = response.json()
            parsed_token_response = TokenResponse.parse_obj(response_json)
            if not parsed_token_response.success or not parsed_token_response.token:
                error_msg = parsed_token_response.error_message or "Unknown auth error"
                error_code_val = parsed_token_response.error_code.value if parsed_token_response.error_code else "N/A"
                logger.error(f"API Auth failed: {error_msg} (Code: {error_code_val})")
                self._session_token_details = parsed_token_response
                raise AuthenticationError(f"API Auth failed: {error_msg} (Code: {error_code_val})", response_text=response.text)
            self._session_token_details = parsed_token_response
            logger.info(f"Auth successful for {self._username}.")
            return self._session_token_details
        except httpx.HTTPStatusError as e:
            logger.error(f"Auth HTTP error: {e.response.status_code} - {e.response.text}")
            try: msg = e.response.json().get("errorMessage", e.response.text)
            except Exception: msg = e.response.text
            raise AuthenticationError(f"HTTP {e.response.status_code}: {msg}", status_code=e.response.status_code, response_text=e.response.text) from e
        except httpx.RequestError as e:
            logger.error(f"Auth request error: {e}")
            raise APIRequestError(f"Auth request error: {e}") from e
        except ValidationError as e:
            logger.error(f"Pydantic validation error in auth response: {e}")
            raise APIResponseParsingError("Failed to parse auth response.", raw_response_text=response.text if 'response' in locals() else None, original_exception=e) from e
        except Exception as e:
            logger.error(f"Unexpected auth error: {e}", exc_info=True)
            raise TopstepAPIError(f"Unexpected auth error: {str(e)}") from e

    async def _request(
        self, method: str, endpoint: str,
        payload: Optional[Union[Dict[str, Any], BaseModel]] = None,
        params: Optional[Dict[str, Any]] = None,
        response_model: Optional[Type[T]] = None,
        requires_auth: bool = True
    ) -> Union[T, List[T], Dict[str, Any], str]:
        headers = await self._get_headers(requires_auth=requires_auth)
        url = f"{self.base_url}{endpoint}"
        json_payload = payload.dict(by_alias=True, exclude_none=True) if isinstance(payload, BaseModel) else payload
        logger.debug(f"Request: {method} {url} | Payload: {json_payload} | Params: {params}")
        try:
            response = await self._client.request(method, url, json=json_payload, params=params, headers=headers)
            response.raise_for_status()
            try: response_data = response.json()
            except Exception:
                response_data = response.text
                if response_model: raise APIResponseParsingError(f"Expected JSON for {endpoint}, got text.", raw_response_text=response.text)
                return response_data
            logger.debug(f"Response: {response.status_code} | Data: {response_data}")
            if response_model:
                try:
                    if hasattr(response_model, '__origin__') and response_model.__origin__ == list:
                        item_type = response_model.__args__[0]
                        if isinstance(response_data, list) and issubclass(item_type, BaseModel):
                            return [item_type.parse_obj(item) for item in response_data]
                        raise APIResponseParsingError(f"Expected list of {item_type} for {endpoint}, received {type(response_data)}.")
                    if issubclass(response_model, BaseModel):
                        return response_model.parse_obj(response_data)
                    raise APIResponseParsingError(f"Response model {response_model} is not Pydantic for {endpoint}.")
                except ValidationError as e:
                    raise APIResponseParsingError(f"Failed to parse {endpoint} into {response_model.__name__}.", raw_response_text=str(response_data), original_exception=e) from e
            return response_data
        except httpx.HTTPStatusError as e:
            error_message = e.response.text
            try: error_message = e.response.json().get('errorMessage', error_message)
            except Exception: pass
            raise APIRequestError(f"API request failed: {error_message}", status_code=e.response.status_code, response_text=e.response.text) from e
        except httpx.RequestError as e: raise APIRequestError(f"Request to {endpoint} failed: {e}") from e
        except APIResponseParsingError: raise
        except Exception as e: raise TopstepAPIError(f"Unexpected error for {endpoint}: {str(e)}") from e

    async def close(self): await self._client.aclose()

    async def get_accounts(self, only_active: bool = True) -> List[TradingAccountModel]:
        payload = {"onlyActiveAccounts": only_active}
        response_wrapper: SearchAccountResponse = await self._request("POST", "/api/Account/search", payload=payload, response_model=SearchAccountResponse)
        if response_wrapper.success and response_wrapper.accounts is not None: return response_wrapper.accounts
        error_code_val = response_wrapper.error_code.value if response_wrapper.error_code else "N/A"
        raise APIRequestError(response_wrapper.error_message or f"Failed to get accounts (Code: {error_code_val})", response_text=str(response_wrapper.dict(by_alias=True)))

    async def search_contracts(self, search_text: str, live: bool = False) -> List[ContractModel]:
        payload = {"live": live, "searchText": search_text}
        response_wrapper: SearchContractResponse = await self._request("POST", "/api/Contract/search", payload=payload, response_model=SearchContractResponse)
        if response_wrapper.success and response_wrapper.contracts is not None: return response_wrapper.contracts
        raise APIRequestError(response_wrapper.error_message or f"Failed to search contracts (Code: {response_wrapper.error_code})", response_text=str(response_wrapper.dict(by_alias=True)))

    async def place_order(self, order_request: PlaceOrderRequest) -> PlaceOrderResponse:
        logger.info(f"Placing order: {order_request.dict(by_alias=True, exclude_none=True)}")
        response: PlaceOrderResponse = await self._request("POST", "/api/Order/place", payload=order_request, response_model=PlaceOrderResponse)
        if not response.success:
            err_code = response.error_code.value if response.error_code else "N/A"
            logger.error(f"{response.error_message or f'Place order failed (Code: {err_code})'}. Response: {response.dict(by_alias=True)}")
        elif response.success and response.order_id is None:
             logger.warning(f"Order placement success but no order_id. Response: {response.dict(by_alias=True)}")
        return response

    async def modify_order(self, order_id: int, account_id: int, new_size: Optional[int]=None, new_limit_price: Optional[float]=None, new_stop_price: Optional[float]=None, new_trail_price: Optional[float]=None) -> ModifyOrderResponse:
        payload = ModifyOrderRequest(accountId=account_id, orderId=order_id, size=new_size, limitPrice=new_limit_price, stopPrice=new_stop_price, trailPrice=new_trail_price)
        logger.info(f"Modifying order {order_id} for account {account_id}: {payload.dict(by_alias=True, exclude_none=True)}")
        response: ModifyOrderResponse = await self._request("POST", "/api/Order/modify", payload=payload, response_model=ModifyOrderResponse)
        if not response.success:
            err_code = response.error_code.value if hasattr(response.error_code, 'value') else response.error_code
            logger.error(f"{response.error_message or f'Failed to modify order {order_id} (Code: {err_code})'}. Response: {response.dict(by_alias=True)}")
        return response

    async def cancel_order(self, order_id: int, account_id: int) -> CancelOrderResponse:
        payload = CancelOrderRequest(accountId=account_id, orderId=order_id)
        logger.info(f"Cancelling order {order_id} for account {account_id}")
        response: CancelOrderResponse = await self._request("POST", "/api/Order/cancel", payload=payload, response_model=CancelOrderResponse)
        if not response.success:
            err_code = response.error_code.value if hasattr(response.error_code, 'value') else response.error_code
            logger.error(f"{response.error_message or f'Failed to cancel order {order_id} (Code: {err_code})'}. Response: {response.dict(by_alias=True)}")
        return response

    async def get_order_details(self, order_id: int, account_id: int, search_hours_fallback: int = DEFAULT_ORDER_SEARCH_TIMESPAN_HOURS) -> Optional[OrderModel]:
        logger.info(f"Attempting to get details for order ID {order_id}, account ID {account_id}.")
        # API /api/Order/search needs a time range.
        # Fallback: search within a recent window and filter client-side.
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=search_hours_fallback)

        payload = SearchOrderRequest(
            accountId=account_id,
            startTimestamp=start_time,
            endTimestamp=end_time
        )
        logger.debug(f"Using fallback for get_order_details. Searching orders with payload: {payload.dict(by_alias=True)}")

        response_wrapper: SearchOrderResponse = await self._request(
            "POST",
            "/api/Order/search",
            payload=payload,
            response_model=SearchOrderResponse
        )

        if response_wrapper.success and response_wrapper.orders is not None:
            for order in response_wrapper.orders:
                if order.id == order_id:
                    logger.info(f"Order {order_id} found via time range search.")
                    return order
            logger.warning(f"Order {order_id} not found in the last {search_hours_fallback} hours for account {account_id}.")
            return None
        elif not response_wrapper.success:
            err_code = response_wrapper.error_code.value if hasattr(response_wrapper.error_code, 'value') else response_wrapper.error_code
            logger.error(f"Failed to search orders for get_order_details: {response_wrapper.error_message} (Code: {err_code})")
            return None
        return None

    async def get_historical_bars(self, contract_id: str, start_time: datetime, end_time: datetime, unit: AggregateBarUnit, unit_number: int, live: bool = False, limit: Optional[int] = None, include_partial_bar: bool = False) -> RetrieveBarResponse:
        logger.info(f"Fetching historical bars for {contract_id} from {start_time} to {end_time}")
        payload = RetrieveBarRequest(contractId=contract_id, live=live, startTime=start_time, endTime=end_time, unit=unit, unitNumber=unit_number, limit=limit, includePartialBar=include_partial_bar)
        response: RetrieveBarResponse = await self._request("POST", "/api/History/retrieveBars", payload=payload, response_model=RetrieveBarResponse)
        if not response.success:
            err_code = response.error_code.value if hasattr(response.error_code, 'value') else response.error_code
            logger.error(f"Get historical bars failed: {response.error_message or 'Unknown error'} (Code: {err_code})")
        return response

    async def get_open_orders(self, account_id: int) -> List[OrderModel]:
        logger.info(f"Fetching open orders for account ID: {account_id}")
        payload = {"accountId": account_id}
        response_wrapper: SearchOrderResponse = await self._request("POST", "/api/Order/searchOpen", payload=payload, response_model=SearchOrderResponse)
        if response_wrapper.success and response_wrapper.orders is not None: return response_wrapper.orders
        err_code = response_wrapper.error_code.value if hasattr(response_wrapper.error_code, 'value') else response_wrapper.error_code
        raise APIRequestError(response_wrapper.error_message or f"Failed to get open orders (Code: {err_code})", response_text=str(response_wrapper.dict(by_alias=True)))

    async def get_positions(self, account_id: int) -> List[PositionModel]:
        logger.info(f"Fetching positions for account ID: {account_id}")
        payload = {"accountId": account_id}
        response_wrapper: SearchPositionResponse = await self._request("POST", "/api/Position/searchOpen", payload=payload, response_model=SearchPositionResponse)
        if response_wrapper.success and response_wrapper.positions is not None: return response_wrapper.positions
        err_code = response_wrapper.error_code.value if hasattr(response_wrapper.error_code, 'value') else response_wrapper.error_code
        raise APIRequestError(response_wrapper.error_message or f"Failed to get positions (Code: {err_code})", response_text=str(response_wrapper.dict(by_alias=True)))

async def get_authenticated_client(username: Optional[str] = None, api_key: Optional[str] = None) -> APIClient:
    client = APIClient(username=username, api_key=api_key)
    await client.authenticate()
    return client
```
