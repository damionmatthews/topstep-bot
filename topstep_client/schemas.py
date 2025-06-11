from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Union, Any
from datetime import datetime

class BaseSchema(BaseModel):
    class Config:
        populate_by_name = True
        extra = 'ignore' # Allow ignoring extra fields from API response

class TokenResponse(BaseSchema):
    token: str = Field(..., alias='token')
    user_id: int = Field(..., alias='userId')
    acquired_at: datetime = Field(default_factory=datetime.utcnow)
    # Add other relevant fields if available from actual API response

class Account(BaseSchema):
    id: int
    name: Optional[str] = None
    user_id: Optional[int] = Field(default=None, alias='userId')
    account_type: Optional[str] = Field(default=None, alias='accountType')
    balance: float
    currency: Optional[str] = None
    active: Optional[bool] = None
    # Add other relevant fields based on API response

class Contract(BaseSchema):
    id: str # e.g., "CON.F.US.ENQ.M25"
    name: str # e.g., "E-mini NASDAQ 100 Index Future"
    description: Optional[str] = None
    tick_size: float = Field(..., alias='tickSize')
    tick_value: float = Field(..., alias='tickValue')
    currency: str
    instrument_id: Optional[int] = Field(default=None, alias="instrumentId")
    # Add other relevant fields

class OrderRequest(BaseSchema):
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    quantity: int = Field(..., alias='qty')
    side: str # "buy" or "sell"
    type: str # "market", "limit", "stop", "TrailingStop"
    trailing_distance: Optional[int] = Field(default=None, alias="trailingDistance")
    limit_price: Optional[float] = Field(default=None, alias='limitPrice')
    stop_price: Optional[float] = Field(default=None, alias='stopPrice')
    # Add other relevant fields like time_in_force, etc.

class OrderDetails(BaseSchema):
    id: int # Order ID from the API
    account_id: int = Field(..., alias='accountId')
    contract_id: str = Field(..., alias='contractId')
    quantity: int = Field(..., alias='qty')
    side: str
    type: str
    status: str # e.g., "Working", "Filled", "Cancelled"
    filled_quantity: Optional[int] = Field(default=None, alias='filledQuantity')
    average_fill_price: Optional[float] = Field(default=None, alias='averageFillPrice')
    # Add other relevant fields

class ErrorDetail(BaseSchema):
    error_code: Optional[str] = Field(default=None, alias='errorCode')
    error_message: Optional[str] = Field(default=None, alias='errorMessage')
    details: Optional[Any] = None

class APIResponse(BaseSchema):
    success: bool
    data: Optional[Any] = None # Actual data will be parsed into specific models
    error: Optional[ErrorDetail] = None

class BarData(BaseSchema):
    t: datetime # Timestamp (often as string from API, Pydantic can parse)
    o: float    # Open
    h: float    # High
    l: float    # Low
    c: float    # Close
    v: int      # Volume
    # Trades: Optional[int] = None # Number of trades, if available

class HistoricalBarsResponse(BaseSchema):
    bars: List[BarData] = Field(default_factory=list)
    # The API might wrap the list, e.g., {"data": {"bars": [...]}} or directly {"bars": [...]}
    # Or even just a direct list of bars. This needs to match the actual API response.
    # For now, assuming the API response is a list of bars directly or under a 'bars' key.
