from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    BOSS = "boss"
    STOREKEEPER = "storekeeper"
    WORKER = "worker"


class Permission(str, Enum):
    VIEW_DEFICIT = "view_deficit"
    DOWNLOAD_REPORTS = "download_reports"
    MANAGE_USERS = "manage_users"
    MARK_ATTENDANCE = "mark_attendance"
    VIEW_ATTENDANCE = "view_attendance"
    MARK_EXPENSES = "mark_expenses"
    VIEW_OWN_ATTENDANCE = "view_own_attendance"
    VIEW_EXPENSES = "view_expenses"
    VIEW_TOP_PRODUCTS = "view_top_products"   # "Top mahsulotlar": FAQAT admin
    MANAGE_WAREHOUSE = "manage_warehouse"     # Kirim / Chiqim / Mahsulotlar: FAQAT admin va sklad nazoratchisi


# Rollar tartibi (ro'yxatlarda shu tartibda ko'rsatiladi)
ROLE_ORDER: tuple[Role, ...] = (Role.ADMIN, Role.BOSS, Role.STOREKEEPER, Role.WORKER)

# Yangi funksional qo'shilsa: Permission ga yangi qiymat qo'shing
# va kerakli rollarga shu yerda bering.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.DOWNLOAD_REPORTS,
        Permission.MANAGE_USERS,
        Permission.MARK_ATTENDANCE,
        Permission.VIEW_ATTENDANCE,
        Permission.VIEW_EXPENSES,
        Permission.MARK_EXPENSES,
        Permission.VIEW_TOP_PRODUCTS,
        Permission.MANAGE_WAREHOUSE,
    }),
    Role.BOSS: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.DOWNLOAD_REPORTS,
        Permission.VIEW_ATTENDANCE,
        Permission.VIEW_EXPENSES,
    }),
    Role.STOREKEEPER: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.DOWNLOAD_REPORTS,
        Permission.MARK_ATTENDANCE,
        Permission.MARK_EXPENSES,
        Permission.MANAGE_WAREHOUSE,
    }),
    Role.WORKER: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.VIEW_OWN_ATTENDANCE,
    }),
}


def has_permission(role: Role | None, permission: Permission) -> bool:
    return role is not None and permission in ROLE_PERMISSIONS.get(role, frozenset())
