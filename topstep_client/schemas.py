from pydantic import BaseModel, Field, field_validator # Removed HttpUrl as it's not used
from typing import Optional, List, Union, Any
from datetime import datetime, timezone # Added timezone
from enum import IntEnum

class BaseSchema(BaseModel):
    class Config:
        populate_by_name = True
        extra = 'ignore' 
        use_enum_values = True 

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

class OrderSide(IntEnum):
    Bid = 0
    Ask = 1

class OrderType(IntEnum):
    Unknown = 0
    Limit = 1
    Market = 2
    StopLimit = 3
    Stop = 4
    TrailingStop = 5
    JoinBid = 6
    JoinAsk = 7

class OrderStatus(IntEnum):
    NoneStatus = 0
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

# Specific error code enums for other responses based on Swagger structure
class SearchContractErrorCode(IntEnum):
    Success = 0
    # Add other specific codes if the API defines them

class SearchOrderErrorCode(IntEnum):
    Success = 0
    AccountNotFound = 1
    # Add other specific codes

class ModifyOrderErrorCode(IntEnum):
    Success = 0
    AccountNotFound = 1
    OrderNotFound = 2
    Rejected = 3
    Pending = 4
    UnknownError = 5
    AccountRejected = 6
    ContractNotFound = 7
    # Add other specific codes

class CancelOrderErrorCode(IntEnum):
    Success = 0
    AccountNotFound = 1
    OrderNotFound = 2
    Rejected = 3
    Pending = 4
    UnknownError = 5
    AccountRejected = 6
    # Add other specific codes

class SearchPositionErrorCode(IntEnum):
    Success = 0
    AccountNotFound = 1
    # Add other specific codes

class RetrieveBarErrorCode(IntEnum):
    Success = 0
    ContractNotFound = 1
    # Add other specific codes


