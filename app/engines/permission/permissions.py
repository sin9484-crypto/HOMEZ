from enum import Enum


class Permission(str, Enum):

    # ======================
    # System
    # ======================

    SYSTEM = "system"

    SYSTEM_CONFIG = "system.config"

    SYSTEM_LOG = "system.log"

    SYSTEM_BACKUP = "system.backup"

    SYSTEM_PLUGIN = "system.plugin"

    SYSTEM_AI = "system.ai"

    # ======================
    # Business
    # ======================

    PRODUCT_READ = "product.read"

    PRODUCT_WRITE = "product.write"

    PRODUCT_DELETE = "product.delete"

    SUPPLIER_READ = "supplier.read"

    SUPPLIER_WRITE = "supplier.write"

    REPORT_READ = "report.read"