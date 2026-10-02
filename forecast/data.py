"""Reading real-world sales exports: Excel, CSV with ; or , and Russian number formats ("1 234,50")."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd

PATTERNS = {
    "date": r"дата|date|день|period|период",
    "product": r"товар|product|наименован|номенклатур|sku|артикул|название|item",
    "revenue": r"выручк|revenue|сумм|amount|sales|продаж|оборот",
    "qty": r"кол|qty|quantity|units|шт|штук",
    "category": r"категор|category|группа|group",
}


@dataclass
class Columns:
    date: str
    revenue: str
    product: str | None = None
    qty: str | None = None
    category: str | None = None


def read_table(content: bytes, filename: str) -> pd.DataFrame:
    """Excel or CSV; delimiter and encoding are detected (1С and Excel in Russia often use ; and cp1251)."""
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Не удалось прочитать файл: неизвестная кодировка")
    first = text.splitlines()[0] if text else ""
    sep = ";" if first.count(";") >= first.count(",") and ";" in first else ("\t" if "\t" in first else ",")
    return pd.read_csv(io.StringIO(text), sep=sep)


def to_number(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    cleaned = (s.astype(str).str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
               .str.replace("₽", "", regex=False).str.replace("руб.", "", regex=False)
               .str.replace(",", ".", regex=False))
    return pd.to_numeric(cleaned, errors="coerce")


def detect_columns(df: pd.DataFrame) -> Columns:
    found: dict[str, str | None] = {}
    for role, pattern in PATTERNS.items():
        found[role] = next((c for c in df.columns if re.search(pattern, str(c), re.I)), None)
    if found["date"] is None:  # no telling header: take the first column that parses as dates
        for c in df.columns:
            parsed = pd.to_datetime(df[c].head(50), errors="coerce", dayfirst=True)
            if parsed.notna().mean() > 0.9:
                found["date"] = c
                break
    if found["revenue"] is None:  # largest numeric column is usually the money
        numeric = [c for c in df.columns if c not in found.values() and to_number(df[c]).notna().mean() > 0.9]
        if numeric:
            found["revenue"] = max(numeric, key=lambda c: to_number(df[c]).sum())
    if not found["date"] or not found["revenue"]:
        raise ValueError("Не нашёл колонки с датой и суммой продаж — выберите их вручную")
    return Columns(**found)


def prepare(df: pd.DataFrame, cols: Columns) -> pd.DataFrame:
    """Normalized table: date, product, category, qty, revenue (one row per input row, bad rows dropped)."""
    out = pd.DataFrame({
        "date": pd.to_datetime(df[cols.date], errors="coerce", dayfirst=not _iso_dates(df[cols.date])),
        "revenue": to_number(df[cols.revenue]),
        "product": df[cols.product].astype(str).str.strip() if cols.product else "Все товары",
        "category": df[cols.category].astype(str).str.strip() if cols.category else "—",
        "qty": to_number(df[cols.qty]) if cols.qty else float("nan"),
    })
    out = out.dropna(subset=["date", "revenue"])
    out["date"] = out["date"].dt.normalize()
    return out.sort_values("date").reset_index(drop=True)


def _iso_dates(s: pd.Series) -> bool:
    sample = s.astype(str).head(20)
    return bool(sample.str.match(r"^\d{4}-\d{2}-\d{2}").mean() > 0.8)


def daily(sales: pd.DataFrame, value: str = "revenue", product: str | None = None) -> pd.Series:
    """Daily totals with missing days filled as 0 (no sales that day)."""
    df = sales if product is None else sales[sales["product"] == product]
    s = df.groupby("date")[value].sum()
    if s.empty:
        return s
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"), fill_value=0.0).astype(float)
