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
SELECTION_POLICY_LABELS = {
    "coverage_first": "coverage优先",
    "calibrated_if_clear": "校准明显更优才切换",
    "default": "默认策略",
}
BASE_LABELS = {
    "coverage_adjusted": "coverage_adjusted",
    "raw": "raw",
}
METHOD_LABELS = {
    "raw": "raw",
    "coverage_adjusted": "coverage_adjusted",
    "calibrated": "calibrated",
    "N/A": "N/A",
    None: "N/A",
}


@st.cache_resource
def get_cached_session_factory():
    init_db()
    return get_session_factory()


@st.cache_resource
def get_cached_data_source():
    return AKShareDataSource(raw_dir=RAW_CACHE_DIR)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(180deg, #08111f 0%, #0d1726 38%, #101826 100%);
        }
        .block-container {
            padding-top: 2.2rem;
            padding-bottom: 2rem;
            max-width: 1380px;
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #121924 0%, #171f2b 100%);
            border-right: 1px solid rgba(255,255,255,0.06);
        }
        .app-shell {
            padding: 1.25rem 1.4rem 1.1rem;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 24px;
            background: linear-gradient(145deg, rgba(18,25,36,0.96), rgba(9,15,25,0.92));
            box-shadow: 0 28px 60px rgba(0,0,0,0.28);
        }
        .hero-eyebrow {
            color: #ff6b6b;
            font-size: 0.92rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .hero-title {
            margin: 0.35rem 0 0.2rem;
            font-size: 3.2rem;
            font-weight: 800;
            line-height: 1.02;
        }
        .hero-subtitle {
            color: rgba(255,255,255,0.72);
            font-size: 1.05rem;
            margin-bottom: 0.4rem;
        }
        .meta-strip {
            margin-top: 1rem;
            padding: 0.8rem 1rem;
            border-radius: 16px;
            background: rgba(255,255,255,0.04);
            color: rgba(255,255,255,0.72);
            font-size: 0.92rem;
        }
        .section-title {
            margin-top: 0.4rem;
            margin-bottom: 0.75rem;
            font-size: 1.85rem;
            font-weight: 800;
        }
        .section-caption {
            color: rgba(255,255,255,0.7);
            margin-bottom: 0.9rem;
        }
        div[data-testid="metric-container"] {
            background: rgba(255,255,255,0.035);
            border: 1px solid rgba(255,255,255,0.07);
            padding: 1rem 1rem 0.85rem;
            border-radius: 18px;
            min-height: 128px;
        }
        div[data-testid="metric-container"] label {
            color: rgba(255,255,255,0.68);
            font-size: 0.92rem;
        }
        div[data-testid="metric-container"] [data-testid="stMetricValue"] {
            font-size: 2rem;
            line-height: 1.05;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 1rem;
        }
        .stTabs [data-baseweb="tab"] {
            height: 2.7rem;
            padding-left: 0.25rem;
            padding-right: 0.25rem;
        }
        .sidebar-help {
            padding: 0.9rem 1rem;
            border-radius: 16px;
            background: rgba(255,255,255,0.04);
            color: rgba(255,255,255,0.74);
            font-size: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def format_method_name(method: str | None) -> str:
    return METHOD_LABELS.get(method, str(method))


def format_policy_name(policy: str) -> str:
    return SELECTION_POLICY_LABELS.get(policy, policy)


def format_distribution(distribution: str | None) -> str:
    if not distribution:
        return "N/A"
    text = distribution
    for key, value in METHOD_LABELS.items():
        if key:
            text = text.replace(f"{key}:", f"{value}:")
    return text


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
                "策略": format_policy_name(item.selection_policy),
                "日期区间": f"{item.start_date.isoformat()}~{item.end_date.isoformat()}",
                "样本数": item.sample_count,
                "raw_MAE": format_percent(item.raw_mean_abs_error),
                "coverage_MAE": format_percent(item.coverage_adjusted_mean_abs_error),
                "calibrated_MAE": format_percent(item.calibrated_mean_abs_error),
                "best_MAE": format_percent(item.best_mean_abs_error),
                "最优单一方法": format_method_name(item.best_single_method),
                "best方法分布": format_distribution(item.best_method_distribution),
                "best命中率": format_hit_rate(item.best_direction_hit_rate),
                "best_corr": format_ratio(item.best_corr),
            }
            for item in results
        ]
    )


