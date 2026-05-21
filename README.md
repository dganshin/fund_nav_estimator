# fund_nav_estimator

个人使用的基金盘中涨跌预计修正系统, 当前为阶段 0 骨架版。

本阶段只完成:

- 基金池与持仓版本数据结构
- 日频底层资产涨跌导入
- 基于持仓权重的基金原始估算 `raw_estimate`
- 官方真实涨跌导入
- 误差表落库

本阶段不包含:

- 买卖规则
- 自动交易
- 组合仓位管理
- 实时行情 API
- 机器学习校准

## 目录结构

```text
fund_nav_estimator/
  README.md
  requirements.txt
  config/
    fund_pool.example.yaml
  data/
    example_holdings.csv
    example_quotes.csv
    example_actual_returns.csv
  src/
    __init__.py
    db.py
    models.py
    init_db.py
    import_data.py
    estimator.py
    main.py
```

## 环境要求

- Python 3.10+

## 安装依赖

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 初始化数据库

默认会在 `data/fund_nav_estimator.db` 创建 SQLite 数据库。

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
python3 -m src.main init-db
```

如需自定义数据库路径:

```bash
export FUND_NAV_DB_URL="sqlite:////absolute/path/to/fund_nav_estimator.db"
python3 -m src.main init-db
```

## 导入示例数据

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
python3 -m src.main import-funds --yaml config/fund_pool.example.yaml
python3 -m src.main import-holdings --csv data/example_holdings.csv
python3 -m src.main import-quotes --csv data/example_quotes.csv
python3 -m src.main import-actuals --csv data/example_actual_returns.csv
```

## 生成估算与误差

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
python3 -m src.main estimate --trade-date 2026-05-20
python3 -m src.main reconcile --trade-date 2026-05-20
```

说明:

- `raw_estimate = sum(weight / 100 * return_pct)`
- `covered_weight` 表示当日有行情的持仓权重和
- `missing_weight = total_weight - covered_weight`
- `error = actual_return - raw_estimate`

## 一键演示

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
python3 -m src.main demo-run --trade-date 2026-05-20
```

## 后续扩展建议

- `src/models.py` 已按实体拆表, 方便后续接 FastAPI
- `src/import_data.py` 可继续扩展为行情抓取和文件批量导入
- `src/estimator.py` 可继续加入误差校准和多因子修正

