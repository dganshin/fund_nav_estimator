import logging
from datetime import date, timedelta, datetime, timezone
from traceback import format_exc

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .data_sources import AKShareDataSource
from .db import get_session_factory
from .models import (
    ActualReturn,
    CalibrationResidual,
    DailyQuote,
    FundNav,
    HoldingVersion,
    TaskRun,
    UserFundPosition,
    UserFundPositionEvent,
)
from .onboarding import ensure_fund_full_onboarded

logger = logging.getLogger(__name__)


def _max_trade_date(session: Session, model, *where_clauses) -> date | None:
    return session.scalar(select(func.max(model.trade_date)).where(*where_clauses))


def _next_day(day: date) -> date:
    return day + timedelta(days=1)


def roll_forward_positions_by_actual_returns(session: Session, today: date | None = None) -> int:
    """用已公布实际涨跌滚动持仓金额, 每个基金每日只更新一次。"""
    today = today or date.today()
    positions = session.scalars(
        select(UserFundPosition).where(UserFundPosition.is_active.is_(True))
    ).all()
    updated = 0
    for pos in positions:
        amount = float(pos.holding_amount or 0.0)
        if amount <= 0:
            continue
        actual = session.scalar(
            select(ActualReturn)
            .where(
                ActualReturn.fund_code == pos.fund_code,
                ActualReturn.trade_date < today,
            )
            .order_by(ActualReturn.trade_date.desc())
        )
        if actual is None:
            continue
        existed = session.scalar(
            select(UserFundPositionEvent).where(
                UserFundPositionEvent.fund_code == pos.fund_code,
                UserFundPositionEvent.event_type == "nav_rollover",
                UserFundPositionEvent.trade_date == actual.trade_date,
            )
        )
        if existed is not None:
            continue
        pending_delta = session.scalar(
            select(func.coalesce(func.sum(UserFundPositionEvent.amount_delta), 0.0)).where(
                UserFundPositionEvent.fund_code == pos.fund_code,
                UserFundPositionEvent.trade_date == actual.trade_date,
                UserFundPositionEvent.effective_date.is_not(None),
                UserFundPositionEvent.effective_date > actual.trade_date,
            )
        ) or 0.0
        base_amount = max(amount - float(pending_delta), 0.0)
        profit = round(base_amount * actual.actual_return, 2)
        if abs(profit) < 0.005:
            continue
        pos.holding_amount = round(amount + profit, 2)
        session.add(
            UserFundPositionEvent(
                fund_code=pos.fund_code,
                event_type="nav_rollover",
                amount_delta=profit,
                share_delta=None,
                nav=None,
                trade_date=actual.trade_date,
                effective_date=today,
                source="system",
                raw_text="",
                image_path="",
                note=f"按 {actual.trade_date} 实际涨跌 {actual.actual_return * 100:+.2f}% 更新持有金额",
            )
        )
        updated += 1
    if updated:
        session.commit()
    return updated


def update_task_progress(session: Session, task_id: int, status: str, progress_text: str = "", error_message: str = ""):
    task = session.get(TaskRun, task_id)
    if task:
        task.status = status
        if progress_text:
            task.progress_text = progress_text
        if error_message:
            task.error_message = error_message
        if status in ["success", "failed"]:
            task.finished_at = datetime.now(timezone.utc)
        session.commit()


def async_onboard_new_fund(fund_code: str, task_id: int):
    """
    后台任务：拉取基础信息、最新公开持仓、资产配置、并回填过去一段时间的净值和日K，
    最后进行因果校准。
    """
    session_factory = get_session_factory()
    data_source = AKShareDataSource()
    with session_factory() as session:
        try:
            update_task_progress(session, task_id, "running", "正在拉取基金基础信息和公开持仓")
            result = ensure_fund_full_onboarded(
                session=session,
                fund_code=fund_code,
                data_source=data_source,
                force_rebuild=True,
            )
            
            if result.get("status") == "missing_holdings":
                update_task_progress(
                    session, task_id, "success", "建档完成，但缺少公开持仓，请手动补充",
                    error_message="、".join(result.get("warnings", []))
                )
            else:
                update_task_progress(session, task_id, "success", "估值准备完毕")
                
        except Exception as exc:
            logger.error("async_onboard_new_fund error for %s: %s", fund_code, exc)
            update_task_progress(session, task_id, "failed", "同步失败", error_message=format_exc())


