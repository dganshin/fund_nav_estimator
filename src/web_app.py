from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import select

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.backfill import backfill_history, fetch_and_store_fund_navs, fetch_and_store_stock_quotes, get_active_holding_asset_codes
    from src.data_sources import AKShareDataSource, DataSourceError
    from src.db import get_session_factory
    from src.estimator import (
        calculate_calibration_stats,
        calculate_compare_estimates,
        calculate_error_stats,
        calculate_selected_stats,
        format_hit_rate,
        format_percent,
        format_ratio,
    )
    from src.import_data import DataImportError, parse_date
    from src.init_db import init_db
    from src.models import DailyQuote, FundNav, SelectedEstimate
    from src.web_services import (
        default_date_range,
        list_fund_options,
        load_asset_allocation_rows,
        load_fund_rows,
        load_holding_rows,
        load_industry_allocation_rows,
        save_asset_allocation_rows,
        save_fund_rows,
        save_holding_rows,
        save_industry_allocation_rows,
    )
else:
    from .backfill import backfill_history, fetch_and_store_fund_navs, fetch_and_store_stock_quotes, get_active_holding_asset_codes
    from .data_sources import AKShareDataSource, DataSourceError
    from .db import get_session_factory
    from .estimator import (
        calculate_calibration_stats,
        calculate_compare_estimates,
        calculate_error_stats,
        calculate_selected_stats,
        format_hit_rate,
        format_percent,
        format_ratio,
    )
    from .import_data import DataImportError, parse_date
    from .init_db import init_db
    from .models import DailyQuote, FundNav, SelectedEstimate
    from .web_services import (
        default_date_range,
        list_fund_options,
        load_asset_allocation_rows,
        load_fund_rows,
        load_holding_rows,
        load_industry_allocation_rows,
        save_asset_allocation_rows,
        save_fund_rows,
        save_holding_rows,
        save_industry_allocation_rows,
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "akshare"


@st.cache_resource
def get_cached_session_factory():
    init_db()
    return get_session_factory()


@st.cache_resource
def get_cached_data_source():
    return AKShareDataSource(cache_dir=RAW_CACHE_DIR)


def clean_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    normalized = frame.astype(object).where(pd.notna(frame), None)
    return normalized.to_dict("records")


def make_table(rows: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame([{column: None for column in columns}])
    return pd.DataFrame(rows, columns=columns)


def show_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        st.warning(warning)


def stats_to_frame(results) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金代码": item.fund_code,
                "基金名称": item.fund_name,
                "样本数": item.sample_count,
                "平均误差": format_percent(item.mean_error, signed=True),
                "平均绝对误差": format_percent(item.mean_abs_error),
                "最大绝对误差": format_percent(item.max_abs_error),
                "方向命中率": format_hit_rate(item.direction_hit_rate),
                "相关系数": format_ratio(item.estimate_actual_corr),
                "最近误差": format_percent(item.latest_error, signed=True),
                "最近交易日": item.latest_trade_date.isoformat(),
            }
            for item in results
        ]
    )


def compare_to_frame(results) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金代码": item.fund_code,
                "基金名称": item.fund_name,
                "日期区间": f"{item.start_date.isoformat()}~{item.end_date.isoformat()}",
                "样本数": item.sample_count,
                "raw_MAE": format_percent(item.raw_mean_abs_error),
                "coverage_MAE": format_percent(item.coverage_adjusted_mean_abs_error),
                "calibrated_MAE": format_percent(item.calibrated_mean_abs_error),
                "最优方法": item.best_method or "N/A",
                "raw命中率": format_hit_rate(item.raw_direction_hit_rate),
                "coverage命中率": format_hit_rate(item.coverage_direction_hit_rate),
                "calibrated命中率": format_hit_rate(item.calibrated_direction_hit_rate),
                "raw_corr": format_ratio(item.raw_corr),
                "coverage_corr": format_ratio(item.coverage_corr),
                "calibrated_corr": format_ratio(item.calibrated_corr),
            }
            for item in results
        ]
    )


def calibration_to_frame(results) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金代码": item.fund_code,
                "基金名称": item.fund_name,
                "样本数": item.sample_count,
                "base类型": item.base_estimate_type,
                "base_MAE": format_percent(item.base_mean_abs_error),
                "calibrated_MAE": format_percent(item.calibrated_mean_abs_error),
                "改进比例": format_percent(item.improvement_pct, signed=True) if item.improvement_pct is not None else "N/A",
                "base命中率": format_hit_rate(item.base_direction_hit_rate),
                "calibrated命中率": format_hit_rate(item.calibrated_direction_hit_rate),
                "base_corr": format_ratio(item.base_corr),
                "calibrated_corr": format_ratio(item.calibrated_corr),
            }
            for item in results
        ]
    )


