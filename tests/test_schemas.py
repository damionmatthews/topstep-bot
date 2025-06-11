import pytest
from topstep_client.schemas import OrderRequest

def test_order_request_trailing_distance_alias():
    """
    Tests that OrderRequest serializes the 'trailing_distance' field
    with the correct alias 'trailDistance'.
    """
    order_data = {
        "account_id": 12345,
        "contract_id": "CON.F.US.XYZ",
        "quantity": 1,
        "side": 1,  # Assuming 1 for buy, 2 for sell as per prior side: int
        "type": 4,  # Assuming 4 for TrailingStop as per prior type: int
        "trailing_distance": 100
    }
    order_request = OrderRequest(**order_data)

    # Serialize the request model to a dictionary using aliases
    request_dict = order_request.model_dump(by_alias=True, exclude_none=True)

    # Check that 'trailDistance' is present and has the correct value
    assert "trailDistance" in request_dict
    assert request_dict["trailDistance"] == 100

    # Check that the old incorrect alias 'trailingStopTicks' is not present
    assert "trailingStopTicks" not in request_dict

    # Check that the original field name 'trailing_distance' is not present
    # when by_alias=True is used.
    assert "trailing_distance" not in request_dict

    # Ensure other fields are present as expected
    assert request_dict["accountId"] == 12345
    assert request_dict["contractId"] == "CON.F.US.XYZ"
    assert request_dict["size"] == 1 # 'quantity' is aliased to 'size'
    assert request_dict["side"] == 1
    assert request_dict["type"] == 4

def test_order_request_serialization_without_trailing_distance():
    """
    Tests that OrderRequest serializes correctly when 'trailing_distance'
    is not provided (should not include 'trailDistance' or 'trailingStopTicks').
    """
    order_data = {
        "account_id": 12345,
        "contract_id": "CON.F.US.XYZ",
        "quantity": 1,
        "side": 1,
        "type": 1 # e.g., Market order
    }
    order_request = OrderRequest(**order_data)
    request_dict = order_request.model_dump(by_alias=True, exclude_none=True)

    assert "trailDistance" not in request_dict
    assert "trailingStopTicks" not in request_dict
    assert "trailing_distance" not in request_dict


def test_import_order_side_from_topstep_client():
    from topstep_client import OrderSide

    assert OrderSide.Bid == 0
    assert OrderSide.Ask == 1


def test_import_order_type_from_topstep_client():
    from topstep_client import OrderType

    assert OrderType.Market == 2
    assert OrderType.Limit == 1


def test_import_position_type_from_topstep_client():
    from topstep_client import PositionType

    assert PositionType.Long == 1
    assert PositionType.Short == 2


def test_import_position_model_from_topstep_client():
    from topstep_client import PositionModel
    from pydantic import BaseModel

    assert PositionModel is not None
    assert issubclass(PositionModel, BaseModel)
