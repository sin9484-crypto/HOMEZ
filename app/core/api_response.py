"""
=========================================================
Homez OS

File : app/core/api_response.py
Version : 5.0.0

Standard API Response
=========================================================
"""

from datetime import datetime
from typing import Any


def success(
    data: Any = None,
    message: str = "Success",
) -> dict:

    return {
        "success": True,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
    }


def created(
    data: Any = None,
    message: str = "Created",
) -> dict:

    return {
        "success": True,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
    }


def accepted(
    data: Any = None,
    message: str = "Accepted",
) -> dict:

    return {
        "success": True,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
    }
def bad_request(
    message: str = "Bad Request",
    errors: Any = None,
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "errors": errors,
    }


def unauthorized(
    message: str = "Unauthorized",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }


def forbidden(
    message: str = "Forbidden",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }


def not_found(
    message: str = "Not Found",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }
def conflict(
    message: str = "Conflict",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }


def validation_error(
    errors: Any,
    message: str = "Validation Error",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "errors": errors,
    }


def server_error(
    message: str = "Internal Server Error",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }
def conflict(
    message: str = "Conflict",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }


def validation_error(
    errors: Any,
    message: str = "Validation Error",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "errors": errors,
    }


def server_error(
    message: str = "Internal Server Error",
) -> dict:

    return {
        "success": False,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }


__all__ = [
    "success",
    "created",
    "accepted",
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "validation_error",
    "server_error",
]
