from app.engines.permission.roles import Role
from app.engines.permission.permissions import Permission


class PermissionEngine:

    def has_permission(
        self,
        role: Role,
        permission: Permission,
    ) -> bool:

        if role == Role.MASTER:
            return True

        if role == Role.ADMIN:

            admin_permissions = {

                Permission.PRODUCT_READ,

                Permission.PRODUCT_WRITE,

                Permission.SUPPLIER_READ,

                Permission.SUPPLIER_WRITE,

                Permission.REPORT_READ,

            }

            return permission in admin_permissions

        if role == Role.STAFF:

            staff_permissions = {

                Permission.PRODUCT_READ,

            }

            return permission in staff_permissions

        return False


permission_engine = PermissionEngine()