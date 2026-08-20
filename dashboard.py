"""Точка входа дашборда Ozon — навигация между страницами."""
import streamlit as st

st.set_page_config(page_title="Ozon Seller Dashboard", page_icon="📊", layout="wide")

pg = st.navigation([
    st.Page("views/overview.py", title="Главная", icon="📊", default=True),
    st.Page("views/dynamics.py", title="Динамика по кабинетам", icon="📈"),
    st.Page("views/sku.py", title="Аналитика по артикулу", icon="🔍"),
    st.Page("views/boost.py", title="Бустинг 75%", icon="🚀"),
])
pg.run()
