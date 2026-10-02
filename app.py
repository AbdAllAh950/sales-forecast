"""Анализ и прогноз продаж: загрузите выгрузку из Excel / 1С / МойСклад — получите динамику, ABC и прогноз.
Run: streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from forecast.analytics import abc, kpis, monthly, weekday_profile
from forecast.data import Columns, daily, detect_columns, prepare, read_table
from forecast.model import make_forecast
from forecast.summary import ai_summary, facts, rub, template

ROOT = Path(__file__).parent
DEMO = ROOT / "data" / "demo_sales.csv"
ACCENT, MUTED, INK, GRID = "#2a78d6", "#b9b8b2", "#52514e", "#e6e5e1"
CLASS_COLORS = {"A": ACCENT, "B": "#8cb6e8", "C": MUTED}

st.set_page_config(page_title="Анализ и прогноз продаж", page_icon="📈", layout="wide")


def setting(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, os.getenv(name, default)))
    except Exception:  # no secrets.toml
        return os.getenv(name, default)


@st.cache_data(show_spinner=False)
def load(content: bytes, name: str) -> pd.DataFrame:
    return read_table(content, name)


@st.cache_data(show_spinner="Считаю прогноз…")
def cached_forecast(series: pd.Series, horizon: int):
    return make_forecast(series, horizon)


def style(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(height=height, margin={"l": 8, "r": 8, "t": 16, "b": 8}, showlegend=False,
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      font={"color": INK}, hoverlabel={"font_size": 13}, separators=", ")
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


# ---------- sidebar: data ----------
with st.sidebar:
    st.header("Данные")
    upload = st.file_uploader("Выгрузка продаж (CSV или Excel)", type=["csv", "xlsx", "xls"],
                              help="Нужны хотя бы дата и сумма продажи. Подойдёт выгрузка из 1С, МойСклад, "
                                   "маркетплейса или кассы.")
    if upload is not None:
        raw = load(upload.getvalue(), upload.name)
        source = upload.name
    else:
        raw = load(DEMO.read_bytes(), DEMO.name)
        source = "демо: интернет-магазин мёда"
        st.caption("Сейчас открыт демо-файл. Загрузите свой — колонки найдутся автоматически.")

    try:
        guess = detect_columns(raw)
    except ValueError:
        guess = Columns(date=raw.columns[0], revenue=raw.columns[-1])
    names = list(raw.columns)
    none = "— нет —"
    with st.expander("Колонки", expanded=upload is not None):
        c_date = st.selectbox("Дата", names, index=names.index(guess.date))
        c_rev = st.selectbox("Сумма продажи", names, index=names.index(guess.revenue))
        opts = [none] + names
        c_prod = st.selectbox("Товар", opts, index=opts.index(guess.product) if guess.product else 0)
        c_qty = st.selectbox("Количество", opts, index=opts.index(guess.qty) if guess.qty else 0)
        c_cat = st.selectbox("Категория", opts, index=opts.index(guess.category) if guess.category else 0)
    cols = Columns(c_date, c_rev, None if c_prod == none else c_prod, None if c_qty == none else c_qty,
                   None if c_cat == none else c_cat)
    sales = prepare(raw, cols)
    if sales.empty:
        st.error("В выбранных колонках нет дат и сумм — проверьте выбор колонок.")
        st.stop()

    st.header("Прогноз")
    horizon = st.slider("На сколько дней вперёд", 7, 90, 30, step=1)
    products = ["Все товары"] + sorted(sales["product"].unique().tolist())
    product = st.selectbox("Товар", products) if len(products) > 2 else "Все товары"
    scope = None if product == "Все товары" else product

# ---------- numbers ----------
series = daily(sales, product=scope)
period = min(30, max(len(series) // 2, 1))
scoped = sales if scope is None else sales[sales["product"] == scope]
k = kpis(scoped, period)
fc = cached_forecast(series, horizon)
abc_df = abc(sales)
wd = weekday_profile(series)
f = facts(k, fc, abc_df, wd, scope)

# ---------- header & KPIs ----------
st.title("📈 Анализ и прогноз продаж")
st.caption(f"{source} · {sales['date'].min():%d.%m.%Y} – {sales['date'].max():%d.%m.%Y} · "
           f"{len(sales):,} строк".replace(",", " ") + (f" · товар: {scope}" if scope else ""))

m1, m2, m3, m4 = st.columns(4)
m1.metric(f"Выручка за {period} дн.", rub(k["revenue"]),
          f"{k['change_pct']:+.0f}% к пред. {period} дн." if k["change_pct"] is not None else None)
m2.metric("В среднем в день", rub(k["per_day"]),
          f"{k['yoy_pct']:+.0f}% к прошлому году" if k["yoy_pct"] is not None else None)
m3.metric(f"Прогноз на {horizon} дн.", rub(fc.total),
          f"{f['forecast_vs_last_period_pct']:+.0f}% к текущему темпу" if f["forecast_vs_last_period_pct"] is not None else None,
          help=f"С вероятностью {round(fc.level * 100)}%: от {rub(fc.total_low)} до {rub(fc.total_high)}")
if fc.accuracy:
    m4.metric("Ошибка прогноза по дням", f"{fc.accuracy['daily_wape']:.0f}%",
              f"{fc.accuracy['daily_wape'] - fc.accuracy['daily_wape_naive']:+.0f} п.п. к «как на прошлой неделе»",
              delta_color="inverse",
              help="WAPE на прошлых периодах: модель «прогнозировала» уже известные дни, и ошибку сравнили с фактом.")
else:
    m4.metric("Ошибка прогноза", "—", help="Нужно больше истории, чтобы проверить прогноз на прошлых данных")

tab_fc, tab_trend, tab_abc, tab_wd, tab_data = st.tabs(["Прогноз", "Динамика", "Товары (ABC)", "Дни недели", "Данные"])

# ---------- forecast ----------
with tab_fc:
    left, right = st.columns([3, 2], gap="large")
    with left:
        hist = series.iloc[-120:]
        t = fc.table
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=list(t["date"]) + list(t["date"][::-1]), y=list(t["upper"]) + list(t["lower"][::-1]),
                                 fill="toself", fillcolor="rgba(42,120,214,0.15)", line={"width": 0},
                                 hoverinfo="skip", name="интервал"))
        fig.add_trace(go.Scatter(x=hist.index, y=hist.values, line={"color": INK, "width": 1.5}, name="факт",
                                 hovertemplate="%{x|%d.%m.%Y}<br>факт: %{y:,.0f} ₽<extra></extra>"))
        fig.add_trace(go.Scatter(x=hist.index, y=hist.rolling(7).mean(), line={"color": INK, "width": 2.5},
                                 opacity=0.35, name="среднее за 7 дней", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=t["date"], y=t["forecast"], line={"color": ACCENT, "width": 2.5}, name="прогноз",
                                 hovertemplate="%{x|%d.%m.%Y}<br>прогноз: %{y:,.0f} ₽<extra></extra>"))
        fig.add_vline(x=series.index.max(), line={"color": MUTED, "width": 1, "dash": "dot"})
        fig.update_xaxes(tickformat="%d.%m")
        fig.update_yaxes(tickformat=",.0f", title_text="₽ в день")
        st.markdown(f"**Выручка по дням: последние 120 дней и прогноз на {horizon}**")
        st.plotly_chart(style(fig, 380), width="stretch", config={"displayModeBar": False})
        st.caption(f"Синим — прогноз, полоса — {round(fc.level * 100)}% интервал. Серая линия — факт, "
                   f"бледная — среднее за 7 дней. Метод: {fc.method}.")
    with right:
        st.markdown("**Коротко о главном**")
        st.markdown("\n".join(f"- {line}" for line in template(f)))
        url, auth, model = setting("LLM_URL"), setting("LLM_AUTH"), setting("LLM_MODEL")
        if url and auth and model:
            if st.button("✨ Сводка и советы от YandexGPT"):
                with st.spinner("Пишу сводку…"):
                    try:
                        st.markdown(ai_summary(f, url, auth, model))
                    except Exception as e:  # show, don't crash the dashboard
                        st.warning(f"Нейросеть не ответила: {e}")
        else:
            st.caption("Сводку с советами от YandexGPT можно включить в `.streamlit/secrets.toml` (см. README).")
        out = fc.table.round({"forecast": 0, "lower": 0, "upper": 0}).assign(date=fc.table["date"].dt.date)
        out = out.rename(columns={"date": "Дата", "forecast": "Прогноз, ₽", "lower": "Нижняя граница, ₽",
                                  "upper": "Верхняя граница, ₽"})
        st.download_button("Скачать прогноз (CSV)", out.to_csv(index=False, sep=";").encode("utf-8-sig"),
                           file_name=f"прогноз_{horizon}_дней.csv", mime="text/csv")
        if fc.accuracy:
            a = fc.accuracy
            with st.expander("Насколько можно доверять прогнозу"):
                st.markdown(
                    f"Модель {a['windows']} раза «прогнозировала» по {a['days']} дней, которые уже прошли, "
                    f"и прогноз сравнили с фактом.\n\n"
                    f"| | Модель | «Как на прошлой неделе» |\n|---|---|---|\n"
                    f"| Ошибка по дням (WAPE) | {a['daily_wape']}% | {a['daily_wape_naive']}% |\n"
                    f"| Ошибка суммы за период | {a['total_err']}% | {a['total_err_naive']}% |\n\n"
                    "Праздники и акции модель заранее не знает: перед Новым годом, 8 Марта и распродажами "
                    "закладывайте запас сверху.")

# ---------- trend ----------
with tab_trend:
    mon = monthly(scoped)
    best = mon.loc[mon.loc[~mon["partial"], "revenue"].idxmax()] if (~mon["partial"]).any() else mon.iloc[-1]
    colors = [ACCENT if lbl == best["label"] else MUTED for lbl in mon["label"]]
    fig = go.Figure(go.Bar(x=mon["label"], y=mon["revenue"] / 1e6, marker_color=colors, marker_cornerradius=4,
                           marker_opacity=[0.45 if p else 1 for p in mon["partial"]],
                           hovertemplate="%{x}: %{y:,.2f} млн ₽<extra></extra>"))
    fig.update_yaxes(tickformat=",.1f", title_text="млн ₽")
    st.markdown(f"**Выручка по месяцам — лучший месяц: {best['label']} ({rub(best['revenue'])})**")
    st.plotly_chart(style(fig), width="stretch", config={"displayModeBar": False})
    if mon["partial"].any():
        st.caption("Бледный столбец — неполный месяц.")

# ---------- ABC ----------
with tab_abc:
    if abc_df["product"].nunique() < 2:
        st.info("Для ABC-анализа нужна колонка с товарами.")
    else:
        n_a = int((abc_df["class"] == "A").sum())
        st.markdown(f"**{n_a} из {len(abc_df)} товаров дают 80% выручки (группа A)** — за весь период")
        d = abc_df.sort_values("revenue")
        fig = go.Figure(go.Bar(x=d["share_pct"], y=d["product"], orientation="h", marker_cornerradius=4,
                               marker_color=[CLASS_COLORS[c] for c in d["class"]],
                               text=[f"{c} · {s:.0f}%" for c, s in zip(d["class"], d["share_pct"])],
                               textposition="outside", cliponaxis=False,
                               hovertemplate="%{y}<br>%{x:.1f}% выручки<extra></extra>"))
        fig.update_xaxes(title_text="доля выручки, %", range=[0, d["share_pct"].max() * 1.2])
        st.plotly_chart(style(fig, 60 + 44 * len(d)), width="stretch", config={"displayModeBar": False})
        st.caption("A — первые 80% выручки, B — следующие 15%, C — остальное. "
                   "A держите всегда в наличии, C — кандидаты на вывод или пересмотр цены.")
        show = abc_df.rename(columns={"product": "Товар", "revenue": "Выручка, ₽", "qty": "Продано, шт",
                                      "share_pct": "Доля, %", "cum_pct": "Накопленная доля, %", "class": "Группа"})
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "Выручка, ₽": st.column_config.NumberColumn(format="localized"),
            "Продано, шт": st.column_config.NumberColumn(format="localized"),
            "Доля, %": st.column_config.NumberColumn(format="localized"),
            "Накопленная доля, %": st.column_config.NumberColumn(format="localized")})

# ---------- weekdays ----------
with tab_wd:
    best_wd = wd.loc[wd["vs_avg_pct"].idxmax()]
    fig = go.Figure(go.Bar(x=wd["weekday"], y=wd["value"], marker_cornerradius=4,
                           marker_color=[ACCENT if w == best_wd["weekday"] else MUTED for w in wd["weekday"]],
                           customdata=wd["vs_avg_pct"],
                           hovertemplate="%{x}: %{y:,.0f} ₽ в среднем<br>%{customdata:+.0f}% к среднему<extra></extra>"))
    fig.update_yaxes(tickformat=",.0f", title_text="₽ в среднем за день")
    st.markdown(f"**Сильнее всего продажи в {f['best_weekday']} — на {f['best_weekday_pct']:.0f}% выше среднего**")
    st.plotly_chart(style(fig), width="stretch", config={"displayModeBar": False})

# ---------- data ----------
with tab_data:
    st.markdown(f"Найденные колонки: дата — `{cols.date}`, сумма — `{cols.revenue}`"
                + (f", товар — `{cols.product}`" if cols.product else "")
                + (f", количество — `{cols.qty}`" if cols.qty else ""))
    st.dataframe(raw.head(200), hide_index=True, width="stretch")
