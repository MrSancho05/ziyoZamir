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


# Rollar tartibi (ro'yxatlarda shu tartibda ko'rsatiladi)
ROLE_ORDER: tuple[Role, ...] = (Role.ADMIN, Role.BOSS, Role.STOREKEEPER, Role.WORKER)

# Yangi funksional qo'shilsa: Permission ga yangi qiymat qo'shing
# va kerakli rollarga shu yerda bering.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.DOWNLOAD_REPORTS,
        Permission.MANAGE_USERS,
    }),
    Role.BOSS: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.DOWNLOAD_REPORTS,
    }),
    Role.STOREKEEPER: frozenset({
        Permission.VIEW_DEFICIT,
        Permission.DOWNLOAD_REPORTS,
    }),
    Role.WORKER: frozenset({
        Permission.VIEW_DEFICIT,
    }),
}


def has_permission(role: Role | None, permission: Permission) -> bool:
    return role is not None and permission in ROLE_PERMISSIONS.get(role, frozenset())
