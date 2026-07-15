"""
HOMEZ AI Commerce OS

Shared Result Object
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class Result:

    success: bool

    code: str

    message: str

    data: Any = None

    @classmethod
    def ok(
        cls,
        data=None,
        message="Success",
        code="SUCCESS",
    ):
        return cls(
            success=True,
            code=code,
            message=message,
            data=data,
        )

    @classmethod
    def fail(
        cls,
        message,
        code="ERROR",
        data=None,
    ):
        return cls(
            success=False,
            code=code,
            message=message,
            data=data,
        )