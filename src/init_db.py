from __future__ import annotations

from sqlalchemy import inspect, text

from .db import get_engine
from .models import Base


def migrate_schema(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "online_calibration_states" in table_names:
        columns = {column["name"] for column in inspector.get_columns("online_calibration_states")}
        with engine.begin() as connection:
            for name, ddl in {
                "beta_known": "ALTER TABLE online_calibration_states ADD COLUMN beta_known FLOAT NOT NULL DEFAULT 1.0",
                "beta_unknown": "ALTER TABLE online_calibration_states ADD COLUMN beta_unknown FLOAT NOT NULL DEFAULT 1.0",
                "alpha": "ALTER TABLE online_calibration_states ADD COLUMN alpha FLOAT NOT NULL DEFAULT 0.0",
            }.items():
                if name not in columns:
                    connection.execute(text(ddl))

    if "calibration_residuals" in table_names:
        columns = {column["name"] for column in inspector.get_columns("calibration_residuals")}
        with engine.begin() as connection:
            for name, ddl in {
                "known_estimate": "ALTER TABLE calibration_residuals ADD COLUMN known_estimate FLOAT NOT NULL DEFAULT 0.0",
                "unknown_estimate": "ALTER TABLE calibration_residuals ADD COLUMN unknown_estimate FLOAT NOT NULL DEFAULT 0.0",
                "base_estimate": "ALTER TABLE calibration_residuals ADD COLUMN base_estimate FLOAT NOT NULL DEFAULT 0.0",
                "calibrated_estimate": "ALTER TABLE calibration_residuals ADD COLUMN calibrated_estimate FLOAT NOT NULL DEFAULT 0.0",
                "beta_known": "ALTER TABLE calibration_residuals ADD COLUMN beta_known FLOAT NOT NULL DEFAULT 1.0",
                "beta_unknown": "ALTER TABLE calibration_residuals ADD COLUMN beta_unknown FLOAT NOT NULL DEFAULT 1.0",
                "alpha": "ALTER TABLE calibration_residuals ADD COLUMN alpha FLOAT NOT NULL DEFAULT 0.0",
                "sample_count": "ALTER TABLE calibration_residuals ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    connection.execute(text(ddl))

    if "fund_estimates" in table_names:
        columns = {column["name"] for column in inspector.get_columns("fund_estimates")}
        if "missing_assets_json" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE fund_estimates "
                        "ADD COLUMN missing_assets_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )

    if "selected_estimates" in table_names:
        columns = {column["name"] for column in inspector.get_columns("selected_estimates")}
        expected_unique_columns = [
            "trade_date",
            "fund_code",
            "holding_version_id",
            "selection_window",
            "selection_policy",
        ]
        has_policy_unique = False
        with engine.connect() as connection:
            for row in connection.execute(text("PRAGMA index_list('selected_estimates')")).fetchall():
                if int(row[2]) != 1:
                    continue
                index_name = row[1]
                index_columns = [
                    index_row[2]
                    for index_row in connection.execute(text(f"PRAGMA index_info('{index_name}')")).fetchall()
                ]
                if index_columns == expected_unique_columns:
                    has_policy_unique = True
                    break
        if "selection_policy" not in columns or not has_policy_unique:
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS selected_estimates_old"))
                connection.execute(text("ALTER TABLE selected_estimates RENAME TO selected_estimates_old"))
                connection.execute(
                    text(
                        """
                        CREATE TABLE selected_estimates (
                            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                            trade_date DATE NOT NULL,
                            fund_code VARCHAR(32) NOT NULL,
                            holding_version_id INTEGER NOT NULL,
                            raw_estimate FLOAT NOT NULL,
                            coverage_adjusted_estimate FLOAT,
                            calibrated_estimate FLOAT,
                            best_estimate FLOAT NOT NULL,
                            best_method VARCHAR(32) NOT NULL,
                            selection_policy VARCHAR(32) NOT NULL DEFAULT 'default',
                            selection_window INTEGER NOT NULL,
                            min_samples INTEGER NOT NULL,
                            min_improvement_bps INTEGER NOT NULL,
                            sample_count INTEGER NOT NULL,
                            raw_mae FLOAT,
                            coverage_adjusted_mae FLOAT,
                            calibrated_mae FLOAT,
                            raw_direction_hit_rate FLOAT,
                            coverage_direction_hit_rate FLOAT,
                            calibrated_direction_hit_rate FLOAT,
                            decision_reason TEXT NOT NULL,
                            confidence_score FLOAT,
                            confidence_level VARCHAR(16),
                            best_status VARCHAR(64) NOT NULL,
                            warning_json TEXT NOT NULL DEFAULT '[]',
                            created_at DATETIME NOT NULL,
                            CONSTRAINT uq_selected_estimate UNIQUE (
                                trade_date,
                                fund_code,
                                holding_version_id,
                                selection_window,
                                selection_policy
                            ),
                            FOREIGN KEY(fund_code) REFERENCES funds (fund_code),
                            FOREIGN KEY(holding_version_id) REFERENCES holding_versions (id)
                        )
                        """
                    )
                )
                if "selection_policy" in columns:
                    selection_policy_expr = "COALESCE(selection_policy, 'default')"
                else:
                    selection_policy_expr = "'default'"
                connection.execute(
                    text(
                        f"""
                        INSERT INTO selected_estimates (
                            id,
                            trade_date,
                            fund_code,
                            holding_version_id,
                            raw_estimate,
                            coverage_adjusted_estimate,
                            calibrated_estimate,
                            best_estimate,
                            best_method,
                            selection_policy,
                            selection_window,
                            min_samples,
                            min_improvement_bps,
                            sample_count,
                            raw_mae,
                            coverage_adjusted_mae,
                            calibrated_mae,
                            raw_direction_hit_rate,
                            coverage_direction_hit_rate,
                            calibrated_direction_hit_rate,
                            decision_reason,
                            confidence_score,
                            confidence_level,
                            best_status,
                            warning_json,
                            created_at
                        )
                        SELECT
                            id,
                            trade_date,
                            fund_code,
                            holding_version_id,
                            raw_estimate,
                            coverage_adjusted_estimate,
                            calibrated_estimate,
                            best_estimate,
                            best_method,
                            {selection_policy_expr},
                            selection_window,
                            min_samples,
                            min_improvement_bps,
                            sample_count,
                            raw_mae,
                            coverage_adjusted_mae,
                            calibrated_mae,
                            raw_direction_hit_rate,
                            coverage_direction_hit_rate,
                            calibrated_direction_hit_rate,
                            decision_reason,
                            confidence_score,
                            confidence_level,
                            best_status,
                            warning_json,
                            created_at
                        FROM selected_estimates_old
                        """
                    )
                )
                connection.execute(text("DROP TABLE selected_estimates_old"))


def init_db(db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    migrate_schema(engine)
