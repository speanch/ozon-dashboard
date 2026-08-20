from dataclasses import dataclass

DB_PATH = "dashboard.db"

SHOPS = ["ozon_stylint", "ozon_rs"]

@dataclass(frozen=True)
class Shop:
    key: str
    label: str
    client_id_default: str
    has_premium: bool = False
    has_performance: bool = False

SHOP_DEFS = {
    "ozon_stylint": Shop(
        key="ozon_stylint",
        label="Stylint",
        client_id_default="443362",
        has_premium=True,
        has_performance=True,
    ),
    "ozon_rs": Shop(
        key="ozon_rs",
        label="Room Saver",
        client_id_default="3201725",
        has_premium=True,
        has_performance=True,
    ),
}

SHOP_LABELS = {s.key: s.label for s in SHOP_DEFS.values()}
SHOP_COLORS = {"ozon_stylint": "#2962FF", "ozon_rs": "#00C853"}

STATUS_LABELS = {
    "awaiting_packaging": "Ожидает сборки",
    "awaiting_deliver": "Ожидает отгрузки",
    "delivering": "В пути",
    "delivered": "Доставлен",
    "cancelled": "Отменён",
    "returned": "Возврат",
}

OZON_API_BASE = "https://api-seller.ozon.ru"

_PREMIUM_SHOPS = {s.key for s in SHOP_DEFS.values() if s.has_premium}
_PERFORMANCE_SHOPS = {s.key for s in SHOP_DEFS.values() if s.has_performance}

def is_premium(shop: str) -> bool:
    return shop in _PREMIUM_SHOPS

def has_performance_api(shop: str) -> bool:
    return shop in _PERFORMANCE_SHOPS

PRESETS: dict[str, tuple[str, callable]] = {}  # filled lazily by shared_ui
