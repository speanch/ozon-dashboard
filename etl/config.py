from dataclasses import dataclass

DB_PATH = "dashboard.db"

SHOPS = ["ozon_stylint", "ozon_rs"]


def order_shops(shops):
    """Сортирует ключи кабинетов в каноническом порядке SHOPS (Stylint первым, затем Room Saver)."""
    order = {s: i for i, s in enumerate(SHOPS)}
    return sorted(shops, key=lambda s: (order.get(s, len(order)), s))

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

# ── коэффициенты полной себестоимости (для расчёта «прибыли по принятым») ──
# К себестоимости товара добавляются доли на прочие расходы:
COMMISSION_RATE = 0.55   # комиссия Озона
DRR_RATE = 0.15          # заложенные расходы на ДРР (рекламу)
TAX_RATE = 0.07          # налог
ACQUIRING_RATE = 0.025   # эквайринг
FULL_COST_MULTIPLIER = 1.0 + COMMISSION_RATE + DRR_RATE + TAX_RATE + ACQUIRING_RATE

_PREMIUM_SHOPS = {s.key for s in SHOP_DEFS.values() if s.has_premium}
_PERFORMANCE_SHOPS = {s.key for s in SHOP_DEFS.values() if s.has_performance}

def is_premium(shop: str) -> bool:
    return shop in _PREMIUM_SHOPS

def has_performance_api(shop: str) -> bool:
    return shop in _PERFORMANCE_SHOPS

PRESETS: dict[str, tuple[str, callable]] = {}  # filled lazily by shared_ui
