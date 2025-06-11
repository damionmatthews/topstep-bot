from pydantic import BaseModel, Field, HttpUrl, field_validator
from typing import Optional, List, Union, Any
from datetime import datetime
from enum import IntEnum

# Base Pydantic Model Configuration
class BaseSchema(BaseModel):
    class Config:
        populate_by_name = True # Enables use of alias for field population
        extra = 'ignore'        # Ignores extra fields from API response
        use_enum_values = True  # Uses enum values (e.g., int) in serialization

# --- Enum Definitions ---

class LoginErrorCode(IntEnum):
    Success = 0
    UserNotFound = 1
    PasswordVerificationFailed = 2
    InvalidCredentials = 3
    AppNotFound = 4
    AppVerificationFailed = 5
    InvalidDevice = 6
    AgreementsNotSigned = 7
    UnknownError = 8
    ApiSubscriptionNotFound = 9
    ApiKeyAuthenticationDisabled = 10

class SearchAccountErrorCode(IntEnum):
    Success = 0
    # Add other codes if defined in Swagger
    UnknownError = 1 # Placeholder

class SearchContractErrorCode(IntEnum):
    Success = 0
    UnknownError = 1 # Placeholder

class PlaceOrderErrorCode(IntEnum):
    Success = 0
    AccountNotFound = 1
    OrderRejected = 2
    InsufficientFunds = 3
    AccountViolation = 4
    OutsideTradingHours = 5
    OrderPending = 6
    UnknownError = 7
    ContractNotFound = 8
    ContractNotActive = 9
    AccountRejected = 10

class ModifyOrderErrorCode(IntEnum):
    Success = 0
    OrderNotFound = 1
    OrderNotModifiable = 2
    UnknownError = 3 # Placeholder

class CancelOrderErrorCode(IntEnum):
    Success = 0
    OrderNotFound = 1
    OrderNotCancellable = 2
    UnknownError = 3 # Placeholder

class SearchOrderErrorCode(IntEnum):
    Success = 0
    UnknownError = 1 # Placeholder

class SearchPositionErrorCode(IntEnum):
    Success = 0
    UnknownError = 1 # Placeholder

class RetrieveBarErrorCode(IntEnum):
    Success = 0
    ContractNotFound = 1
    InvalidTimeRange = 2
    UnknownError = 3 # Placeholder

class OrderSide(IntEnum):
    Bid = 0  # Buy
    Ask = 1  # Sell

class OrderType(IntEnum):
    Unknown = 0
    Limit = 1
    Market = 2
    StopLimit = 3
    Stop = 4
    TrailingStop = 5 # Assuming this is from Swagger, if not, adjust
    JoinBid = 6      # Assuming from Swagger
    JoinAsk = 7      # Assuming from Swagger

class OrderStatus(IntEnum):
    NoneStatus = 0 # "None" is a keyword, so using "NoneStatus"
    Open = 1
    Filled = 2
    Cancelled = 3
    Expired = 4
    Rejected = 5
    Pending = 6

class PositionType(IntEnum):
    Undefined = 0
    Long = 1
    Short = 2

class AggregateBarUnit(IntEnum):
    Unspecified = 0
    Second = 1
    Minute = 2
    Hour = 3
    Day = 4
    Week = 5
    Month = 6

# --- Schema Definitions ---

