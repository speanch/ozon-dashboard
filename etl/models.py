"""SQLAlchemy models — поля извлечены из calculator_MP/models/marketplace_order.py."""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Float, String, Text, DateTime, Date, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class Order(Base):
    """Заказы из Ozon/WB/Yandex. Соответствует marketplace.order в Odoo."""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, comment="Номер отправления")
    marketplace = Column(String(50), nullable=False, index=True)
    marketplace_status = Column(String(100))
    order_date = Column(DateTime, index=True)
    order_source = Column(String(50))
    state = Column(String(20), default="draft")

    # ── товары ──
    sku_bed = Column(String(100))
    sku_mattress = Column(String(100))
    sku_floor_frame = Column(String(100))
    sku_left_armrest = Column(String(100))
    sku_right_armrest = Column(String(100))
    mezzanine_bed_sku = Column(String(100))
    mezzanine_cabinet_1_sku = Column(String(100))
    mezzanine_cabinet_2_sku = Column(String(100))
    mezzanine_skolye_sku = Column(String(100))
    cabinet_1_sku = Column(String(100))
    cabinet_2_sku = Column(String(100))
    cabinet_3_sku = Column(String(100))
    product_name = Column(String(500))

    qty_bed = Column(Integer, default=0)
    qty_mattress = Column(Integer, default=0)
    qty_floor_frame = Column(Integer, default=0)
    qty_left_armrest = Column(Integer, default=0)
    qty_right_armrest = Column(Integer, default=0)
    qty_mezzanine_bed = Column(Integer, default=0)
    qty_mezzanine_cabinet_1 = Column(Integer, default=0)
    qty_mezzanine_cabinet_2 = Column(Integer, default=0)
    qty_mezzanine_skolye = Column(Integer, default=0)
    qty_cabinet_1 = Column(Integer, default=0)
    qty_cabinet_2 = Column(Integer, default=0)
    qty_cabinet_3 = Column(Integer, default=0)

    # ── клиент и доставка ──
    customer_name = Column(String(255))
    phone = Column(String(50))
    email = Column(String(255))
    customer_comment = Column(Text)
    delivery_address = Column(Text)
    region = Column(String(100))
    distance_mkad = Column(Float, default=0.0)
    elevator = Column(String(20))
    floor = Column(String(20))
    delivery_type = Column(String(20), default="delivery")
    has_assembly = Column(String(10), default="yes")

    # ── финансы (из calculator_MP:246-273) ──
    price_bed = Column(Float, default=0.0)
    price_mattress = Column(Float, default=0.0)
    price_floor_frame = Column(Float, default=0.0)
    price_left_armrest = Column(Float, default=0.0)
    price_right_armrest = Column(Float, default=0.0)
    price_mezzanine_bed = Column(Float, default=0.0)
    price_mezzanine_cabinet_1 = Column(Float, default=0.0)
    price_mezzanine_cabinet_2 = Column(Float, default=0.0)
    price_mezzanine_skolye = Column(Float, default=0.0)
    price_cabinet_1 = Column(Float, default=0.0)
    price_cabinet_2 = Column(Float, default=0.0)
    price_cabinet_3 = Column(Float, default=0.0)

    service_delivery = Column(Float, default=0.0)
    service_furniture_lifting = Column(Float, default=0.0)
    service_mattress_lifting = Column(Float, default=0.0)
    service_assembly_cost = Column(Float, default=0.0)
    service_assembler_team = Column(Float, default=0.0)

    ozon_costs_data = Column(Text)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    @property
    def total_price(self) -> float:
        return (
            self.price_bed
            + self.price_mattress
            + self.price_floor_frame
            + self.price_left_armrest
            + self.price_right_armrest
            + self.price_mezzanine_bed
            + self.price_mezzanine_cabinet_1
            + self.price_mezzanine_cabinet_2
            + self.price_mezzanine_skolye
            + self.price_cabinet_1
            + self.price_cabinet_2
            + self.price_cabinet_3
        )

    @property
    def total_service(self) -> float:
        return (
            self.service_delivery
            + self.service_furniture_lifting
            + self.service_mattress_lifting
            + self.service_assembly_cost
            + self.service_assembler_team
        )