def selected_to_frame(results) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金代码": item.fund_code,
                "基金名称": item.fund_name,
                "日期区间": f"{item.start_date.isoformat()}~{item.end_date.isoformat()}",
                "样本数": item.sample_count,
                "raw_MAE": format_percent(item.raw_mean_abs_error),
                "coverage_MAE": format_percent(item.coverage_adjusted_mean_abs_error),
                "calibrated_MAE": format_percent(item.calibrated_mean_abs_error),
                "best_MAE": format_percent(item.best_mean_abs_error),
                "最优单一方法": item.best_single_method or "N/A",
                "best方法分布": item.best_method_distribution,
                "best命中率": format_hit_rate(item.best_direction_hit_rate),
                "best_corr": format_ratio(item.best_corr),
            }
            for item in results
        ]
    )


def render_fund_editor(session_factory) -> None:
    st.subheader("基金池")
    with session_factory() as session:
        frame = make_table(
            load_fund_rows(session),
            ["fund_code", "fund_name", "fund_type", "market", "is_active"],
        )

    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "fund_code": st.column_config.TextColumn("fund_code"),
            "fund_name": st.column_config.TextColumn("fund_name"),
            "fund_type": st.column_config.TextColumn("fund_type"),
            "market": st.column_config.TextColumn("market"),
            "is_active": st.column_config.CheckboxColumn("is_active"),
        },
        key="fund_editor",
    )

    if st.button("保存基金池", key="save_funds"):
        with session_factory() as session:
            count = save_fund_rows(session, clean_records(edited))
        st.success(f"已保存基金 {count} 条")


def render_holdings_editor(session_factory, selected_fund: str | None) -> None:
    st.subheader("前十大持仓")
    if not selected_fund:
        st.info("先在左侧选择一个基金。")
        return

    with session_factory() as session:
        frame = make_table(
            load_holding_rows(session, selected_fund),
            ["fund_code", "report_date", "source", "asset_code", "asset_name", "asset_type", "weight_pct"],
        )

    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "fund_code": st.column_config.TextColumn("fund_code"),
            "report_date": st.column_config.TextColumn("report_date"),
            "source": st.column_config.TextColumn("source"),
            "asset_code": st.column_config.TextColumn("asset_code"),
            "asset_name": st.column_config.TextColumn("asset_name"),
            "asset_type": st.column_config.TextColumn("asset_type"),
            "weight_pct": st.column_config.NumberColumn("weight_pct", format="%.4f"),
        },
        key="holding_editor",
    )

    if st.button("保存持仓", key="save_holdings"):
        with session_factory() as session:
            count = save_holding_rows(session, clean_records(edited))
        st.success(f"已保存持仓版本 {count} 条")


def render_asset_editor(session_factory, selected_fund: str | None) -> None:
    st.subheader("资产配置")
    if not selected_fund:
        st.info("先在左侧选择一个基金。")
        return

    with session_factory() as session:
        frame = make_table(
            load_asset_allocation_rows(session, selected_fund),
            ["fund_code", "report_date", "source", "stock_weight_pct", "bond_weight_pct", "cash_weight_pct", "other_weight_pct"],
        )

    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "fund_code": st.column_config.TextColumn("fund_code"),
            "report_date": st.column_config.TextColumn("report_date"),
            "source": st.column_config.TextColumn("source"),
            "stock_weight_pct": st.column_config.NumberColumn("stock_weight_pct", format="%.4f"),
            "bond_weight_pct": st.column_config.NumberColumn("bond_weight_pct", format="%.4f"),
            "cash_weight_pct": st.column_config.NumberColumn("cash_weight_pct", format="%.4f"),
            "other_weight_pct": st.column_config.NumberColumn("other_weight_pct", format="%.4f"),
        },
        key="asset_editor",
    )

    if st.button("保存资产配置", key="save_asset"):
        with session_factory() as session:
            count = save_asset_allocation_rows(session, clean_records(edited))
        st.success(f"已保存资产配置 {count} 条")


def render_industry_editor(session_factory, selected_fund: str | None) -> None:
    st.subheader("行业配置")
    if not selected_fund:
        st.info("先在左侧选择一个基金。")
        return

    with session_factory() as session:
        frame = make_table(
            load_industry_allocation_rows(session, selected_fund),
            ["fund_code", "report_date", "source", "industry_name", "industry_code", "weight_pct"],
        )

    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "fund_code": st.column_config.TextColumn("fund_code"),
            "report_date": st.column_config.TextColumn("report_date"),
            "source": st.column_config.TextColumn("source"),
            "industry_name": st.column_config.TextColumn("industry_name"),
            "industry_code": st.column_config.TextColumn("industry_code"),
            "weight_pct": st.column_config.NumberColumn("weight_pct", format="%.4f"),
        },
        key="industry_editor",
    )

    if st.button("保存行业配置", key="save_industry"):
        with session_factory() as session:
            count = save_industry_allocation_rows(session, clean_records(edited))
        st.success(f"已保存行业配置 {count} 条")