def comparison_rows_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    display_frame = pd.DataFrame(rows)[
        [
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
    ].rename(
        columns={
            "trade_date": "交易日",
            "actual_return": "真实涨跌",
            "raw_estimate": "原始估值",
            "coverage_adjusted_estimate": "覆盖修正估值",
            "calibrated_estimate": "校准估值",
            "best_estimate": "最终估值",
            "best_method": "最终方法",
            "raw_error": "raw误差",
            "coverage_error": "coverage误差",
            "calibrated_error": "calibrated误差",
            "best_error": "最终误差",
            "confidence_level": "置信等级",
        }
    )
    display_frame["最终方法"] = display_frame["最终方法"].map(format_method_name)
    return display_frame


def render_action_report() -> None:
    report = st.session_state.get("last_action_report")
    if report is None:
        st.info("还没有执行更新操作。")
        return

    st.markdown('<div class="section-title">最近一次操作</div>', unsafe_allow_html=True)
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
                        "best方法分布": format_distribution(item.best_method_distribution),
                        "置信等级": item.confidence_level or "N/A",
                    }
                    for item in summaries
                ]
            ),
            use_container_width=True,
        )


def render_fund_editor(session_factory) -> None:
    st.subheader("基金管理")
    st.caption("直接维护基金池, 不再依赖 CSV 批量改动。")
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
            "fund_code": st.column_config.TextColumn("基金代码"),
            "fund_name": st.column_config.TextColumn("基金名称"),
            "fund_type": st.column_config.TextColumn("基金类型"),
            "market": st.column_config.TextColumn("市场"),
            "is_active": st.column_config.CheckboxColumn("启用"),
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
    st.caption("编辑后会保存为新的 active 持仓版本。")

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
            "fund_code": st.column_config.TextColumn("基金代码"),
            "report_date": st.column_config.TextColumn("报告日"),
            "source": st.column_config.TextColumn("来源"),
            "asset_code": st.column_config.TextColumn("资产代码"),
            "asset_name": st.column_config.TextColumn("资产名称"),
            "asset_type": st.column_config.TextColumn("资产类型"),
            "weight_pct": st.column_config.NumberColumn("权重(%)", format="%.4f"),
        },
        key="holding_editor",
    )
    total_weight = pd.to_numeric(edited["weight_pct"], errors="coerce").fillna(0).sum()
    st.caption(f"当前持仓合计: {total_weight:.2f}%")
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
    st.caption("股票仓位会直接影响 coverage_adjusted_estimate。")

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
            "fund_code": st.column_config.TextColumn("基金代码"),
            "report_date": st.column_config.TextColumn("报告日"),
            "source": st.column_config.TextColumn("来源"),
            "stock_weight_pct": st.column_config.NumberColumn("股票仓位(%)", format="%.4f"),
            "bond_weight_pct": st.column_config.NumberColumn("债券仓位(%)", format="%.4f"),
            "cash_weight_pct": st.column_config.NumberColumn("现金仓位(%)", format="%.4f"),
            "other_weight_pct": st.column_config.NumberColumn("其他仓位(%)", format="%.4f"),
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
    st.caption("当前阶段行业配置主要用于留存, 后续可接代理指数估值。")

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
            "fund_code": st.column_config.TextColumn("基金代码"),
            "report_date": st.column_config.TextColumn("报告日"),
            "source": st.column_config.TextColumn("来源"),
            "industry_name": st.column_config.TextColumn("行业名称"),
            "industry_code": st.column_config.TextColumn("行业代码"),
            "weight_pct": st.column_config.NumberColumn("权重(%)", format="%.4f"),
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

    st.markdown(
        f"""
        <div class="app-shell">
            <div class="hero-eyebrow">Fund NAV Estimator</div>
            <div class="hero-title">{snapshot['fund_name'] or '未命名基金'}</div>
            <div class="hero-subtitle">
                {snapshot['fund_code']} | 当前策略: {format_policy_name(selection_policy)} | 统计窗口: {window} 日
            </div>
            <div class="meta-strip">
                持仓报告日: {holding_summary['report_date'] or 'N/A'} |
                资产配置报告日: {allocation_summary['report_date'] or 'N/A'} |
                股票仓位: {'N/A' if allocation_summary['stock_weight_pct'] is None else f"{allocation_summary['stock_weight_pct']:.2f}%"}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-title">结果看板</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-caption">把 raw, coverage, calibrated, best 放在同一页看, 先比较结果, 再决定是否切策略。</div>',
        unsafe_allow_html=True,
    )

    top_cards = st.columns(4)
    top_cards[0].metric("基金", f"{snapshot['fund_code']} | {snapshot['fund_name'] or 'N/A'}")
    top_cards[1].metric("最新估值日期", snapshot["latest_estimate_date"].isoformat() if snapshot["latest_estimate_date"] else "N/A")
    top_cards[2].metric("最新真实净值日期", snapshot["latest_actual_date"].isoformat() if snapshot["latest_actual_date"] else "N/A")
    top_cards[3].metric("置信等级", snapshot["confidence_level"] or "N/A")

    estimate_cards = st.columns(5)
    estimate_cards[0].metric("原始估值", format_percent(snapshot["raw_estimate"], signed=True))
    estimate_cards[1].metric("覆盖修正估值", format_percent(snapshot["coverage_adjusted_estimate"], signed=True))
    estimate_cards[2].metric("校准估值", format_percent(snapshot["calibrated_estimate"], signed=True))
    estimate_cards[3].metric("最终估值", format_percent(snapshot["best_estimate"], signed=True))
    estimate_cards[4].metric("最终方法", format_method_name(snapshot["best_method"]))

    performance_cards = st.columns(3)
    performance_cards[0].metric("最近 MAE", format_percent(snapshot["latest_mae"]))
    performance_cards[1].metric("方向命中率", format_hit_rate(snapshot["direction_hit_rate"]))
    performance_cards[2].metric(
        "股票仓位",
        "N/A" if allocation_summary["stock_weight_pct"] is None else f"{allocation_summary['stock_weight_pct']:.2f}%",
    )

    comparison_frame = comparison_rows_to_frame(compare_rows)
    title_col, export_col = st.columns([5, 1.4])
    title_col.markdown('<div class="section-title">估值对比表</div>', unsafe_allow_html=True)
    export_col.write("")
    export_col.write("")
    export_col.download_button(
        "导出 CSV",
        data=dataframe_to_csv_bytes(comparison_frame),
        file_name=f"{selected_fund}_estimate_comparison.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.dataframe(comparison_frame, use_container_width=True, hide_index=True)

    chart_frame = pd.DataFrame(compare_rows)
    left_chart, right_chart = st.columns(2)
    left_chart.plotly_chart(build_return_comparison_figure(chart_frame), use_container_width=True)
    right_chart.plotly_chart(build_error_figure(chart_frame), use_container_width=True)

    st.markdown('<div class="section-title">历史准确率统计</div>', unsafe_allow_html=True)
    metric_tab1, metric_tab2, metric_tab3, metric_tab4 = st.tabs(
        ["基础误差", "三种估值比较", "校准效果", "最终选择结果"]
    )
    with metric_tab1:
        st.dataframe(stats_to_frame(stats_rows), use_container_width=True, hide_index=True)
    with metric_tab2:
        st.dataframe(compare_to_frame(compare_stats_rows), use_container_width=True, hide_index=True)
    with metric_tab3:
        st.dataframe(calibration_to_frame(calibration_rows), use_container_width=True, hide_index=True)
    with metric_tab4:
        st.dataframe(selected_to_frame(selected_rows), use_container_width=True, hide_index=True)

    st.markdown('<div class="section-title">当前 active holdings</div>', unsafe_allow_html=True)
    holdings_frame = pd.DataFrame(holding_summary["rows"]).rename(
        columns={
            "asset_code": "资产代码",
            "asset_name": "资产名称",
            "asset_type": "资产类型",
            "weight_pct": "权重(%)",
        }
    )
    st.dataframe(holdings_frame, use_container_width=True, hide_index=True)


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
    inject_styles()
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

    st.sidebar.markdown("## 基金选择")
    selected_fund = st.sidebar.selectbox(
        "基金",
        options=list(fund_map.keys()),
        index=default_index if fund_map else None,
        format_func=lambda item: f"{item} | {fund_map[item]}",
    ) if fund_map else None

    default_start = sidebar_context["start_date"] or date.today().replace(day=1)
    default_end = sidebar_context["end_date"] or date.today()

    st.sidebar.markdown("## 日期范围")
    start_date = st.sidebar.date_input("开始日期", value=default_start)
    end_date = st.sidebar.date_input("结束日期", value=default_end)

    st.sidebar.markdown("## 估值策略")
    selection_policy = st.sidebar.selectbox(
        "最终估值选择策略",
        options=["coverage_first", "calibrated_if_clear", "default"],
        index=0,
        format_func=format_policy_name,
    )

    with st.sidebar.expander("高级参数", expanded=False):
        window = int(st.number_input("统计窗口", min_value=5, max_value=120, value=20, step=1))
        base = st.selectbox("校准基准", options=["coverage_adjusted", "raw"], index=0, format_func=lambda item: BASE_LABELS[item])
        min_samples = int(st.number_input("最小样本数", min_value=3, max_value=60, value=5, step=1))
        sleep_seconds = float(st.number_input("抓取间隔(秒)", min_value=0.0, max_value=2.0, value=0.2, step=0.1))

    st.sidebar.markdown("## 操作按钮")
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

    st.sidebar.markdown(
        """
        <div class="sidebar-help">
            日常使用建议:
            <br/>1. 先选基金和日期区间
            <br/>2. 点"更新该基金历史数据"
            <br/>3. 回到结果看板看 raw, coverage, calibrated, best 的差异
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.caption("启动命令")
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
