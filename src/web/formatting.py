from __future__ import annotations

import pandas as pd


def format_nullable_percent(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.2f}%"


def format_method_distribution(value: str | None) -> str:
    return value or "N/A"


def dataframe_to_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")
