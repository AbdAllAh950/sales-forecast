"""Business numbers: period comparison, months, ABC analysis, weekdays."""
from __future__ import annotations

import pandas as pd

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def kpis(sales: pd.DataFrame, days: int = 30) -> dict:
    """Last `days` days vs the `days` before them, and vs the same days a year ago if the data reaches back."""
    end = sales["date"].max()
    cur = sales[sales["date"] > end - pd.Timedelta(days=days)]
    prev = sales[(sales["date"] <= end - pd.Timedelta(days=days)) & (sales["date"] > end - pd.Timedelta(days=2 * days))]
    yago_end = end - pd.DateOffset(years=1)
    yago = sales[(sales["date"] <= yago_end) & (sales["date"] > yago_end - pd.Timedelta(days=days))]
    by_product = cur.groupby("product")["revenue"].sum().sort_values(ascending=False)

    def change(a: float, b: float) -> float | None:
        return round((a / b - 1) * 100, 1) if b else None

    revenue, prev_revenue = float(cur["revenue"].sum()), float(prev["revenue"].sum())
    return {
        "days": days,
        "start": (end - pd.Timedelta(days=days - 1)).date(),
        "end": end.date(),
        "revenue": revenue,
        "change_pct": change(revenue, prev_revenue),
        "yoy_pct": change(revenue, float(yago["revenue"].sum())) if len(yago) else None,
        "per_day": revenue / days,
        "units": float(cur["qty"].sum()) if cur["qty"].notna().any() else None,
        "top_product": by_product.index[0] if len(by_product) else None,
        "top_share_pct": round(by_product.iloc[0] / revenue * 100, 1) if revenue and len(by_product) else None,
    }


def monthly(sales: pd.DataFrame) -> pd.DataFrame:
    m = sales.groupby(sales["date"].dt.to_period("M"))["revenue"].sum().reset_index()
    m["label"] = m["date"].map(lambda p: f"{MONTHS_RU[p.month - 1]} {str(p.year)[2:]}")
    m["partial"] = False
    if len(m):  # the last month is partial if data stops before its end
        last_day = sales["date"].max()
        m.loc[m.index[-1], "partial"] = last_day < last_day.to_period("M").end_time.normalize()
    return m


def abc(sales: pd.DataFrame, a: float = 80, b: float = 95) -> pd.DataFrame:
    """ABC analysis by revenue: A = products giving the first 80%, B = next 15%, C = the rest."""
    g = sales.groupby("product").agg(revenue=("revenue", "sum"), qty=("qty", "sum")).sort_values("revenue", ascending=False)
    g["share_pct"] = g["revenue"] / g["revenue"].sum() * 100
    g["cum_pct"] = g["share_pct"].cumsum()
    prev_cum = g["cum_pct"] - g["share_pct"]
    g["class"] = ["A" if p < a else "B" if p < b else "C" for p in prev_cum]
    return g.round({"share_pct": 1, "cum_pct": 1}).reset_index()


def weekday_profile(daily_series: pd.Series) -> pd.DataFrame:
    df = daily_series.to_frame("value")
    df["wd"] = df.index.dayofweek
    out = df.groupby("wd")["value"].mean().reindex(range(7)).reset_index()
    out["weekday"] = [WEEKDAYS_RU[i] for i in out["wd"]]
    out["vs_avg_pct"] = (out["value"] / out["value"].mean() - 1) * 100
    return out
