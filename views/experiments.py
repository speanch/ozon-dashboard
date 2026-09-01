"""Эксперименты — временные метки (начало–конец) для графиков динамики продаж."""
import os
import sys
from datetime import date, datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, Experiment
from etl.config import SHOP_LABELS

st.title("Эксперименты")
st.caption(
    "Временные метки (начало–конец + название) отображаются закрашенными областями на графиках динамики продаж. "
    "Редактируйте прямо в таблице: добавление строки — кнопка «+» внизу, удаление — меню строки (корзина)."
)

engine = init_db()
session = get_session(engine)

experiments = session.query(Experiment).order_by(Experiment.start_date.desc()).all()

label_to_key = {v: k for k, v in SHOP_LABELS.items()}
shop_options = list(SHOP_LABELS.values())


def _shops_to_labels(shop_value):
    if not shop_value:
        return []
    return [SHOP_LABELS.get(s.strip(), s.strip()) for s in str(shop_value).split(",") if s.strip()]


if experiments:
    df = pd.DataFrame([{
        "ID": e.id,
        "Название": e.name,
        "Начало": pd.Timestamp(e.start_date),
        "Конец": pd.Timestamp(e.end_date),
        "Кабинет": _shops_to_labels(e.shop),
        "Артикулы": e.offer_ids or "",
        "Описание": e.description or "",
        "Результат": e.result or "",
    } for e in experiments])
else:
    df = pd.DataFrame({
        "ID": pd.Series(dtype="float64"),
        "Название": pd.Series(dtype="object"),
        "Начало": pd.Series(dtype="datetime64[ns]"),
        "Конец": pd.Series(dtype="datetime64[ns]"),
        "Кабинет": pd.Series(dtype="object"),
        "Артикулы": pd.Series(dtype="object"),
        "Описание": pd.Series(dtype="object"),
        "Результат": pd.Series(dtype="object"),
    })

edited = st.data_editor(
    df,
    num_rows="dynamic",
    width="stretch",
    height=600,
    row_height=110,
    hide_index=True,
    column_config={
        "ID": st.column_config.NumberColumn("ID", disabled=True, width=50),
        "Название": st.column_config.TextColumn("Название", required=True, width=120),
        "Начало": st.column_config.DateColumn("Начало", format="DD.MM.YYYY"),
        "Конец": st.column_config.DateColumn("Конец", format="DD.MM.YYYY"),
        "Кабинет": st.column_config.MultiselectColumn("Кабинет", options=shop_options, width=130),
        "Артикулы": st.column_config.TextColumn(
            "Артикулы", help="Через запятую, например: YKEMG-03112, YFEM-0311", width=150,
        ),
        "Описание": st.column_config.TextColumn("Описание", width=300),
        "Результат": st.column_config.TextColumn("Результат", width=300),
    },
    key="experiments_editor",
)

if st.button("Сохранить изменения", type="primary"):
    def _to_date(v):
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None
        try:
            import numpy as np
            if isinstance(v, np.datetime64):
                return pd.Timestamp(v).date()
        except Exception:
            pass
        return None

    def _parse_shops(v):
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            items = list(v)
        elif hasattr(v, "tolist"):
            items = list(v.tolist())
        else:
            items = [s.strip() for s in str(v).split(",") if s.strip()]
        keys = []
        for x in items:
            lbl = str(x).strip()
            k = label_to_key.get(lbl)
            if k and k not in keys:
                keys.append(k)
        return keys

    errors = []
    parsed = []
    for _, row in edited.iterrows():
        name = str(row["Название"]).strip() if pd.notna(row["Название"]) else ""
        start = _to_date(row["Начало"])
        end = _to_date(row["Конец"])
        desc = str(row["Описание"]).strip() if pd.notna(row["Описание"]) else ""
        result = str(row["Результат"]).strip() if pd.notna(row["Результат"]) else ""
        shop_keys = _parse_shops(row["Кабинет"])
        offer_ids = str(row["Артикулы"]).strip() if pd.notna(row["Артикулы"]) else ""
        rid = row["ID"]

        if not name:
            errors.append("У всех экспериментов должно быть название.")
        elif start is None:
            errors.append(f"«{name}»: укажите дату начала")
        elif end is not None and end < start:
            errors.append(f"«{name}»: дата окончания раньше даты начала.")
        else:
            parsed.append({
                "id": int(rid) if pd.notna(rid) else None,
                "name": name,
                "start": start,
                "end": end,
                "desc": desc,
                "result": result,
                "shop": ",".join(shop_keys) or None,
                "offer_ids": offer_ids,
            })

    if errors:
        for err in errors:
            st.error(err)
    else:
        original_ids = {int(x) for x in df["ID"] if pd.notna(x)}
        kept_ids = {p["id"] for p in parsed if p["id"] is not None}

        to_delete = original_ids - kept_ids
        if to_delete:
            session.query(Experiment).filter(Experiment.id.in_(to_delete)).delete(synchronize_session=False)

        for p in parsed:
            if p["id"] is not None:
                exp = session.get(Experiment, p["id"])
                if exp is not None:
                    exp.name = p["name"]
                    exp.start_date = p["start"]
                    exp.end_date = p["end"]
                    exp.description = p["desc"] or None
                    exp.shop = p["shop"]
                    exp.offer_ids = p["offer_ids"] or None
                    exp.result = p["result"] or None
            else:
                session.add(Experiment(
                    name=p["name"], start_date=p["start"],
                    end_date=p["end"], description=p["desc"] or None,
                    shop=p["shop"], offer_ids=p["offer_ids"] or None,
                    result=p["result"] or None,
                ))

        session.commit()
        st.session_state.pop("experiments_editor", None)
        st.rerun()

session.close()
engine.dispose()
