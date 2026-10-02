"""Plain-language summary for the owner: a template that always works, and an optional LLM version."""
from __future__ import annotations

import json

import httpx
import pandas as pd

from .analytics import WEEKDAYS_RU
from .model import Forecast

WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def rub(x: float) -> str:
    """1 234 ₽, 45,6 тыс. ₽, 1,34 млн ₽ — the way people read money in Russian reports."""
    if abs(x) >= 1_000_000:
        return f"{x / 1_000_000:.2f}".replace(".", ",") + " млн ₽"
    if abs(x) >= 10_000:
        return f"{x / 1000:.1f}".replace(".", ",") + " тыс. ₽"
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def pct(x: float) -> str:
    return f"{abs(x):.0f}%"


def day_range(start, end) -> str:
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GEN[end.month - 1]}"
    return f"{start.day} {MONTHS_GEN[start.month - 1]} – {end.day} {MONTHS_GEN[end.month - 1]}"


def facts(k: dict, fc: Forecast, abc_df: pd.DataFrame, weekdays: pd.DataFrame, product: str | None) -> dict:
    best = weekdays.loc[weekdays["vs_avg_pct"].idxmax()]
    worst = weekdays.loc[weekdays["vs_avg_pct"].idxmin()]
    a = abc_df[abc_df["class"] == "A"]
    return {
        "scope": product or "все товары",
        "period": day_range(k["start"], k["end"]),
        "days": k["days"],
        "revenue": round(k["revenue"]),
        "change_vs_previous_pct": k["change_pct"],
        "change_vs_last_year_pct": k["yoy_pct"],
        "forecast_days": len(fc.table),
        "forecast_total": round(fc.total),
        "forecast_low": round(fc.total_low),
        "forecast_high": round(fc.total_high),
        "forecast_vs_last_period_pct": round((fc.total / (k["per_day"] * len(fc.table)) - 1) * 100, 1) if k["per_day"] else None,
        "interval_level_pct": round(fc.level * 100),
        "top_product": k["top_product"],
        "top_share_pct": k["top_share_pct"],
        "a_class_products": a["product"].tolist(),
        "products_total": len(abc_df),
        "best_weekday": WEEKDAYS_FULL[WEEKDAYS_RU.index(best["weekday"])],
        "best_weekday_pct": round(best["vs_avg_pct"], 1),
        "worst_weekday": WEEKDAYS_FULL[WEEKDAYS_RU.index(worst["weekday"])],
        "worst_weekday_pct": round(worst["vs_avg_pct"], 1),
        "accuracy": fc.accuracy,
    }


def template(f: dict) -> list[str]:
    lines = []
    s = f"За {f['period']} выручка — **{rub(f['revenue'])}**"
    ch = f["change_vs_previous_pct"]
    if ch is not None:
        s += f", на {pct(ch)} {'больше' if ch >= 0 else 'меньше'}, чем за предыдущие {f['days']} дней"
    if f["change_vs_last_year_pct"] is not None:
        y = f["change_vs_last_year_pct"]
        s += f" и на {pct(y)} {'больше' if y >= 0 else 'меньше'}, чем год назад"
    lines.append(s + ".")

    s = (f"Прогноз на следующие {f['forecast_days']} дней — около **{rub(f['forecast_total'])}** "
         f"(с вероятностью {f['interval_level_pct']}%: от {rub(f['forecast_low'])} до {rub(f['forecast_high'])})")
    d = f["forecast_vs_last_period_pct"]
    if d is not None and abs(d) >= 3:
        s += f" — это на {pct(d)} {'выше' if d > 0 else 'ниже'} текущего темпа продаж"
    lines.append(s + ".")

    if f["top_product"] and f["products_total"] > 1 and f["scope"] == "все товары":
        n_a = len(f["a_class_products"])
        lines.append(f"Главный товар — «{f['top_product']}»: {pct(f['top_share_pct'])} выручки. "
                     f"80% выручки дают {n_a} из {f['products_total']} товаров (группа A) — их наличие важнее всего.")

    lines.append(f"Сильнее всего продажи в {_prep(f['best_weekday'])} (+{pct(f['best_weekday_pct'])} к среднему), "
                 f"слабее всего — в {_prep(f['worst_weekday'])} (−{pct(f['worst_weekday_pct'])}). "
                 f"Акции и рассылки лучше запускать накануне сильных дней.")

    acc = f["accuracy"]
    if acc:
        better = acc["daily_wape"] < acc["daily_wape_naive"]
        lines.append(f"Проверка на прошлых данных: прогноз по дням ошибался в среднем на {pct(acc['daily_wape'])}"
                     + (f" — точнее, чем простое «как на прошлой неделе» ({pct(acc['daily_wape_naive'])})." if better
                        else f" (простое «как на прошлой неделе» — {pct(acc['daily_wape_naive'])})."))
    return lines


def _prep(weekday: str) -> str:
    """'в понедельник', 'в среду', 'в пятницу' — accusative after 'в'."""
    return {"среда": "среду", "пятница": "пятницу", "суббота": "субботу"}.get(weekday, weekday)


AI_PROMPT = (
    "Ты — аналитик продаж малого бизнеса. По фактам в JSON напиши владельцу 4–5 коротких пунктов по-русски: "
    "что происходит с продажами, чего ждать дальше и что конкретно сделать на этой неделе. "
    "Используй только числа из JSON, ничего не выдумывай, суммы пиши в рублях с разделителями. "
    "Без вступления и заключения, каждый пункт с новой строки, начинай с «• »."
)


def ai_summary(f: dict, url: str, auth: str, model: str, client: httpx.Client | None = None) -> str:
    """OpenAI-compatible chat completion; for YandexGPT: https://ai.api.cloud.yandex.net/v1/chat/completions,
    auth "Api-Key <key>", model "gpt://<folder_id>/yandexgpt/latest"."""
    client = client or httpx.Client(timeout=30)
    r = client.post(url, headers={"Authorization": auth}, json={
        "model": model, "temperature": 0.3, "max_tokens": 600,
        "messages": [{"role": "system", "content": AI_PROMPT},
                     {"role": "user", "content": json.dumps(f, ensure_ascii=False, default=str)}],
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()
