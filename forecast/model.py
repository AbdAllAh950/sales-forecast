"""Daily sales forecast.

Point forecast = average of two simple models that fail in different ways:
  • ETS / Holt–Winters with weekly seasonality — follows the current level quickly;
  • the average of each weekday over the last 4 weeks — steady, not thrown by one odd day.
On rolling backtests over the demo data this average beat each model alone and the naive "same weekday last week"
baseline (see README). The interval comes from simulating the ETS model, centred on the averaged forecast.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

METHOD = "среднее двух моделей: Хольт–Винтерс (ETS) + профиль дней недели за 4 недели"


@dataclass
class Forecast:
    table: pd.DataFrame          # date, forecast, lower, upper (daily, interval = `level`)
    total: float                 # expected sum over the horizon
    total_low: float             # interval for the SUM (simulated, not the sum of daily bounds)
    total_high: float
    method: str
    level: float
    accuracy: dict | None        # backtest on past windows (see `backtest`), None if history is too short


def wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denom = np.abs(actual).sum()
    return float(np.abs(actual - predicted).sum() / denom * 100) if denom else float("nan")


def future_dates(history: pd.Series, horizon: int) -> pd.DatetimeIndex:
    return pd.date_range(history.index.max() + pd.Timedelta(days=1), periods=horizon, freq="D")


def seasonal_naive(history: pd.Series, horizon: int) -> np.ndarray:
    """Baseline: every future day = the same weekday last week."""
    return np.resize(history.iloc[-7:].to_numpy(), horizon)


def weekday_profile(history: pd.Series, horizon: int, weeks: int = 4) -> np.ndarray:
    recent = history.iloc[-7 * weeks:]
    profile = recent.groupby(recent.index.dayofweek).mean()
    out = profile.reindex(future_dates(history, horizon).dayofweek).to_numpy()
    return np.nan_to_num(out, nan=float(recent.mean()))


def _fit_ets(history: pd.Series):
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ETSModel(history.astype(float), error="add", trend=None, seasonal="add", seasonal_periods=7)
        return model.fit(disp=False, maxiter=200)


def predict(history: pd.Series, horizon: int):
    """Averaged point forecast plus the fitted ETS model (used for intervals)."""
    ets = _fit_ets(history)
    point = (ets.forecast(horizon).to_numpy() + weekday_profile(history, horizon)) / 2
    return np.clip(point, 0, None), ets


def backtest(series: pd.Series, horizon: int, origins: int = 4) -> dict | None:
    """Pretend we forecast `horizon` days from each of the last `origins` cut-off points and compare with what
    really happened. Several windows, so one unusual month does not decide the score."""
    h = min(horizon, 30)
    if len(series) < 8 * 7 + h * origins:
        return None
    daily_err, daily_naive, total_err, total_naive = [], [], [], []
    for o in range(origins):
        end = len(series) - o * h
        train, test = series.iloc[: end - h], series.iloc[end - h: end].to_numpy()
        pred, _ = predict(train, h)
        naive = seasonal_naive(train, h)
        daily_err.append(wape(test, pred))
        daily_naive.append(wape(test, naive))
        if test.sum():
            total_err.append(abs(pred.sum() / test.sum() - 1) * 100)
            total_naive.append(abs(naive.sum() / test.sum() - 1) * 100)
    return {
        "days": h, "windows": origins,
        "daily_wape": round(float(np.mean(daily_err)), 1),
        "daily_wape_naive": round(float(np.mean(daily_naive)), 1),
        "total_err": round(float(np.mean(total_err)), 1) if total_err else None,
        "total_err_naive": round(float(np.mean(total_naive)), 1) if total_naive else None,
    }


def make_forecast(series: pd.Series, horizon: int = 30, level: float = 0.8, seed: int = 0) -> Forecast:
    series = series.astype(float)
    future = future_dates(series, horizon)
    alpha = 1 - level

    if len(series) < 8 * 7:  # under 8 weeks: too little for a model, use the weekday profile only
        base = weekday_profile(series, horizon)
        spread = float(series.iloc[-28:].std() or 0) * 1.28
        table = pd.DataFrame({"date": future, "forecast": base, "lower": np.clip(base - spread, 0, None),
                              "upper": base + spread})
        total = float(base.sum())
        margin = spread * np.sqrt(horizon)
        return Forecast(table, total, max(total - margin, 0), total + margin,
                        "профиль дней недели (мало данных для модели)", level, None)

    point, ets = predict(series, horizon)
    pred = ets.get_prediction(start=len(series), end=len(series) + horizon - 1).summary_frame(alpha=alpha)
    mean = pred["mean"].to_numpy()
    table = pd.DataFrame({
        "date": future,
        "forecast": point,
        "lower": np.clip(point - (mean - pred["pi_lower"].to_numpy()), 0, None),
        "upper": point + (pred["pi_upper"].to_numpy() - mean),
    })
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        paths = np.asarray(ets.simulate(nsimulations=horizon, repetitions=1000, anchor="end",
                                        rng=np.random.default_rng(seed)))
    shift = point.sum() - mean.sum()
    lo, hi = np.quantile(paths.sum(axis=0) + shift, [alpha / 2, 1 - alpha / 2])
    return Forecast(table, float(point.sum()), float(max(lo, 0)), float(hi), METHOD, level,
                    backtest(series, horizon))
