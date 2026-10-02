import io
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from forecast.analytics import abc, kpis, monthly, weekday_profile
from forecast.data import Columns, daily, detect_columns, prepare, read_table, to_number
from forecast.model import make_forecast, seasonal_naive, wape
from forecast.summary import ai_summary, facts, rub, template

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo_sales.csv"


@pytest.fixture(scope="module")
def sales():
    raw = read_table(DEMO.read_bytes(), DEMO.name)
    return prepare(raw, detect_columns(raw))


# ---------- reading real exports ----------
def test_russian_csv_semicolon_cp1251_and_number_formats():
    text = "Дата;Номенклатура;Сумма, руб.\n01.03.2026;Мёд липовый;1 234,50\n02.03.2026;Мёд липовый; 990,00\n"
    df = read_table(text.encode("cp1251"), "1c_export.csv")
    cols = detect_columns(df)
    assert (cols.date, cols.product, cols.revenue) == ("Дата", "Номенклатура", "Сумма, руб.")
    s = prepare(df, cols)
    assert s["revenue"].tolist() == [1234.5, 990.0]
    assert s["date"].dt.day.tolist() == [1, 2] and (s["date"].dt.month == 3).all()  # dd.mm, not mm.dd


def test_english_headers_and_excel(tmp_path):
    df = pd.DataFrame({"Order date": ["2026-01-05", "2026-01-06"], "SKU": ["A", "B"],
                       "Qty": [2, 1], "Sales": [200.0, 150.0]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    raw = read_table(buf.getvalue(), "export.xlsx")
    cols = detect_columns(raw)
    assert (cols.date, cols.product, cols.qty, cols.revenue) == ("Order date", "SKU", "Qty", "Sales")


def test_detects_unnamed_columns_by_content():
    df = pd.DataFrame({"c1": ["01.02.2026", "02.02.2026", "03.02.2026"], "c2": ["x", "y", "z"], "c3": [10, 20, 30]})
    cols = detect_columns(df)
    assert cols.date == "c1" and cols.revenue == "c3"


def test_to_number_handles_rubles_and_spaces():
    assert to_number(pd.Series(["1 234 567,8 ₽", "12,5", "abc"])).tolist()[:2] == [1234567.8, 12.5]


def test_daily_fills_missing_days_with_zero():
    s = prepare(pd.DataFrame({"d": ["2026-01-01", "2026-01-03"], "v": [5, 7]}), Columns("d", "v"))
    assert daily(s).tolist() == [5.0, 0.0, 7.0]


# ---------- analytics ----------
def test_abc_classes():
    s = pd.DataFrame({"date": pd.Timestamp("2026-01-01"), "product": list("ABCDE"),
                      "revenue": [70, 15, 8, 5, 2], "qty": 1, "category": "-"})
    out = abc(s).set_index("product")["class"].to_dict()
    assert out == {"A": "A", "B": "A", "C": "B", "D": "B", "E": "C"}  # class by where the product starts


def test_kpis_compare_periods(sales):
    k = kpis(sales, 30)
    last30 = sales[sales["date"] > sales["date"].max() - pd.Timedelta(days=30)]["revenue"].sum()
    assert k["revenue"] == pytest.approx(last30)
    assert k["change_pct"] is not None and k["yoy_pct"] is not None
    assert k["top_product"] == "Таёжное разнотравье 500 г"


def test_monthly_and_weekdays(sales):
    m = monthly(sales)
    assert len(m) == 13 and m["label"].iloc[3] == "дек 25" and not m["partial"].iloc[-1]
    wd = weekday_profile(daily(sales))
    assert wd.loc[wd["vs_avg_pct"].idxmax(), "weekday"] == "Пн"  # demo data peaks on Mondays


# ---------- forecast ----------
def test_forecast_shape_and_interval(sales):
    fc = make_forecast(daily(sales), 30)
    t = fc.table
    assert len(t) == 30 and t["date"].iloc[0] == pd.Timestamp("2026-10-01")
    assert (t["lower"] <= t["forecast"]).all() and (t["forecast"] <= t["upper"]).all() and (t["lower"] >= 0).all()
    assert fc.total_low < fc.total < fc.total_high


def test_model_beats_naive_on_daily_error(sales):
    for series in [daily(sales)] + [daily(sales, product=p) for p in sales["product"].unique()]:
        acc = make_forecast(series, 30).accuracy
        assert acc["daily_wape"] < acc["daily_wape_naive"]


def test_short_history_falls_back():
    idx = pd.date_range("2026-01-01", periods=20)
    fc = make_forecast(pd.Series(np.arange(20, dtype=float) + 10, index=idx), 7)
    assert len(fc.table) == 7 and fc.accuracy is None and "профиль" in fc.method


def test_wape_and_naive():
    assert wape(np.array([10, 10]), np.array([5, 15])) == 50
    s = pd.Series(range(14), index=pd.date_range("2026-01-01", periods=14), dtype=float)
    assert seasonal_naive(s, 9).tolist() == [7, 8, 9, 10, 11, 12, 13, 7, 8]


# ---------- summary ----------
def test_rub_format():
    assert rub(1_337_420) == "1,34 млн ₽" and rub(44_581) == "44,6 тыс. ₽" and rub(950) == "950 ₽"


def test_template_summary(sales):
    d = daily(sales)
    fc = make_forecast(d, 30)
    f = facts(kpis(sales, 30), fc, abc(sales), weekday_profile(d), None)
    text = "\n".join(template(f))
    assert "За 1–30 сентября выручка — **1,34 млн ₽**" in text
    assert "в понедельник" in text and "в субботу" in text
    assert "«Таёжное разнотравье 500 г»" in text and "4 из 6 товаров" in text


def test_ai_summary_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "• Продажи растут"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = ai_summary({"revenue": 1}, "https://llm.test/v1/chat/completions", "Api-Key k", "gpt://f/yandexgpt/latest", client)
    assert out == "• Продажи растут" and seen[0].headers["Authorization"] == "Api-Key k"


# ---------- the Streamlit app end to end ----------
def test_app_runs_with_demo_and_reacts_to_controls():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180).run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels == ["Выручка за 30 дн.", "В среднем в день", "Прогноз на 30 дн.", "Ошибка прогноза по дням"]
    assert at.metric[0].value == "1,34 млн ₽"

    at.slider[0].set_value(14).run()
    assert not at.exception and at.metric[2].label == "Прогноз на 14 дн."

    at.sidebar.selectbox[-1].set_value("Липовый 500 г").run()
    assert not at.exception and "Липовый" in at.caption[0].value
