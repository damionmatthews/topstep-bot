class TopstepAPIError(Exception):
    """Base class for exceptions in this module."""
    def __init__(self, message: str, status_code: int = None, response_text: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

class AuthenticationError(TopstepAPIError):
    """Raised when authentication fails."""
    pass

class APIRequestError(TopstepAPIError):
    """Raised for general API request errors (e.g., network issues, bad requests)."""
    pass

class APIResponseParsingError(TopstepAPIError):
    """Raised when the API response cannot be parsed as expected (e.g., Pydantic validation error)."""
    def __init__(self, message: str, raw_response_text: str = None, original_exception: Exception = None):
        super().__init__(message, response_text=raw_response_text)
        self.original_exception = original_exception

class ContractNotFoundError(TopstepAPIError):
    """Raised when a contract is not found."""
    pass

class OrderPlacementError(TopstepAPIError):
    """Raised when order placement fails."""
    pass
