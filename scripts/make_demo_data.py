"""Generates data/demo_sales.csv: 13 months of daily sales for a small online honey shop (fictional)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PRODUCTS = [  # name, category, base units/day, price ₽
    ("Таёжное разнотравье 500 г", "Мёд", 14, 890),
    ("Липовый 500 г", "Мёд", 11, 790),
    ("Гречишный 500 г", "Мёд", 7, 750),
    ("Подарочный набор 3×250 г", "Подарки", 4, 1490),
    ("Пробник 50 г", "Пробники", 6, 190),
    ("Мёд в сотах 300 г", "Мёд", 2, 1250),
]
WEEKDAY = np.array([1.15, 1.05, 1.0, 0.97, 0.95, 0.85, 1.03])  # Mon..Sun: online orders peak on Monday


def make(start="2025-09-01", end="2026-09-30", seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    t = np.arange(len(days))
    season = 1 + 0.28 * np.cos(2 * np.pi * (days.dayofyear.to_numpy() - 15) / 365.25)  # winter high, summer low
    trend = 1 + 0.22 * t / len(days)
    rows = []
    for name, cat, base, price in PRODUCTS:
        mult = season * trend * WEEKDAY[days.dayofweek]
        boost = np.ones(len(days))
        md = days.strftime("%m-%d")
        if cat == "Подарки":
            boost[(md >= "12-10") & (md <= "12-30")] = 5.0        # New Year gifts
            boost[(md >= "03-01") & (md <= "03-07")] = 3.0        # 8 March
            boost[(md >= "02-08") & (md <= "02-13")] = 1.8        # 14 February
            boost[(md >= "07-01") & (md <= "08-31")] *= 0.6
        else:
            boost[(md >= "12-15") & (md <= "12-30")] = 1.5
            boost[(md >= "01-01") & (md <= "01-08")] = 0.55        # New Year holidays: few orders
        bf = (days.month == 11) & (days.day >= 24) & (days.day <= 30)  # Black Friday week
        boost[bf] *= 1.6
        if name.startswith("Мёд в сотах"):
            boost[(days.month >= 8) & (days.month <= 10)] *= 2.2  # fresh comb season
        units = rng.poisson(base * mult * boost)
        unit_price = np.where(days >= "2026-03-01", round(price * 1.07, -1), price)  # price rise in March
        for d, u, p in zip(days, units, unit_price):
            if u:
                rows.append((d.date().isoformat(), name, cat, int(u), int(u * p)))
    return pd.DataFrame(rows, columns=["Дата", "Товар", "Категория", "Количество", "Выручка"])


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "demo_sales.csv"
    df = make()
    df.to_csv(out, index=False, sep=";", encoding="utf-8-sig")   # like an Excel/1C export in Russia
    print(f"{len(df)} rows → {out}")