class TokenResponse(BaseSchema):
    success: bool
    error_code: Optional[LoginErrorCode] = Field(default=None, alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    token: Optional[str] = None
    # Pydantic v1: default_factory for dynamic default values
    acquired_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator('error_code', pre=True, allow_reuse=True) # pre=True for Pydantic v1
    @classmethod
    def _validate_login_error_code(cls, v):
        if v is None:
            return v
        try:
            return LoginErrorCode(v)
        except ValueError:
            return LoginErrorCode.UnknownError # Or handle as per API spec for unknown codes

class TradingAccountModel(BaseSchema):
    id: int
    name: Optional[str] = None
    balance: float # Assuming this is always present
    can_trade: bool = Field(..., alias='canTrade')
    is_visible: bool = Field(..., alias='isVisible')
    simulated: bool

class SearchAccountResponse(BaseSchema):
    success: bool
    error_code: SearchAccountErrorCode = Field(..., alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage') # Alias if needed
    accounts: List[TradingAccountModel] = Field(default_factory=list)

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_sa_error_code(cls, v):
        try:
            return SearchAccountErrorCode(v)
        except ValueError:
            return SearchAccountErrorCode.UnknownError

class ContractModel(BaseSchema):
    id: str # contractId in some places, id in others; Swagger is the source of truth
    name: str
    description: Optional[str] = None
    tick_size: float = Field(..., alias='tickSize')
    tick_value: float = Field(..., alias='tickValue')
    active_contract: bool = Field(..., alias='activeContract')

class SearchContractResponse(BaseSchema):
    success: bool
    error_code: SearchContractErrorCode = Field(..., alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    contracts: List[ContractModel] = Field(default_factory=list)

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_sc_error_code(cls, v):
        try:
            return SearchContractErrorCode(v)
        except ValueError:
            return SearchContractErrorCode.UnknownError

class PlaceOrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    type: OrderType
    side: OrderSide
    size: int # Assuming this is quantity
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    trail_price: Optional[float] = Field(default=None, alias='trailPrice') # Check Swagger if this is part of PlaceOrder or ModifyOrder
    custom_tag: Optional[str] = Field(default=None, alias='customTag')
    linked_order_id: Optional[int] = Field(default=None, alias='linkedOrderId') # Check Swagger

class OrderModel(BaseSchema): # Formerly OrderDetails
    id: int # orderId in some places, id in others.
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    creation_timestamp: datetime = Field(..., alias='creationTimestamp')
    update_timestamp: Optional[datetime] = Field(default=None, alias='updateTimestamp')
    status: OrderStatus
    type: OrderType
    side: OrderSide
    size: int # Original quantity
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    fill_volume: int = Field(..., alias='fillVolume') # Quantity filled
    # avgFillPrice might be here too, check Swagger

class PlaceOrderResponse(BaseSchema):
    success: bool
    error_code: Optional[PlaceOrderErrorCode] = Field(default=None, alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    order_id: Optional[int] = Field(default=None, alias='orderId')

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_po_error_code(cls, v):
        if v is None:
            return v
        try:
            return PlaceOrderErrorCode(v)
        except ValueError:
            return PlaceOrderErrorCode.UnknownError

class ModifyOrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    order_id: int = Field(..., alias='orderId')
    size: Optional[int] = None # New quantity
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    trail_price: Optional[float] = Field(default=None, alias='trailPrice') # Check Swagger

class ModifyOrderResponse(BaseSchema):
    success: bool
    error_code: ModifyOrderErrorCode = Field(..., alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_mo_error_code(cls, v):
        try:
            return ModifyOrderErrorCode(v)
        except ValueError:
            return ModifyOrderErrorCode.UnknownError


class CancelOrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    order_id: int = Field(..., alias='orderId')

class CancelOrderResponse(BaseSchema):
    success: bool
    error_code: CancelOrderErrorCode = Field(..., alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_co_error_code(cls, v):
        try:
            return CancelOrderErrorCode(v)
        except ValueError:
            return CancelOrderErrorCode.UnknownError

class SearchOrderResponse(BaseSchema):
    success: bool
    error_code: SearchOrderErrorCode = Field(..., alias="errorCode")
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    orders: List[OrderModel] = Field(default_factory=list)

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_so_error_code(cls, v):
        try:
            return SearchOrderErrorCode(v)
        except ValueError:
            return SearchOrderErrorCode.UnknownError

class PositionModel(BaseSchema): # Formerly PositionSchema
    id: int # positionId
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    creation_timestamp: datetime = Field(..., alias='creationTimestamp')
    type: PositionType # Long or Short
    size: int # Current size of the position
    average_price: float = Field(..., alias='averagePrice')
    # unrealizedPnl, realizedPnl might be here too, check Swagger

class SearchPositionResponse(BaseSchema):
    success: bool
    error_code: SearchPositionErrorCode = Field(..., alias="errorCode")
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    positions: List[PositionModel] = Field(default_factory=list)

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_sp_error_code(cls, v):
        try:
            return SearchPositionErrorCode(v)
        except ValueError:
            return SearchPositionErrorCode.UnknownError

class AggregateBarModel(BaseSchema): # Formerly BarData
    t: datetime = Field(..., alias="timestamp") # Assuming 't' is 'timestamp'
    o: float = Field(..., alias="open")
    h: float = Field(..., alias="high")
    l: float = Field(..., alias="low")
    c: float = Field(..., alias="close")
    v: int = Field(..., alias="volume")

class RetrieveBarRequest(BaseSchema):
    contract_id: str = Field(..., alias="contractId")
    live: bool # Whether to retrieve live or historical bars
    start_time: datetime = Field(..., alias="startTime")
    end_time: datetime = Field(..., alias="endTime")
    unit: AggregateBarUnit
    unit_number: int = Field(..., alias="unitNumber") # e.g., 1 for 1 Minute, 5 for 5 Minutes
    limit: Optional[int] = None # Max number of bars to return
    include_partial_bar: bool = Field(default=False, alias="includePartialBar")

class RetrieveBarResponse(BaseSchema): # Formerly HistoricalBarsResponse
    success: bool
    error_code: RetrieveBarErrorCode = Field(..., alias="errorCode")
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    bars: List[AggregateBarModel] = Field(default_factory=list)

    @field_validator('error_code', pre=True, allow_reuse=True)
    @classmethod
    def _validate_rb_error_code(cls, v):
        try:
            return RetrieveBarErrorCode(v)
        except ValueError:
            return RetrieveBarErrorCode.UnknownError

# Generic Error/API Response (if used by the API consistently)
class ErrorDetail(BaseSchema):
    error_code: Optional[str] = Field(default=None, alias='errorCode') # Could be string or int based on API
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    details: Optional[Any] = None # For more complex error details

class APIResponse(BaseSchema): # A generic wrapper if API uses this structure
    success: bool
    data: Optional[Any] = None
    error: Optional[ErrorDetail] = None


# --- Backwards Compatibility Aliases ---
# These should be phased out eventually.
Account = TradingAccountModel
Contract = ContractModel
OrderRequest = PlaceOrderRequest
OrderDetails = OrderModel
PositionSchema = PositionModel
BarData = AggregateBarModel
HistoricalBarsResponse = RetrieveBarResponse

# This is a direct alias, if OpenOrderSchema is identical to OrderModel
# If it's meant to be a subset or different, it needs its own definition.
OpenOrderSchema = OrderModel
```