class DailyMetric(Base):
    """Агрегированная дневная метрика (из /v1/analytics/data)."""
    __tablename__ = "daily_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    sku = Column(String(100), index=True)
    product_name = Column(String(500))
    revenue = Column(Float, default=0.0)
    ordered_units = Column(Integer, default=0)
    returns = Column(Integer, default=0)
    cancellations = Column(Integer, default=0)
    raw_json = Column(Text)


class StockSnapshot(Base):
    """Снимок остатков на складах (из /v2/analytics/stock_on_warehouses)."""
    __tablename__ = "stock_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    sku = Column(String(100), index=True)
    product_name = Column(String(500))
    warehouse_name = Column(String(200))
    quantity = Column(Integer, default=0)
    reserved = Column(Integer, default=0)
    in_transit = Column(Integer, default=0)


class FinanceTransaction(Base):
    """Финансовая транзакция (из /v3/finance/transaction/list)."""
    __tablename__ = "finance_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    operation_date = Column(Date, nullable=False, index=True)
    operation_type = Column(String(100), index=True)
    operation_type_name = Column(String(200))
    posting_number = Column(String(100))
    amount = Column(Float, default=0.0)
    raw_json = Column(Text)


class CashFlow(Base):
    """Движение денежных средств (из /v1/finance/cash-flow-statement/list)."""
    __tablename__ = "cash_flows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    cash_flow_type = Column(String(100), comment="income/expense")
    operation = Column(String(200))
    amount = Column(Float, default=0.0)
    raw_json = Column(Text)


class FinanceBalance(Base):
    """Текущий баланс кабинета (из /v1/finance/balance → closing_balance)."""
    __tablename__ = "finance_balance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    balance = Column(Float, default=0.0)
    raw_json = Column(Text)


class AdDailyStats(Base):
    """Дневная статистика рекламы (из Performance API)."""
    __tablename__ = "ad_daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    campaign_id = Column(String(50))
    campaign_name = Column(String(200))
    sku = Column(String(100), index=True)
    product_name = Column(String(500))
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0.0)
    cart_adds = Column(Integer, default=0)
    avg_cpc = Column(Float, default=0.0)
    spend = Column(Float, default=0.0)
    units_sold = Column(Integer, default=0)
    promo_revenue = Column(Float, default=0.0)
    total_order_amount = Column(Float, default=0.0)
    promo_acos = Column(Float, default=0.0, comment="ДРР в продвижении, %")
    overall_acos = Column(Float, default=0.0, comment="ДРР общий, %")


class ProductPrice(Base):
    """Снимок цен товара (из /v5/product/info/prices)."""
    __tablename__ = "product_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    offer_id = Column(String(100), index=True)
    product_id = Column(String(50), index=True)
    price = Column(Float, default=0.0)
    old_price = Column(Float, default=0.0)
    min_price = Column(Float, default=0.0)
    marketing_price = Column(Float, default=0.0)
    retail_price = Column(Float, default=0.0)
    price_index = Column(Float, default=0.0)
    color_index = Column(String(30))
    commission_fbo = Column(Float, default=0.0)
    commission_fbs = Column(Float, default=0.0)
    raw_json = Column(Text)


class ProductMapping(Base):
    """Мост sku (числовой Ozon SKU) ↔ offer_id (артикул селлера) ↔ название."""
    __tablename__ = "product_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    sku = Column(String(50), nullable=False, index=True)
    offer_id = Column(String(100), index=True)
    product_id = Column(String(50))
    name = Column(String(500))


class BoostSnapshot(Base):
    """Снимок эластичного бустинга (акция ELASTIC_BOOSTING из /v1/actions + /v1/actions/products)."""
    __tablename__ = "boost_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop = Column(String(50), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    product_id = Column(String(50), index=True)
    offer_id = Column(String(100), index=True)
    sku = Column(String(50))
    name = Column(String(500))
    current_boost = Column(Float, default=0.0)
    min_boost = Column(Float, default=0.0)
    max_boost = Column(Float, default=0.0)
    price_min_elastic = Column(Float, default=0.0)
    price_max_elastic = Column(Float, default=0.0)
    action_price = Column(Float, default=0.0)
    price = Column(Float, default=0.0)
    max_action_price = Column(Float, default=0.0)
    stock = Column(Integer, default=0)
    add_mode = Column(String(20))


def init_db(path: str = "dashboard.db"):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return engine


def get_session(engine) -> Session:
    return Session(engine)