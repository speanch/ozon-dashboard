"""SQLAlchemy models — поля извлечены из calculator_MP/models/marketplace_order.py."""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Float, String, Text, DateTime, Date, create_engine, event
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
    net_price = Column(Float, default=0.0, comment="Себестоимость товара")
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


class EtlState(Base):
    """Ключ-значение: последняя успешная синхронизация по сущности и кабинету.

    Используется для инкрементальной догрузки (аналитика, финансы), чтобы
    не перекачивать полное окно на каждом запуске.
    """
    __tablename__ = "etl_state"

    key = Column(String(100), primary_key=True)  # e.g. "analytics:ozon_rs"
    last_sync = Column(Date)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Experiment(Base):
    """Временная метка эксперимента для графиков динамики (начало–конец + название)."""
    __tablename__ = "experiments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    description = Column(Text)
    shop = Column(String(50), index=True, comment="Кабинеты через запятую (ozon_stylint,ozon_rs)")
    offer_ids = Column(Text, comment="Артикулы кровати через запятую")
    result = Column(Text, comment="Результат эксперимента")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine)
    _migrate(engine)
    return engine


def _migrate(engine):
    """Добавляет недостающие колонки в существующие таблицы (без потери данных)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "experiments" in tables:
            cols = {c["name"] for c in inspector.get_columns("experiments")}
            if "shop" not in cols:
                conn.execute(text("ALTER TABLE experiments ADD COLUMN shop VARCHAR(50)"))
            if "offer_ids" not in cols:
                conn.execute(text("ALTER TABLE experiments ADD COLUMN offer_ids TEXT"))
            if "result" not in cols:
                conn.execute(text("ALTER TABLE experiments ADD COLUMN result TEXT"))

            info = conn.execute(text("PRAGMA table_info(experiments)")).fetchall()
            end_row = next((r for r in info if r[1] == "end_date"), None)
            if end_row is not None and end_row[3]:
                conn.execute(text(
                    "CREATE TABLE experiments_new ("
                    "id INTEGER NOT NULL, "
                    "name VARCHAR(200) NOT NULL, "
                    "start_date DATE NOT NULL, "
                    "end_date DATE, "
                    "description TEXT, "
                    "created_at DATETIME, "
                    "shop VARCHAR(50), "
                    "offer_ids TEXT, "
                    "result TEXT, "
                    "PRIMARY KEY (id))"
                ))
                conn.execute(text(
                    "INSERT INTO experiments_new "
                    "(id, name, start_date, end_date, description, created_at, shop, offer_ids, result) "
                    "SELECT id, name, start_date, end_date, description, created_at, shop, offer_ids, result "
                    "FROM experiments"
                ))
                conn.execute(text("DROP TABLE experiments"))
                conn.execute(text("ALTER TABLE experiments_new RENAME TO experiments"))

        if "product_prices" in tables:
            pcols = {c["name"] for c in inspector.get_columns("product_prices")}
            if "net_price" not in pcols:
                conn.execute(text("ALTER TABLE product_prices ADD COLUMN net_price FLOAT DEFAULT 0.0"))

        # Убираем дубликаты, накопившиеся до появления инкрементальной догрузки
        # с дедупликацией (одинаковые строки за один (shop, date, sku)).
        if "daily_metrics" in tables:
            conn.execute(text(
                "DELETE FROM daily_metrics WHERE id NOT IN "
                "(SELECT MIN(id) FROM daily_metrics GROUP BY shop, date, sku)"
            ))

        if "finance_transactions" in tables:
            conn.execute(text(
                "DELETE FROM finance_transactions WHERE id NOT IN "
                "(SELECT MIN(id) FROM finance_transactions "
                "GROUP BY shop, operation_date, operation_type, operation_type_name, posting_number, amount)"
            ))


def get_session(engine) -> Session:
    return Session(engine)