import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Adjust the import path based on your project structure if main.py is not in the root
# Assuming main.py is in the root and contains the 'app' and 'api_client' instances.
from main import app, api_client as main_api_client
from topstep_client.schemas import OrderRequest, PlaceOrderResponse
from topstep_client import APIClient # Import APIClient for spec

@pytest.fixture
def client():
    # This fixture will be used to make requests to the FastAPI app
    # It also handles startup/shutdown events for the app context
    with TestClient(app) as c:
        yield c

@pytest.fixture
def mock_api_client():
    # Create a MagicMock instance that mimics the APIClient
    # We use 'spec=APIClient' to ensure the mock only allows methods that exist on APIClient
    mocked_client = MagicMock(spec=APIClient)

    # Set up a default return value for place_order
    # It must be an awaitable if the original is async
    async def async_place_order_mock(*args, **kwargs):
        return PlaceOrderResponse(orderId=12345, success=True, errorCode=None, errorMessage=None)

    mocked_client.place_order = MagicMock(side_effect=async_place_order_mock)

    # If other methods of api_client are called during the request, mock them too
    # For example, if initialize_topstep_client calls authenticate:
    async def async_authenticate_mock(*args, **kwargs):
        # Simulate successful authentication if needed by startup
        mocked_client._session_token = "mock_token" # if anything in main checks this
        return MagicMock() # Or a mock TokenResponse
    mocked_client.authenticate = MagicMock(side_effect=async_authenticate_mock)

    async def async_get_accounts_mock(*args, **kwargs):
        return [] # Default to no accounts
    mocked_client.get_accounts = MagicMock(side_effect=async_get_accounts_mock)

    # Patch 'main.api_client' to use this mock for the duration of the test
    # This assumes 'api_client' in 'main.py' is a module-level variable.
    with patch('main.api_client', new=mocked_client) as patched_client:
        yield patched_client


def test_post_manual_trailing_stop_order_success(client, mock_api_client: MagicMock): # mock_api_client is injected by the fixture
    """
    Tests the /manual/trailing_stop_order endpoint.
    Verifies that it calls api_client.place_order with an OrderRequest
    that correctly serializes 'trailing_distance' to 'trailDistance'.
    """
    payload = {
        "accountId": 8027309,
        "contractId": "CON.F.US.EP.M25",
        "side": "long",
        "size": 1,
        "trailingStopTicks": 100
    }

    response = client.post("/manual/trailing_stop_order", json=payload)

    assert response.status_code == 200
    response_json = response.json()
    assert response_json["success"] is True
    assert "details" in response_json
    assert response_json["details"]["orderId"] == 12345 # From mock return

    # Check that api_client.place_order was called once
    mock_api_client.place_order.assert_called_once()

    # Get the arguments passed to place_order
    args, kwargs = mock_api_client.place_order.call_args
    assert len(args) == 1
    called_order_request = args[0]

    # Verify the type and content of the OrderRequest object
    assert isinstance(called_order_request, OrderRequest)
    assert called_order_request.account_id == payload["accountId"]
    assert called_order_request.contract_id == payload["contractId"]
    assert called_order_request.quantity == payload["size"]
    assert called_order_request.side == 0  # 'long' maps to 0
    assert called_order_request.type == 5  # TrailingStop type
    assert called_order_request.trailing_distance == payload["trailingStopTicks"]

    # Verify how this OrderRequest object *would* serialize
    serialized_order_request = called_order_request.model_dump(by_alias=True, exclude_none=True)

    assert "trailDistance" in serialized_order_request
    assert serialized_order_request["trailDistance"] == payload["trailingStopTicks"]
    assert "trailingStopTicks" not in serialized_order_request
    assert "trailing_distance" not in serialized_order_request


def test_post_manual_trailing_stop_order_missing_ticks(client, mock_api_client: MagicMock):
    payload = {
        "accountId": 8027309,
        "contractId": "CON.F.US.EP.M25",
        "side": "long",
        "size": 1,
    }
    response = client.post("/manual/trailing_stop_order", json=payload)
    # FastAPI/Pydantic V2 might return 422 if the field is required in ManualTradeParams
    # and not Optional with a default that makes sense for this check.
    # The current ManualTradeParams has `trailing_stop_ticks: Optional[int] = Field(default=None, ...)`
    # The endpoint logic then checks: `if params.trailing_stop_ticks is None or params.trailing_stop_ticks <= 0:`
    # So, if it's None (missing from payload), this check should trigger.
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["success"] is False
    assert "Trailing stop ticks must be a positive integer" in response_json["message"]
    mock_api_client.place_order.assert_not_called()


def test_post_manual_trailing_stop_order_zero_ticks(client, mock_api_client: MagicMock):
    payload = {
        "accountId": 8027309,
        "contractId": "CON.F.US.EP.M25",
        "side": "long",
        "size": 1,
        "trailingStopTicks": 0
    }
    response = client.post("/manual/trailing_stop_order", json=payload)
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["success"] is False
    assert "Trailing stop ticks must be a positive integer" in response_json["message"]
    mock_api_client.place_order.assert_not_called()
