"""
=========================================================
Homez OS

File : app/engines/base/engine.py
Version : 1.0.0

Base Engine
=========================================================
"""

from abc import ABC
from abc import abstractmethod

from sqlalchemy.orm import Session


class BaseEngine(ABC):

    def __init__(
        self,
        db: Session,
    ):
        self.db = db

    @abstractmethod
    def process(
        self,
        *args,
        **kwargs,
    ):
        """
        Execute engine process.
        """
        raise NotImplementedError