class TokenResponse(BaseSchema):
    success: bool
    error_code: Optional[LoginErrorCode] = Field(default=None, alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    token: Optional[str] = None
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # Ensure timezone aware

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_login_error_code(cls, v):
        if v is None: return v
        try: return LoginErrorCode(v)
        except ValueError: return None # Or raise, or keep original if unknown

class TradingAccountModel(BaseSchema):
    id: int
    name: Optional[str] = None
    balance: float
    can_trade: bool = Field(..., alias='canTrade')
    is_visible: bool = Field(..., alias='isVisible')
    simulated: bool

class SearchAccountResponse(BaseSchema):
    success: bool
    error_code: SearchAccountErrorCode = Field(..., alias='errorCode')
    error_message: Optional[str] = Field(default=None)
    accounts: Optional[List[TradingAccountModel]] = Field(default_factory=list)

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_search_account_error_code(cls, v):
        return SearchAccountErrorCode(v)

class ContractModel(BaseSchema):
    id: str
    name: str
    description: Optional[str] = None
    tick_size: float = Field(..., alias='tickSize')
    tick_value: float = Field(..., alias='tickValue')
    active_contract: bool = Field(..., alias='activeContract')

class SearchContractResponse(BaseSchema):
    success: bool
    error_code: SearchContractErrorCode = Field(..., alias='errorCode') 
    error_message: Optional[str] = Field(default=None)
    contracts: Optional[List[ContractModel]] = Field(default_factory=list)

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_search_contract_error_code(cls, v):
        return SearchContractErrorCode(v)


class PlaceOrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    type: int # <--- Changed to int
    side: int # <--- Changed to int
    size: int
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    trail_price: Optional[float] = Field(default=None, alias='trailPrice')
    custom_tag: Optional[str] = Field(default=None, alias='customTag')
    linked_order_id: Optional[int] = Field(default=None, alias='linkedOrderId')

class OrderModel(BaseSchema):
    id: int
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    creation_timestamp: datetime = Field(..., alias='creationTimestamp')
    update_timestamp: Optional[datetime] = Field(default=None, alias='updateTimestamp')
    status: OrderStatus
    type: OrderType
    side: OrderSide
    size: int
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    fill_volume: int = Field(..., alias='fillVolume')

class SearchOrderRequest(BaseSchema): # Added this schema
    account_id: int = Field(..., alias="accountId")
    start_timestamp: datetime = Field(..., alias="startTimestamp")
    end_timestamp: datetime = Field(..., alias="endTimestamp")

class SearchOrderResponse(BaseSchema):
    success: bool
    error_code: SearchOrderErrorCode = Field(..., alias="errorCode") 
    error_message: Optional[str] = None
    orders: List[OrderModel] = Field(default_factory=list)

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_search_order_error_code(cls, v):
        return SearchOrderErrorCode(v)

class PlaceOrderResponse(BaseSchema):
    success: bool
    error_code: Optional[PlaceOrderErrorCode] = Field(default=None, alias="errorCode")
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    order_id: Optional[int] = Field(default=None, alias="orderId")

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_place_order_error_code(cls, v):
        if v is None: return v
        try: return PlaceOrderErrorCode(v)
        except ValueError: return None


class ModifyOrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    order_id: int = Field(..., alias='orderId')
    size: Optional[int] = None
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    trail_price: Optional[float] = Field(default=None, alias='trailPrice')

class ModifyOrderResponse(BaseSchema):
    success: bool
    error_code: ModifyOrderErrorCode = Field(..., alias="errorCode") 
    error_message: Optional[str] = None

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_modify_order_error_code(cls, v):
        return ModifyOrderErrorCode(v)


class CancelOrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    order_id: int = Field(..., alias='orderId')

class CancelOrderResponse(BaseSchema):
    success: bool
    error_code: CancelOrderErrorCode = Field(..., alias="errorCode") 
    error_message: Optional[str] = None

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_cancel_order_error_code(cls, v):
        return CancelOrderErrorCode(v)


class PositionModel(BaseSchema):
    id: int
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    creation_timestamp: datetime = Field(..., alias='creationTimestamp')
    type: PositionType
    size: int
    average_price: float = Field(..., alias='averagePrice')

class SearchPositionResponse(BaseSchema):
    success: bool
    error_code: SearchPositionErrorCode = Field(..., alias="errorCode") 
    error_message: Optional[str] = None
    positions: List[PositionModel] = Field(default_factory=list)

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_search_pos_error_code(cls, v):
        return SearchPositionErrorCode(v)


class AggregateBarModel(BaseSchema):
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: int

class RetrieveBarRequest(BaseSchema):
    contract_id: str = Field(..., alias="contractId")
    live: bool
    start_time: datetime = Field(..., alias="startTime")
    end_time: datetime = Field(..., alias="endTime")
    unit: AggregateBarUnit
    unit_number: int = Field(..., alias="unitNumber")
    limit: Optional[int] = None
    include_partial_bar: bool = Field(default=False, alias="includePartialBar")

class RetrieveBarResponse(BaseSchema):
    success: bool
    error_code: RetrieveBarErrorCode = Field(..., alias="errorCode") 
    error_message: Optional[str] = None
    bars: List[AggregateBarModel] = Field(default_factory=list)

    @field_validator('error_code', mode='before')
    @classmethod
    def _validate_retrieve_bar_error_code(cls, v):
        return RetrieveBarErrorCode(v)


class ErrorDetail(BaseSchema): # Kept for generic error use if needed
    error_code: Optional[str] = Field(default=None, alias='errorCode') 
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    details: Optional[Any] = None

class APIResponse(BaseSchema): # Generic wrapper
    success: bool
    data: Optional[Any] = None
    error: Optional[ErrorDetail] = None

class OpenOrderSchema(OrderModel): # Inherits all fields from OrderModel
    pass

# For backwards compatibility during transition - eventually remove these aliases
Account = TradingAccountModel
Contract = ContractModel
OrderRequest = PlaceOrderRequest # This was a major rename
OrderDetails = OrderModel       # This was a major rename
BarData = AggregateBarModel
HistoricalBarsResponse = RetrieveBarResponse # This was a major structural change in client
PositionSchema = PositionModel
