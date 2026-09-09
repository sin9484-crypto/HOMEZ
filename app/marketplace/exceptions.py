"""
=========================================================
Homez OS

File : app/marketplace/exceptions.py
Version : 1.0.0

Marketplace Exceptions
=========================================================
"""


class MarketplaceError(Exception):
    """Marketplace Base Exception"""

    default_message = "Marketplace Error"

    def __init__(self, message=None):
        super().__init__(message or self.default_message)


class AuthenticationError(MarketplaceError):
    default_message = "Authentication Failed"


class AuthorizationError(MarketplaceError):
    default_message = "Authorization Failed"


class InvalidTokenError(AuthenticationError):
    default_message = "Invalid Access Token"


class TokenExpiredError(AuthenticationError):
    default_message = "Access Token Expired"


class ApiConnectionError(MarketplaceError):
    default_message = "API Connection Failed"


class ApiTimeoutError(ApiConnectionError):
    default_message = "API Request Timeout"


class ApiRequestError(MarketplaceError):
    default_message = "Invalid API Request"


class ApiResponseError(MarketplaceError):
    default_message = "Unexpected API Response"


class ApiRateLimitError(MarketplaceError):
    default_message = "API Rate Limit Exceeded"


class MarketplaceUnavailableError(MarketplaceError):
    default_message = "Marketplace Service Unavailable"


class InvalidMarketplaceError(MarketplaceError):
    default_message = "Unsupported Marketplace"


class ProductSyncError(MarketplaceError):
    default_message = "Product Synchronization Failed"


class InventorySyncError(MarketplaceError):
    default_message = "Inventory Synchronization Failed"


class OrderSyncError(MarketplaceError):
    default_message = "Order Synchronization Failed"


class ShipmentSyncError(MarketplaceError):
    default_message = "Shipment Synchronization Failed"


class ReturnSyncError(MarketplaceError):
    default_message = "Return Synchronization Failed"