from sqlalchemy.orm import Session

from app.domains.settings.model import Setting


DEFAULT_SETTINGS = [
    {
        "key": "APP_NAME",
        "value": "Homez",
        "category": "SYSTEM",
        "description": "Application Name",
        "is_system": True,
    },
    {
        "key": "APP_VERSION",
        "value": "0.1.0",
        "category": "SYSTEM",
        "description": "Application Version",
        "is_system": True,
    },
    {
        "key": "DEFAULT_LANGUAGE",
        "value": "ko",
        "category": "SYSTEM",
        "description": "Default Language",
        "is_system": True,
    },
    {
        "key": "DEFAULT_TIMEZONE",
        "value": "Asia/Seoul",
        "category": "SYSTEM",
        "description": "Default Timezone",
        "is_system": True,
    },
    {
        "key": "LOGIN_MAX_FAIL",
        "value": "5",
        "category": "SECURITY",
        "description": "Maximum Login Fail Count",
        "is_system": True,
    },
    {
        "key": "PASSWORD_MIN_LENGTH",
        "value": "8",
        "category": "SECURITY",
        "description": "Minimum Password Length",
        "is_system": True,
    },
    {
        "key": "UPLOAD_MAX_SIZE",
        "value": "10485760",
        "category": "UPLOAD",
        "description": "Maximum Upload Size(Byte)",
        "is_system": True,
    },
]


def initialize_setting_seed(db: Session) -> None:
    """
    Initialize default application settings.
    Existing settings are not overwritten.
    """

    for item in DEFAULT_SETTINGS:
        exists = (
            db.query(Setting)
            .filter(Setting.key == item["key"])
            .first()
        )

        if exists:
            continue

        db.add(
            Setting(
                key=item["key"],
                value=item["value"],
                category=item["category"],
                description=item["description"],
                is_system=item["is_system"],
                active=True,
            )
        )

    db.commit()