from enum import Enum


class Role(str, Enum):
    MASTER = "MASTER"
    ADMIN = "ADMIN"
    STAFF = "STAFF"
    VIEWER = "VIEWER"