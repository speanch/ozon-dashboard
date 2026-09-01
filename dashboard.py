"""Точка входа дашборда Ozon — навигация между страницами."""
import streamlit as st

st.set_page_config(page_title="Ozon Seller Dashboard", page_icon="📊", layout="wide")

pg = st.navigation([
    st.Page("views/overview.py", title="Главная", icon="📊", default=True),
    st.Page("views/dynamics.py", title="Динамика продаж по кабинетам", icon="📈"),
    st.Page("views/sku_dynamics.py", title="Динамика продаж по артикулам", icon="📦"),
    st.Page("views/boost.py", title="Бустинг 75%", icon="🚀"),
    st.Page("views/strategy.py", title="Стратегия рекламы", icon="🎯"),
    st.Page("views/price_index.py", title="Ценовой индекс", icon="💲"),
    st.Page("views/experiments.py", title="Эксперименты", icon="🧪"),
])
pg.run()
