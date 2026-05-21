from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def build_return_comparison_figure(frame: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    if frame.empty:
        figure.update_layout(title="实际涨跌 vs 估算涨跌")
        return figure

    for column_name, label in (
        ("actual_return_value", "actual_return"),
        ("raw_estimate_value", "raw_estimate"),
        ("coverage_adjusted_estimate_value", "coverage_adjusted_estimate"),
        ("calibrated_estimate_value", "calibrated_estimate"),
        ("best_estimate_value", "best_estimate"),
    ):
        if column_name not in frame.columns:
            continue
        if frame[column_name].dropna().empty:
            continue
        figure.add_trace(
            go.Scatter(
                x=frame["trade_date"],
                y=frame[column_name] * 100,
                mode="lines+markers",
                name=label,
            )
        )

    figure.update_layout(
        title="实际涨跌 vs 估算涨跌",
        xaxis_title="trade_date",
        yaxis_title="return_pct",
        hovermode="x unified",
        legend_title="series",
    )
    return figure


def build_error_figure(frame: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    if frame.empty:
        figure.update_layout(title="每日误差")
        return figure

    for column_name, label in (
        ("raw_error_value", "raw_error"),
        ("coverage_error_value", "coverage_error"),
        ("calibrated_error_value", "calibrated_error"),
        ("best_error_value", "best_error"),
    ):
        if column_name not in frame.columns:
            continue
        if frame[column_name].dropna().empty:
            continue
        figure.add_trace(
            go.Scatter(
                x=frame["trade_date"],
                y=frame[column_name] * 100,
                mode="lines+markers",
                name=label,
            )
        )

    figure.update_layout(
        title="每日误差",
        xaxis_title="trade_date",
        yaxis_title="error_pct",
        hovermode="x unified",
        legend_title="series",
    )
    return figure
