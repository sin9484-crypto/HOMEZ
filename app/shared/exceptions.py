"""
HOMEZ AI Commerce OS

Shared Exceptions
"""


class HomezException(Exception):
    """HOMEZ 기본 예외"""

    def __init__(self, message: str, code: str = "HOMEZ_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class PermissionDenied(HomezException):

    def __init__(self):
        super().__init__(
            message="권한이 없습니다.",
            code="PERMISSION_DENIED",
        )


class ProductNotFound(HomezException):

    def __init__(self):
        super().__init__(
            message="상품을 찾을 수 없습니다.",
            code="PRODUCT_NOT_FOUND",
        )


class SupplierInactive(HomezException):

    def __init__(self):
        super().__init__(
            message="비활성 공급처입니다.",
            code="SUPPLIER_INACTIVE",
        )