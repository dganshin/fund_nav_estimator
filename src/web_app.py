from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
    from src.import_data import DataImportError
    from src.init_db import init_db
    from src.web import (
        build_error_figure,
        build_return_comparison_figure,
        dataframe_to_csv_bytes,
        get_active_asset_allocation_summary,
        get_active_holding_summary,
        get_fund_sidebar_context,
        get_latest_dashboard_snapshot,
        load_estimate_comparison_rows,
        run_backfill_action,
        run_recalculate_action,
        run_selection_action,
    )
    from src.web_services import (
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
    from .import_data import DataImportError
    from .init_db import init_db
    from .web import (
        build_error_figure,
        build_return_comparison_figure,
        dataframe_to_csv_bytes,
        get_active_asset_allocation_summary,
        get_active_holding_summary,
        get_fund_sidebar_context,
        get_latest_dashboard_snapshot,
        load_estimate_comparison_rows,
        run_backfill_action,
        run_recalculate_action,
        run_selection_action,
    )
    from .web_services import (
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
    return AKShareDataSource(raw_dir=RAW_CACHE_DIR)


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
                "policy": item.selection_policy,
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


def comparison_rows_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    display_columns = [
        "trade_date",
        "actual_return",
        "raw_estimate",
        "coverage_adjusted_estimate",
        "calibrated_estimate",
        "best_estimate",
        "best_method",
        "raw_error",
        "coverage_error",
        "calibrated_error",
        "best_error",
        "confidence_level",
    ]
    return pd.DataFrame(rows)[display_columns]


def render_action_report() -> None:
    report = st.session_state.get("last_action_report")
    if report is None:
        st.info("还没有执行更新操作。")
        return

    st.markdown("**最近一次操作日志**")
    for log in report.logs:
        st.write(log)
    show_warnings(report.warnings)

    summaries = report.payload.get("summaries") if isinstance(report.payload, dict) else None
    if summaries:
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


def render_fund_editor(session_factory) -> None:
    st.subheader("基金管理")
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

    if st.button("保存基金管理", key="save_funds"):
        with session_factory() as session:
            count = save_fund_rows(session, clean_records(edited))
        st.success(f"已保存基金 {count} 条")


def render_holdings_editor(session_factory, selected_fund: str | None) -> None:
    st.subheader("持仓管理")
    if not selected_fund:
        st.info("先在侧边栏选择一个基金。")
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
    total_weight = pd.to_numeric(edited["weight_pct"], errors="coerce").fillna(0).sum()
    st.caption(f"当前 total_weight: {total_weight:.2f}%")
    if total_weight > 100:
        st.warning("当前持仓 total_weight 超过 100%, 保存前请确认。")

    if st.button("保存为新的持仓版本", key="save_holdings"):
        with session_factory() as session:
            count = save_holding_rows(session, clean_records(edited))
        st.success(f"已保存持仓版本 {count} 条")


def render_asset_editor(session_factory, selected_fund: str | None) -> None:
    st.subheader("资产配置管理")
    if not selected_fund:
        st.info("先在侧边栏选择一个基金。")
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
        st.info("先在侧边栏选择一个基金。")
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


def render_dashboard_tab(
    session_factory,
    selected_fund: str | None,
    start_date: date,
    end_date: date,
    selection_policy: str,
    window: int,
    base: str,
) -> None:
    st.subheader("结果看板")
    if not selected_fund:
        st.info("先在侧边栏选择一个基金。")
        return

    with session_factory() as session:
        snapshot = get_latest_dashboard_snapshot(
            session,
            fund_code=selected_fund,
            selection_window=window,
            selection_policy=selection_policy,
        )
        compare_rows = load_estimate_comparison_rows(
            session,
            fund_code=selected_fund,
            start_date=start_date,
            end_date=end_date,
            window=window,
            selection_policy=selection_policy,
        )
        stats_rows = calculate_error_stats(session, fund_code=selected_fund, start_date=start_date, end_date=end_date)
        compare_stats_rows = calculate_compare_estimates(
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
            selection_policy=selection_policy,
        )
        holding_summary = get_active_holding_summary(session, selected_fund)
        allocation_summary = get_active_asset_allocation_summary(session, selected_fund)

    card1, card2, card3, card4 = st.columns(4)
    card1.metric("基金", f"{snapshot['fund_code']} | {snapshot['fund_name'] or 'N/A'}")
    card2.metric("最新估值日期", snapshot["latest_estimate_date"].isoformat() if snapshot["latest_estimate_date"] else "N/A")
    card3.metric("最新真实净值日期", snapshot["latest_actual_date"].isoformat() if snapshot["latest_actual_date"] else "N/A")
    card4.metric("confidence", snapshot["confidence_level"] or "N/A")

    card5, card6, card7, card8, card9 = st.columns(5)
    card5.metric("raw_estimate", format_percent(snapshot["raw_estimate"], signed=True))
    card6.metric("coverage_adjusted", format_percent(snapshot["coverage_adjusted_estimate"], signed=True))
    card7.metric("calibrated_estimate", format_percent(snapshot["calibrated_estimate"], signed=True))
    card8.metric("best_estimate", format_percent(snapshot["best_estimate"], signed=True))
    card9.metric("best_method", snapshot["best_method"] or "N/A")

    card10, card11, card12 = st.columns(3)
    card10.metric("最近 MAE", format_percent(snapshot["latest_mae"]))
    card11.metric("方向命中率", format_hit_rate(snapshot["direction_hit_rate"]))
    card12.metric("股票仓位", "N/A" if allocation_summary["stock_weight_pct"] is None else f"{allocation_summary['stock_weight_pct']:.2f}%")

    st.caption(
        f"active holding report_date: {holding_summary['report_date'] or 'N/A'} | "
        f"active asset allocation report_date: {allocation_summary['report_date'] or 'N/A'}"
    )

    comparison_frame = comparison_rows_to_frame(compare_rows)
    st.markdown("**估值对比表**")
    st.download_button(
        "导出估值对比 CSV",
        data=dataframe_to_csv_bytes(comparison_frame),
        file_name=f"{selected_fund}_estimate_comparison.csv",
        mime="text/csv",
    )
    st.dataframe(comparison_frame, use_container_width=True)

    chart_frame = pd.DataFrame(compare_rows)
    left_chart, right_chart = st.columns(2)
    left_chart.plotly_chart(build_return_comparison_figure(chart_frame), use_container_width=True)
    right_chart.plotly_chart(build_error_figure(chart_frame), use_container_width=True)

    st.markdown("**历史准确率统计**")
    st.dataframe(stats_to_frame(stats_rows), use_container_width=True)
    st.dataframe(compare_to_frame(compare_stats_rows), use_container_width=True)
    st.dataframe(calibration_to_frame(calibration_rows), use_container_width=True)
    st.dataframe(selected_to_frame(selected_rows), use_container_width=True)

    st.markdown("**当前 active holdings**")
    st.dataframe(pd.DataFrame(holding_summary["rows"]), use_container_width=True)


def run_sidebar_action(
    action_name: str,
    session_factory,
    data_source,
    selected_fund: str | None,
    start_date: date,
    end_date: date,
    selection_policy: str,
    window: int,
    base: str,
    min_samples: int,
    sleep_seconds: float,
) -> None:
    if not selected_fund:
        st.sidebar.error("请先选择基金。")
        return

    with st.sidebar.status(f"{action_name} 运行中", expanded=True) as status:
        try:
            with session_factory() as session:
                if action_name == "更新该基金历史数据":
                    report = run_backfill_action(
                        session=session,
                        data_source=data_source,
                        fund_code=selected_fund,
                        start_date=start_date,
                        end_date=end_date,
                        window=window,
                        base=base,
                        min_samples=min_samples,
                        selection_policy=selection_policy,
                        sleep_seconds=sleep_seconds,
                    )
                elif action_name == "重新计算估值":
                    report = run_recalculate_action(
                        session=session,
                        fund_code=selected_fund,
                        start_date=start_date,
                        end_date=end_date,
                        window=window,
                        base=base,
                        min_samples=min_samples,
                        selection_policy=selection_policy,
                    )
                else:
                    report = run_selection_action(
                        session=session,
                        fund_code=selected_fund,
                        start_date=start_date,
                        end_date=end_date,
                        selection_window=window,
                        min_samples=max(10, min_samples),
                        min_improvement_bps=5,
                        selection_policy=selection_policy,
                    )
            st.session_state["last_action_report"] = report
            for log in report.logs:
                st.write(log)
            for warning in report.warnings:
                st.write(warning)
            status.update(label=f"{action_name} 完成", state="complete")
        except (DataImportError, DataSourceError, ValueError) as exc:
            status.update(label=f"{action_name} 失败", state="error")
            st.session_state["last_action_report"] = None
            st.sidebar.error(str(exc))


def main() -> None:
    st.set_page_config(page_title="Fund NAV Estimator", layout="wide")
    st.title("Fund NAV Estimator")
    st.caption("本地录入 + 一键更新 + 估值对比")

    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()

    with session_factory() as session:
        sidebar_context = get_fund_sidebar_context(session)

    fund_options = sidebar_context["fund_options"]
    fund_map = {code: name for code, name in fund_options}
    default_fund_code = sidebar_context["selected_fund_code"]
    default_index = 0
    if default_fund_code and default_fund_code in fund_map:
        default_index = list(fund_map.keys()).index(default_fund_code)

    st.sidebar.markdown("**基金选择**")
    selected_fund = st.sidebar.selectbox(
        "fund_code",
        options=list(fund_map.keys()),
        index=default_index if fund_map else None,
        format_func=lambda item: f"{item} | {fund_map[item]}",
    ) if fund_map else None

    default_start = sidebar_context["start_date"] or date.today().replace(day=1)
    default_end = sidebar_context["end_date"] or date.today()

    st.sidebar.markdown("**日期范围**")
    start_date = st.sidebar.date_input("start_date", value=default_start)
    end_date = st.sidebar.date_input("end_date", value=default_end)

    st.sidebar.markdown("**估值策略**")
    selection_policy = st.sidebar.selectbox(
        "selection_policy",
        options=["coverage_first", "calibrated_if_clear", "default"],
        index=0,
    )
    window = int(st.sidebar.number_input("window", min_value=5, max_value=120, value=20, step=1))
    base = st.sidebar.selectbox("base", options=["coverage_adjusted", "raw"], index=0)
    min_samples = int(st.sidebar.number_input("min_samples", min_value=3, max_value=60, value=5, step=1))
    sleep_seconds = float(st.sidebar.number_input("sleep_seconds", min_value=0.0, max_value=2.0, value=0.2, step=0.1))

    st.sidebar.markdown("**操作按钮**")
    if st.sidebar.button("刷新页面", use_container_width=True):
        st.rerun()
    if st.sidebar.button("更新该基金历史数据", use_container_width=True):
        run_sidebar_action(
            "更新该基金历史数据",
            session_factory,
            data_source,
            selected_fund,
            start_date,
            end_date,
            selection_policy,
            window,
            base,
            min_samples,
            sleep_seconds,
        )
    if st.sidebar.button("重新计算估值", use_container_width=True):
        run_sidebar_action(
            "重新计算估值",
            session_factory,
            data_source,
            selected_fund,
            start_date,
            end_date,
            selection_policy,
            window,
            base,
            min_samples,
            sleep_seconds,
        )
    if st.sidebar.button("重新生成 selected_estimates", use_container_width=True):
        run_sidebar_action(
            "重新生成 selected_estimates",
            session_factory,
            data_source,
            selected_fund,
            start_date,
            end_date,
            selection_policy,
            window,
            base,
            min_samples,
            sleep_seconds,
        )

    st.sidebar.markdown("运行命令")
    st.sidebar.code("streamlit run src/web_app.py", language="bash")

    tab_dashboard, tab_actions, tab_funds, tab_holdings, tab_asset, tab_industry = st.tabs(
        ["结果看板", "更新日志", "基金管理", "持仓管理", "资产配置", "行业配置"]
    )

    try:
        with tab_dashboard:
            render_dashboard_tab(
                session_factory=session_factory,
                selected_fund=selected_fund,
                start_date=start_date,
                end_date=end_date,
                selection_policy=selection_policy,
                window=window,
                base=base,
            )
        with tab_actions:
            render_action_report()
        with tab_funds:
            render_fund_editor(session_factory)
        with tab_holdings:
            render_holdings_editor(session_factory, selected_fund)
        with tab_asset:
            render_asset_editor(session_factory, selected_fund)
        with tab_industry:
            render_industry_editor(session_factory, selected_fund)
    except (DataImportError, DataSourceError, ValueError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