def sync_daily_all_funds(task_id: int):
    """
    后台任务：对 active 基金进行增量同步（净值、日K），并增量滚动校准
    """
    session_factory = get_session_factory()
    data_source = AKShareDataSource()
    today = date.today()
    
    with session_factory() as session:
        try:
            update_task_progress(session, task_id, "running", "正在获取需要同步的基金列表")
            
            from .models import Fund
            funds = session.scalars(select(Fund).where(Fund.is_active.is_(True))).all()
            total = len(funds)
            
            # 批量获取最新净值；避免每只基金再单独爬东财历史净值。
            update_task_progress(session, task_id, "running", "正在批量拉取最新公募基金和ETF净值...")
            from .backfill import fetch_and_store_bulk_navs
            active_codes = {f.fund_code for f in funds}
            try:
                fetch_and_store_bulk_navs(session, data_source, active_codes)
            except Exception as exc:
                logger.warning("bulk nav fetch failed: %s", exc)
            
            for i, fund in enumerate(funds, 1):
                fund_code = fund.fund_code
                update_task_progress(session, task_id, "running", f"({i}/{total}) 检查 {fund_code} 增量数据")
                
                # 只处理尚未生成残差的新真实净值日。
                latest_actual_date = _max_trade_date(
                    session, ActualReturn, ActualReturn.fund_code == fund_code
                )
                latest_nav_date = _max_trade_date(
                    session, FundNav, FundNav.fund_code == fund_code
                )
                if latest_actual_date is None:
                    logger.info("sync_daily_all_funds skip %s: no actual_return", fund_code)
                    continue

                active_holding = session.scalar(
                    select(HoldingVersion)
                    .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
                    .order_by(HoldingVersion.report_date.desc())
                )
                if active_holding is None:
                    logger.info("sync_daily_all_funds skip %s: no active holding", fund_code)
                    continue

                latest_residual_date = _max_trade_date(
                    session,
                    CalibrationResidual,
                    CalibrationResidual.fund_code == fund_code,
                    CalibrationResidual.holding_version_id == active_holding.id,
                )
                if latest_residual_date and latest_actual_date <= latest_residual_date:
                    logger.info(
                        "sync_daily_all_funds skip %s: already calibrated to %s",
                        fund_code,
                        latest_residual_date,
                    )
                    continue

                start_date = max(
                    active_holding.report_date,
                    _next_day(latest_residual_date) if latest_residual_date else active_holding.report_date,
                )
                end_date = latest_actual_date
                if start_date > end_date:
                    continue

                update_task_progress(
                    session,
                    task_id,
                    "running",
                    f"({i}/{total}) 同步 {fund_code} {start_date}~{end_date} 行情并校准",
                )

                from .backfill import fetch_and_store_stock_quotes
                asset_codes = [item.asset_code for item in active_holding.items]
                if asset_codes:
                    latest_quote_count = session.scalar(
                        select(func.count(func.distinct(DailyQuote.asset_code))).where(
                            DailyQuote.asset_code.in_(asset_codes),
                            DailyQuote.trade_date == end_date,
                        )
                    ) or 0
                    if latest_quote_count < len(set(asset_codes)):
                        try:
                            fetch_and_store_stock_quotes(
                                session, data_source, start_date, end_date, asset_codes
                            )
                        except Exception as exc:
                            logger.warning("sync_daily_all_funds quote error for %s: %s", fund_code, exc)

                from .calibration import run_incremental_calibration
                calibrated = run_incremental_calibration(session, fund_code)
                logger.info(
                    "sync_daily_all_funds calibrated %s: %s rows, latest_nav=%s",
                    fund_code,
                    calibrated,
                    latest_nav_date,
                )

            rolled = roll_forward_positions_by_actual_returns(session, today=today)
            logger.info("sync_daily_all_funds rolled forward positions: %s", rolled)
            
            update_task_progress(session, task_id, "success", "每日数据同步和校准完成")
            
        except Exception as exc:
            logger.error("sync_daily_all_funds error: %s", exc)
            update_task_progress(session, task_id, "failed", "每日同步失败", error_message=format_exc())
