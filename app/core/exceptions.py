"""
=========================================================
Homez OS

File : app/core/exceptions.py
Version : 2.1.0

Application Exceptions
=========================================================
"""

from fastapi import HTTPException
from fastapi import status


class AppException(
    HTTPException
):

    def __init__(
        self,
        status_code: int,
        detail: str,
        headers: dict | None = None,
    ):

        super().__init__(
            status_code=status_code,
            detail=detail,
            headers=headers,
        )


class BadRequestException(
    AppException
):

    def __init__(
        self,
        detail: str = "Bad request.",
    ):

        super().__init__(
            status.HTTP_400_BAD_REQUEST,
            detail,
        )


class UnauthorizedException(
    AppException
):

    def __init__(
        self,
        detail: str = "Unauthorized.",
        headers: dict | None = None,
    ):

        super().__init__(
            status.HTTP_401_UNAUTHORIZED,
            detail,
            headers=headers,
        )


class ForbiddenException(
    AppException
):

    def __init__(
        self,
        detail: str = "Forbidden.",
    ):

        super().__init__(
            status.HTTP_403_FORBIDDEN,
            detail,
        )
class NotFoundException(
    AppException
):

    def __init__(
        self,
        detail: str = "Resource not found.",
    ):

        super().__init__(
            status.HTTP_404_NOT_FOUND,
            detail,
        )


class ConflictException(
    AppException
):

    def __init__(
        self,
        detail: str = "Conflict occurred.",
    ):

        super().__init__(
            status.HTTP_409_CONFLICT,
            detail,
        )


class ValidationException(
    AppException
):

    def __init__(
        self,
        detail: str = "Validation failed.",
    ):

        super().__init__(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail,
        )


class TooManyRequestsException(
    AppException
):

    def __init__(
        self,
        detail: str = "Too many requests.",
        headers: dict | None = None,
    ):

        super().__init__(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail,
            headers=headers,
        )
class InternalServerException(
    AppException
):

    def __init__(
        self,
        detail: str = "Internal server error.",
    ):

        super().__init__(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail,
        )


class ServiceUnavailableException(
    AppException
):

    def __init__(
        self,
        detail: str = "Service unavailable.",
    ):

        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail,
        )


__all__ = [
    "AppException",
    "BadRequestException",
    "UnauthorizedException",
    "ForbiddenException",
    "NotFoundException",
    "ConflictException",
    "ValidationException",
    "TooManyRequestsException",
    "InternalServerException",
    "ServiceUnavailableException",
]