def render_actions_tab(session_factory, data_source, selected_fund: str | None) -> None:
    st.subheader("历史回填")
    if not selected_fund:
        st.info("先在左侧选择一个基金。")
        return

    default_start, default_end = default_date_range()
    left, right = st.columns(2)
    start_date = left.date_input("start_date", value=default_start, key="action_start")
    end_date = right.date_input("end_date", value=default_end, key="action_end")
    option_left, option_mid, option_right = st.columns(3)
    window = option_left.number_input("window", min_value=5, max_value=120, value=20, step=1)
    min_samples = option_mid.number_input("min_samples", min_value=3, max_value=60, value=5, step=1)
    sleep_seconds = option_right.number_input("sleep_seconds", min_value=0.0, max_value=2.0, value=0.2, step=0.1)
    base = st.selectbox("base", options=["coverage_adjusted", "raw"], index=0)

    button_left, button_mid, button_right = st.columns(3)

    if button_left.button("抓基金净值", use_container_width=True):
        try:
            with session_factory() as session:
                report = fetch_and_store_fund_navs(session, data_source, selected_fund, start_date, end_date)
                preview = session.scalars(
                    select(FundNav)
                    .where(FundNav.fund_code == selected_fund, FundNav.trade_date >= start_date, FundNav.trade_date <= end_date)
                    .order_by(FundNav.trade_date.asc())
                ).all()
            st.success(f"已导入基金净值 {report.imported_count} 条, 生成真实涨跌 {report.generated_actual_returns} 条")
            show_warnings(report.warnings)
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "trade_date": row.trade_date.isoformat(),
                            "fund_code": row.fund_code,
                            "unit_nav": row.unit_nav,
                            "accumulated_nav": row.accumulated_nav,
                            "source": row.source,
                        }
                        for row in preview
                    ]
                ),
                use_container_width=True,
            )
        except (DataSourceError, DataImportError, ValueError) as exc:
            st.error(str(exc))

    if button_mid.button("抓股票行情", use_container_width=True):
        try:
            with session_factory() as session:
                asset_codes = get_active_holding_asset_codes(session, selected_fund, end_date)
                report = fetch_and_store_stock_quotes(
                    session,
                    data_source,
                    start_date,
                    end_date,
                    asset_codes,
                    sleep_seconds=sleep_seconds,
                )
                preview = session.scalars(
                    select(DailyQuote)
                    .where(DailyQuote.asset_code.in_(asset_codes), DailyQuote.trade_date >= start_date, DailyQuote.trade_date <= end_date)
                    .order_by(DailyQuote.trade_date.asc(), DailyQuote.asset_code.asc())
                ).all()
            st.success(f"已导入股票行情 {report.imported_count} 条")
            show_warnings(report.warnings)
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "trade_date": row.trade_date.isoformat(),
                            "asset_code": row.asset_code,
                            "asset_name": row.asset_name,
                            "return_pct": format_percent(row.return_pct, signed=True),
                            "source": row.source,
                        }
                        for row in preview
                    ]
                ),
                use_container_width=True,
            )
        except (DataSourceError, DataImportError, ValueError) as exc:
            st.error(str(exc))

    if button_right.button("一键回填", use_container_width=True):
        try:
            with session_factory() as session:
                (
                    nav_report,
                    quote_report,
                    estimate_report,
                    reconcile_report,
                    calibration_count,
                    selection_count,
                    summaries,
                ) = backfill_history(
                    session=session,
                    data_source=data_source,
                    fund_code=selected_fund,
                    start_date=start_date,
                    end_date=end_date,
                    window=window,
                    base=base,
                    min_samples=min_samples,
                    sleep_seconds=sleep_seconds,
                )
            st.success("历史回填完成")
            st.write(
                {
                    "fund_navs": nav_report.imported_count,
                    "quotes": quote_report.imported_count,
                    "estimates": estimate_report.total_count,
                    "reconcile": reconcile_report.total_count,
                    "calibrate_history": calibration_count,
                    "select_history": selection_count,
                }
            )
            show_warnings(nav_report.warnings + quote_report.warnings + estimate_report.warnings + reconcile_report.warnings)
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "基金代码": item.fund_code,
                            "基金名称": item.fund_name,
                            "日期区间": f"{item.start_date.isoformat()}~{item.end_date.isoformat()}",
                            "样本数": item.sample_count,
                            "raw_MAE": format_percent(item.raw_mean_abs_error),
                            "coverage_MAE": format_percent(item.coverage_mean_abs_error),
                            "calibrated_MAE": format_percent(item.calibrated_mean_abs_error),
                            "best_MAE": format_percent(item.best_mean_abs_error),
                            "best方法分布": item.best_method_distribution,
                            "置信等级": item.confidence_level or "N/A",
                        }
                        for item in summaries
                    ]
                ),
                use_container_width=True,
            )
        except (DataSourceError, DataImportError, ValueError) as exc:
            st.error(str(exc))


