"""Конфигурация дашборда: кабинеты, лейблы, экономические коэффициенты."""
import os
from dataclasses import dataclass

DB_PATH = "dashboard.db"

SHOPS = ["shop_a", "shop_b"]


def order_shops(shops):
    """Сортирует ключи кабинетов в каноническом порядке SHOPS."""
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
    "shop_a": Shop(
        key="shop_a",
        label="Кабинет A",
        client_id_default="",
        has_premium=True,
        has_performance=True,
    ),
    "shop_b": Shop(
        key="shop_b",
        label="Кабинет B",
        client_id_default="",
        has_premium=True,
        has_performance=True,
    ),
}

SHOP_LABELS = {s.key: s.label for s in SHOP_DEFS.values()}
SHOP_COLORS = {"shop_a": "#2962FF", "shop_b": "#00C853"}

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
# К себестоимости товара добавляются доли на прочие расходы: комиссия площадки,
# заложенный ДРР (реклама), налог, эквайринг. Значения настраиваются в .env
# под конкретный бизнес.
COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", "0.20"))   # комиссия площадки
DRR_RATE = float(os.getenv("DRR_RATE", "0.15"))                 # заложенные расходы на ДРР
TAX_RATE = float(os.getenv("TAX_RATE", "0.07"))                 # налог
ACQUIRING_RATE = float(os.getenv("ACQUIRING_RATE", "0.025"))    # эквайринг
FULL_COST_MULTIPLIER = 1.0 + COMMISSION_RATE + DRR_RATE + TAX_RATE + ACQUIRING_RATE

_PREMIUM_SHOPS = {s.key for s in SHOP_DEFS.values() if s.has_premium}
_PERFORMANCE_SHOPS = {s.key for s in SHOP_DEFS.values() if s.has_performance}

def is_premium(shop: str) -> bool:
    return shop in _PREMIUM_SHOPS

def has_performance_api(shop: str) -> bool:
    return shop in _PERFORMANCE_SHOPS

PRESETS: dict[str, tuple[str, callable]] = {}  # filled lazily by shared_ui
