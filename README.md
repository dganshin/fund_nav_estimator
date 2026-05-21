# fund_nav_estimator

个人使用的基金盘中涨跌预计修正系统, 当前为阶段 3。

本阶段已完成:

- 原始估值 `raw_estimate`
- 真实涨跌回填 `actual_returns`
- 基金净值导入 `fund_navs`
- 误差记录 `reconcile`
- 历史误差统计 `stats`
- 滚动线性校准 `calibrated_estimate`
- 覆盖率修正 `coverage_adjusted_estimate`
- 资产配置导入
- 行业配置导入
- 历史批量校准
- 校准效果统计

本阶段仍不包含:

- 实时行情 API
- OCR
- QDII
- 买卖规则
- Web 前端
- 自动交易

## 目录结构

```text
fund_nav_estimator/
  README.md
  requirements.txt
  config/
    fund_pool.example.yaml
  data/
    example_actual_returns.csv
    example_asset_allocations.csv
    example_fund_navs.csv
    example_funds.csv
    example_holdings.csv
    example_industry_allocations.csv
    example_quotes.csv
  src/
    __init__.py
    db.py
    models.py
    init_db.py
    import_data.py
    estimator.py
    main.py
  tests/
    test_stage1.py
    test_stage2.py
    test_stage3.py
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

默认数据库路径:

- `data/fund_nav_estimator.db`

初始化命令:

```bash
python3 src/main.py init-db
```

如需自定义数据库路径:

```bash
export FUND_NAV_DB_URL="sqlite:////absolute/path/to/fund_nav_estimator.db"
python3 src/main.py init-db
```

## 当前可用命令

```bash
python3 src/main.py init-db
python3 src/main.py import-funds --csv data/example_funds.csv
python3 src/main.py import-funds --yaml config/fund_pool.example.yaml
python3 src/main.py import-holdings --csv data/example_holdings.csv
python3 src/main.py import-quotes --csv data/example_quotes.csv
python3 src/main.py import-actuals --csv data/example_actual_returns.csv
python3 src/main.py import-navs --csv data/example_fund_navs.csv
python3 src/main.py import-asset-allocation --csv data/example_asset_allocations.csv
python3 src/main.py import-industry-allocation --csv data/example_industry_allocations.csv
python3 src/main.py estimate --trade-date 2026-05-21
python3 src/main.py reconcile --trade-date 2026-05-21
python3 src/main.py stats
python3 src/main.py stats --fund-code 000001
python3 src/main.py stats --window 20
python3 src/main.py calibrate --trade-date 2026-05-21
python3 src/main.py calibrate --trade-date 2026-05-21 --window 20
python3 src/main.py calibrate --trade-date 2026-05-21 --base coverage_adjusted
python3 src/main.py calibrate-history --start-date 2026-05-16 --end-date 2026-05-21 --window 5 --base coverage_adjusted
python3 src/main.py calibration-stats
python3 src/main.py calibration-stats --fund-code 000001
python3 src/main.py calibration-stats --window 20 --base raw
python3 src/main.py demo-run --trade-date 2026-05-21
```

## 阶段 3 数据表

新增表:

- `fund_asset_allocations`
- `fund_industry_allocations`
- `calibrated_estimates`

保留并继续使用:

- `funds`
- `holding_versions`
- `holding_items`
- `daily_quotes`
- `fund_estimates`
- `actual_returns`
- `fund_navs`
- `estimate_errors`

## CSV 字段说明

### 基金列表

文件:

- `data/example_funds.csv`

字段:

- `fund_code`
- `fund_name`
- `fund_type`
- `market`
- `is_active`

### 基金持仓

文件:

- `data/example_holdings.csv`

字段:

- `fund_code`
- `report_date`
- `source`
- `asset_code`
- `asset_name`
- `asset_type`
- `weight_pct`

### 行情

文件:

- `data/example_quotes.csv`

字段:

- `trade_date`
- `asset_code`
- `asset_name`
- `return_pct`
- `source`

### 真实涨跌

文件:

- `data/example_actual_returns.csv`

字段:

- `trade_date`
- `fund_code`
- `actual_return_pct`
- `source`

说明:

- 百分数输入, `0.62` 表示 `+0.62%`
- 入库后转成内部小数 `0.0062`

### 基金净值

文件:

- `data/example_fund_navs.csv`

字段:

- `trade_date`
- `fund_code`
- `unit_nav`
- `accumulated_nav`
- `source`

自动计算:

```text
actual_return = unit_nav_today / unit_nav_previous_trade_day - 1
```

### 资产配置

文件:

- `data/example_asset_allocations.csv`

字段:

- `fund_code`
- `report_date`
- `source`
- `stock_weight_pct`
- `bond_weight_pct`
- `cash_weight_pct`
- `other_weight_pct`

说明:

- 百分数字段统一转成内部小数
- 空值按 `0` 处理
- 同一基金导入更晚版本后, 新版本默认 `active`, 旧版本 `inactive`

### 行业配置

文件:

- `data/example_industry_allocations.csv`

字段:

- `fund_code`
- `report_date`
- `source`
- `industry_name`
- `industry_code`
- `weight_pct`

说明:

- 本阶段只导入和保存
- 后续阶段可作为行业代理估值的基础数据

## 百分数输入与内部计算

导入时:

- `weight_pct=9.5` -> `0.095`
- `return_pct=2.0` -> `0.020`
- `actual_return_pct=0.62` -> `0.0062`
- `stock_weight_pct=90.80` -> `0.9080`

展示时:

- 所有内部小数统一转回百分比输出

## 原始估值与覆盖率修正

### raw_estimate

公式:

```text
raw_estimate = Σ(weight_i × return_i)
```

其中:

- `weight_i` 是持仓权重的小数
- `return_i` 是资产当日涨跌幅的小数

### coverage_adjusted_estimate

目的:

- 解决前十大持仓只覆盖部分股票仓位的问题

公式:

```text
coverage_adjusted_estimate = raw_estimate / covered_weight * target_equity_weight
```

其中:

- `covered_weight` 是有行情覆盖的持仓权重
- `target_equity_weight` 通常来自 `fund_asset_allocations.stock_weight`
- 如果没有资产配置, 或 `covered_weight <= 0`, 则退化为 `raw_estimate`

假设与风险:

- 假设未知股票部分与已知持仓整体同方向、同弹性
- 这是启发式修正, 不是最终真实估值
- 后续可以用行业代理指数替代这个简单假设

## rolling calibration

### 目的

- 利用历史 `raw_estimate` 与 `actual_return` 的关系, 修正当日估值偏差

### 模型

```text
actual_return = alpha + beta * estimate + epsilon
```

其中:

- `estimate` 可以是 `raw_estimate`
- 也可以是 `coverage_adjusted_estimate`

### 为什么不能用当天或未来数据训练

- 如果在 `2026-05-21` 计算校准结果时使用了 `2026-05-21` 当天真实涨跌, 就会产生 look-ahead bias
- 因此训练样本只能使用 `trade_date` 之前的历史数据

### 参数含义

- `alpha`: 截距
- `beta`: 斜率
- `window`: 回看样本窗口
- `sample_count`: 实际可用训练样本数

### 样本不足时为什么退化为 raw_estimate

- 样本太少时线性回归不稳定
- 本项目默认 `min_samples = 5`
- 当 `sample_count < min_samples` 时:
  - `calibrated_estimate = raw_estimate`
  - `alpha = 0`
  - `beta = 1`
  - `model_status = insufficient_samples`

## reconcile

运行:

```bash
python3 src/main.py reconcile --trade-date 2026-05-21
```

核心字段:

- `error = actual_return - raw_estimate`
- `abs_error = abs(error)`
- `direction_hit`: 方向是否一致

规则:

- 同时大于 `0` -> 命中
- 同时小于 `0` -> 命中
- 同时等于 `0` -> 命中
- 一个为 `0` 一个非 `0` -> 不命中

## stats

运行:

```bash
python3 src/main.py stats
python3 src/main.py stats --fund-code 000001
python3 src/main.py stats --window 20
```

输出指标:

- `sample_count`
- `mean_error`
- `mean_abs_error`
- `max_abs_error`
- `direction_hit_rate`
- `estimate_actual_corr`
- `latest_error`
- `latest_trade_date`

## calibrate

运行:

```bash
python3 src/main.py calibrate --trade-date 2026-05-21
python3 src/main.py calibrate --trade-date 2026-05-21 --window 20
python3 src/main.py calibrate --trade-date 2026-05-21 --base coverage_adjusted
```

输出字段:

- `raw_estimate`
- `coverage_adjusted_estimate`
- `calibrated_estimate`
- `alpha`
- `beta`
- `sample_count`
- `window`
- `train_start_date`
- `train_end_date`
- `mean_abs_error`
- `direction_hit_rate`
- `model_status`
- `confidence_level`

## calibrate-history

运行:

```bash
python3 src/main.py calibrate-history --start-date 2026-05-16 --end-date 2026-05-21 --window 5 --base coverage_adjusted
```

说明:

- 会按日期顺序逐天生成 `calibrated_estimates`
- 每一天只能使用该日期之前的历史数据
- 可重复运行, 不会产生重复脏数据

## calibration-stats

运行:

```bash
python3 src/main.py calibration-stats
python3 src/main.py calibration-stats --fund-code 000001
python3 src/main.py calibration-stats --window 20 --base raw
```

比较指标:

- `raw_mean_abs_error`
- `calibrated_mean_abs_error`
- `improvement_pct`
- `raw_direction_hit_rate`
- `calibrated_direction_hit_rate`
- `raw_corr`
- `calibrated_corr`

其中:

```text
improvement_pct = (raw_mae - calibrated_mae) / raw_mae
```

## confidence_level

规则:

- `A`
  - `sample_count >= 20`
  - `mean_abs_error <= 0.003`
  - `direction_hit_rate >= 0.75`
  - `estimate_actual_corr >= 0.70`
- `B`
  - `sample_count >= 10`
  - `mean_abs_error <= 0.006`
  - `direction_hit_rate >= 0.65`
- `C`
  - `sample_count >= 5`
- `D`
  - `sample_count < 5` 或模型异常

## 数据质量检查

当前已支持:

- `actual_return` 绝对值超过 `20%` 时给 warning
- `unit_nav <= 0` 时拒绝导入
- 缺少前一交易日净值时不给 `actual_return`, 但给 warning
- `trade_date` 必须是 `YYYY-MM-DD`
- `fund_code` 和 `asset_code` 全程按字符串处理, 保留前导零

## demo-run

运行:

```bash
python3 src/main.py demo-run --trade-date 2026-05-21
```

会自动执行:

1. 初始化数据库
2. 导入基金
3. 导入持仓
4. 导入行情
5. 导入资产配置
6. 导入行业配置
7. 导入净值并生成真实涨跌
8. 生成 `raw_estimate`
9. 生成 `estimate_errors`
10. 生成 `calibrated_estimates`
11. 输出 `stats`
12. 输出 `calibration-stats`

`demo-run` 可重复执行。

## 测试

运行:

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
pytest
```

## 下一阶段

阶段 4 可以继续做:

- 更丰富的代理指数估值
- 行业配置参与估值
- 更稳健的 rolling calibration
- 真实数据抓取模块
