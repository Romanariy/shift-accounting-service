from dataclasses import dataclass


class WorkType:
    DEFAULT_SHIFT = "default_shift"
    BIG_ADMIN = "big_admin"
    SMALL_ADMIN = "small_admin"
    CYCLORAMA_PAINTING = "cyclorama_painting"
    CLEANING = "cleaning"


class PayCode:
    BIG_ADMIN = WorkType.BIG_ADMIN
    SMALL_ADMIN = WorkType.SMALL_ADMIN
    CYCLORAMA_PAINTING = WorkType.CYCLORAMA_PAINTING
    CLEANING = WorkType.CLEANING
    COMPANION = "companion"
    PHONE_WITH_BIG_ADMIN = "phone_with_big_admin"
    PHONE_WITHOUT_BIG_ADMIN = "phone_without_big_admin"


class CalculationType:
    FIXED = "fixed"
    HOURLY = "hourly"
    PER_UNIT = "per_unit"


class EntryStatus:
    CONFIRMED = "confirmed"
    NEEDS_REVIEW = "needs_review"


class EntrySource:
    TELEGRAM = "telegram"
    MANUAL = "manual"
    IMPORT = "import"


class SyncStatus:
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


WORK_TYPE_CHOICES = (
    (WorkType.BIG_ADMIN, "Большой админ"),
    (WorkType.SMALL_ADMIN, "Малый админ"),
    (WorkType.CYCLORAMA_PAINTING, "Покраска циклораммы"),
    (WorkType.CLEANING, "Уборка"),
)

PAY_CODE_CHOICES = WORK_TYPE_CHOICES + (
    (PayCode.COMPANION, "Сопровождение"),
    (PayCode.PHONE_WITH_BIG_ADMIN, "Телефоны при большом админе"),
    (PayCode.PHONE_WITHOUT_BIG_ADMIN, "Телефоны без большого админа"),
)

CALCULATION_TYPE_CHOICES = (
    (CalculationType.FIXED, "Фиксированная"),
    (CalculationType.HOURLY, "Почасовая"),
    (CalculationType.PER_UNIT, "За штуку"),
)

ENTRY_STATUS_CHOICES = (
    (EntryStatus.CONFIRMED, "Подтверждено"),
    (EntryStatus.NEEDS_REVIEW, "Нужно проверить"),
)

ENTRY_SOURCE_CHOICES = (
    (EntrySource.TELEGRAM, "Telegram"),
    (EntrySource.MANUAL, "Вручную"),
    (EntrySource.IMPORT, "Импорт"),
)

SYNC_STATUS_CHOICES = (
    (SyncStatus.PENDING, "Ожидает"),
    (SyncStatus.SYNCED, "Передано"),
    (SyncStatus.FAILED, "Ошибка"),
)


@dataclass(frozen=True)
class SeedEmployee:
    short_name: str
    full_name: str
    telegram_username: str
    default_work_type: str
    aliases: tuple[str, ...]


INITIAL_EMPLOYEES = (
    SeedEmployee("Рамис", "Рамис", "minca131", WorkType.SMALL_ADMIN, ("Рамис",)),
    SeedEmployee(
        "Наташа",
        "Наталья",
        "Natali_eve725",
        WorkType.BIG_ADMIN,
        ("Наташа", "Наталья", "Натали"),
    ),
    SeedEmployee("Полина", "Полина", "polink_aaa", WorkType.SMALL_ADMIN, ("Полина",)),
    SeedEmployee(
        "Ксюша",
        "Ксюша",
        "queenisneverlate",
        WorkType.SMALL_ADMIN,
        ("Ксюша", "Ксения"),
    ),
    SeedEmployee("Рома", "Рома", "", WorkType.SMALL_ADMIN, ("Рома", "Роман")),
)

INITIAL_PAY_RULES = (
    {
        "code": PayCode.BIG_ADMIN,
        "title": "Большой админ",
        "calculation_type": CalculationType.FIXED,
        "fixed_amount": "1400.00",
    },
    {
        "code": PayCode.SMALL_ADMIN,
        "title": "Малый админ",
        "calculation_type": CalculationType.HOURLY,
        "hourly_rate": "200.00",
        "min_amount": "600.00",
        "max_amount": "1200.00",
    },
    {
        "code": PayCode.CYCLORAMA_PAINTING,
        "title": "Покраска циклораммы",
        "calculation_type": CalculationType.FIXED,
        "fixed_amount": "1000.00",
    },
    {
        "code": PayCode.CLEANING,
        "title": "Уборка",
        "calculation_type": CalculationType.FIXED,
        "fixed_amount": "700.00",
    },
    {
        "code": PayCode.COMPANION,
        "title": "Сопровождение",
        "calculation_type": CalculationType.PER_UNIT,
        "fixed_amount": "500.00",
    },
    {
        "code": PayCode.PHONE_WITH_BIG_ADMIN,
        "title": "Телефоны при большом админе",
        "calculation_type": CalculationType.FIXED,
        "fixed_amount": "200.00",
    },
    {
        "code": PayCode.PHONE_WITHOUT_BIG_ADMIN,
        "title": "Телефоны без большого админа",
        "calculation_type": CalculationType.FIXED,
        "fixed_amount": "400.00",
    },
)