def render_dashboard_tab(session_factory, selected_fund: str | None) -> None:
    st.subheader("结果看板")
    if not selected_fund:
        st.info("先在左侧选择一个基金。")
        return

    default_start = date(2026, 4, 1)
    default_end = date(2026, 5, 20)
    left, right = st.columns(2)
    start_date = left.date_input("统计开始日期", value=default_start, key="dashboard_start")
    end_date = right.date_input("统计结束日期", value=default_end, key="dashboard_end")
    option_left, option_mid = st.columns(2)
    window = option_left.number_input("统计窗口", min_value=5, max_value=120, value=20, step=1, key="dashboard_window")
    base = option_mid.selectbox("校准基准", options=["coverage_adjusted", "raw"], index=0, key="dashboard_base")

    with session_factory() as session:
        stats_rows = calculate_error_stats(session, fund_code=selected_fund, start_date=start_date, end_date=end_date)
        compare_rows = calculate_compare_estimates(
            session,
            fund_code=selected_fund,
            start_date=start_date,
            end_date=end_date,
            window=window,
            base=base,
        )
        calibration_rows = calculate_calibration_stats(
            session,
            fund_code=selected_fund,
            start_date=start_date,
            end_date=end_date,
            window=window,
            base=base,
        )
        selected_rows = calculate_selected_stats(
            session,
            fund_code=selected_fund,
            start_date=start_date,
            end_date=end_date,
            selection_window=window,
        )
        latest_selected = session.scalars(
            select(SelectedEstimate)
            .where(
                SelectedEstimate.fund_code == selected_fund,
                SelectedEstimate.trade_date >= start_date,
                SelectedEstimate.trade_date <= end_date,
                SelectedEstimate.selection_window == window,
            )
            .order_by(SelectedEstimate.trade_date.desc())
        ).first()

    if latest_selected is not None:
        st.info(
            f"最新 best_method: {latest_selected.best_method} | "
            f"best_estimate: {format_percent(latest_selected.best_estimate, signed=True)} | "
            f"confidence: {latest_selected.confidence_level or 'N/A'}"
        )

    st.markdown("**stats**")
    st.dataframe(stats_to_frame(stats_rows), use_container_width=True)
    st.markdown("**compare-estimates**")
    st.dataframe(compare_to_frame(compare_rows), use_container_width=True)
    st.markdown("**calibration-stats**")
    st.dataframe(calibration_to_frame(calibration_rows), use_container_width=True)
    st.markdown("**selected-stats**")
    st.dataframe(selected_to_frame(selected_rows), use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Fund NAV Estimator", layout="wide")
    st.title("Fund NAV Estimator")
    st.caption("本地录入 + 历史回填 + 估值结果查看")

    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()

    with session_factory() as session:
        fund_options = list_fund_options(session)

    fund_map = {code: name for code, name in fund_options}
    selected_fund = st.sidebar.selectbox("基金代码", options=[""] + list(fund_map.keys()), format_func=lambda item: item if item == "" else f"{item} | {fund_map[item]}")
    selected_fund = selected_fund or None
    st.sidebar.markdown("运行 Web:")
    st.sidebar.code("streamlit run src/web_app.py", language="bash")

    tab_funds, tab_holdings, tab_asset, tab_industry, tab_actions, tab_dashboard = st.tabs(
        ["基金池", "持仓", "资产配置", "行业配置", "历史回填", "结果看板"]
    )

    try:
        with tab_funds:
            render_fund_editor(session_factory)
        with tab_holdings:
            render_holdings_editor(session_factory, selected_fund)
        with tab_asset:
            render_asset_editor(session_factory, selected_fund)
        with tab_industry:
            render_industry_editor(session_factory, selected_fund)
        with tab_actions:
            render_actions_tab(session_factory, data_source, selected_fund)
        with tab_dashboard:
            render_dashboard_tab(session_factory, selected_fund)
    except (DataImportError, DataSourceError, ValueError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
