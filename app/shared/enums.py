from enum import Enum


class Marketplace(str, Enum):
    COUPANG = "COUPANG"
    NAVER = "NAVER"


class AIStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"