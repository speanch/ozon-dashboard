# Ozon Dashboard

Streamlit-дашборд для Ozon Seller: обзор заказов, динамика продаж, аналитика по SKU и «Эластичный бустинг».

## Структура

- `dashboard.py` — главная точка входа Streamlit
- `run_etl.py` — запуск ETL-выгрузки
- `etl/` — клиенты API Ozon, pipeline, модели, конфигурация
- `views/` — страницы дашборда
- `fetch_boost_api.py` — выгрузка данных бустинга

## Настройка

Скопируйте `.env.example` в `.env` и заполните:

- `OZON_STYLINT_CLIENT_ID`, `OZON_STYLINT_API_KEY`
- `OZON_RS_CLIENT_ID`, `OZON_RS_API_KEY`
- `PERFORMANCE_*` — ключи Performance API для рекламной статистики

## Запуск

```
pip install -r requirements.txt
streamlit run dashboard.py
```

## Примечание о конфиденциальности

Локальная БД (`dashboard.db`) и снапшоты выгрузок (`snapshots/`) не хранятся в репозитории.
