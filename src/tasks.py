import logging
from datetime import date, timedelta, datetime, timezone
from traceback import format_exc

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calibration import run_online_calibration
from .data_sources import AKShareDataSource
from .db import get_session_factory
from .models import ActualReturn, CalibrationResidual, HoldingVersion, TaskRun
from .onboarding import ensure_fund_full_onboarded

logger = logging.getLogger(__name__)


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
            
            # 批量获取最新净值
            update_task_progress(session, task_id, "running", "正在批量拉取最新公募基金和ETF净值...")
            from .backfill import fetch_and_store_bulk_navs
            active_codes = {f.fund_code for f in funds}
            try:
                fetch_and_store_bulk_navs(session, data_source, active_codes)
            except Exception as exc:
                logger.warning("bulk nav fetch failed: %s", exc)
            
            for i, fund in enumerate(funds, 1):
                fund_code = fund.fund_code
                update_task_progress(session, task_id, "running", f"({i}/{total}) 正在同步 {fund_code} 行情并校准")
                
                # 增量拉取净值和行情
                # 利用 ensure_fund_full_onboarded 的幂等性，但只拉取缺失日期
                from .onboarding import _latest_nav
                from .backfill import fetch_and_store_fund_navs, fetch_and_store_stock_quotes
                
                latest_nav = _latest_nav(session, fund_code)
                # 最多往前追溯30天，或基于最新有净值的日期
                start_date = latest_nav.trade_date if latest_nav else (today - timedelta(days=30))
                
                try:
                    fetch_and_store_fund_navs(session, data_source, fund_code, start_date, today)
                except Exception as exc:
                    logger.warning("sync_daily_all_funds nav error for %s: %s", fund_code, exc)
                
                # 找到当前 active 的 holding_version 拉取成分股行情
                active_holding = session.scalar(
                    select(HoldingVersion)
                    .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
                    .order_by(HoldingVersion.report_date.desc())
                )
                if active_holding:
                    asset_codes = [item.asset_code for item in active_holding.items]
                    if asset_codes:
                        try:
                            fetch_and_store_stock_quotes(session, data_source, start_date, today, asset_codes)
                        except Exception as exc:
                            logger.warning("sync_daily_all_funds quote error for %s: %s", fund_code, exc)
                
                # 增量校准
                from .calibration import run_incremental_calibration
                run_incremental_calibration(session, fund_code)
            
            update_task_progress(session, task_id, "success", "每日数据同步和校准完成")
            
        except Exception as exc:
            logger.error("sync_daily_all_funds error: %s", exc)
            update_task_progress(session, task_id, "failed", "每日同步失败", error_message=format_exc())
