# fund_nav_estimator

个人使用的基金盘中涨跌预计修正系统, 当前为阶段 2。

本阶段已完成:

- 手动导入基金列表
- 手动导入基金持仓版本
- 手动导入股票当日涨跌
- 计算基金原始估算涨跌 `raw_estimate`
- 导入真实涨跌 `actual_return_pct`
- 导入基金净值 `unit_nav`
- 从连续两个交易日 `unit_nav` 自动生成 `actual_return`
- `reconcile` 生成误差记录
- `stats` 输出历史误差统计

本阶段仍不包含:

- 校准模型
- `calibrated_estimate`
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
    example_fund_navs.csv
    example_funds.csv
    example_holdings.csv
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
python3 src/main.py estimate --trade-date 2026-05-21
python3 src/main.py reconcile --trade-date 2026-05-21
python3 src/main.py stats
python3 src/main.py stats --fund-code 000001
python3 src/main.py stats --window 20
python3 src/main.py stats --fund-code 000001 --window 20
python3 src/main.py demo-run --trade-date 2026-05-21
```

## 阶段 2 数据表

在阶段 1 基础上新增:

- `fund_navs`

字段:

- `trade_date`
- `fund_code`
- `unit_nav`
- `accumulated_nav`
- `source`
- `created_at`

说明:

- `actual_returns` 仍然保留, 作为 `reconcile` 的最终真实涨跌来源
- 从 `fund_navs` 计算出的真实涨跌, 会自动回填到 `actual_returns`

## CSV 字段说明

### 基金列表 CSV

文件示例: `data/example_funds.csv`

- `fund_code`: 基金代码, 按字符串处理
- `fund_name`: 基金名称
- `fund_type`: 基金类型
- `market`: 市场标识
- `is_active`: 是否启用, 支持 `true/false/1/0`

### 基金持仓 CSV

文件示例: `data/example_holdings.csv`

- `fund_code`: 基金代码, 按字符串处理
- `report_date`: 持仓披露日期, 格式 `YYYY-MM-DD`
- `source`: 数据来源
- `asset_code`: 资产代码, 按字符串处理
- `asset_name`: 资产名称
- `asset_type`: 资产类型
- `weight_pct`: 百分数输入, 例如 `9.5` 表示 9.5%

### 行情 CSV

文件示例: `data/example_quotes.csv`

- `trade_date`: 交易日, 格式 `YYYY-MM-DD`
- `asset_code`: 资产代码, 按字符串处理
- `asset_name`: 资产名称
- `return_pct`: 百分数输入, 例如 `2.0` 表示 +2.0%
- `source`: 数据来源

### 真实涨跌 CSV

文件示例: `data/example_actual_returns.csv`

- `trade_date`: 交易日
- `fund_code`: 基金代码
- `actual_return_pct`: 百分数输入, 例如 `0.62` 表示 +0.62%
- `source`: 数据来源

说明:

- 为兼容旧数据, 也接受字段名 `actual_return`
- 导入后统一转成内部小数, 例如 `0.62 -> 0.0062`

### 基金净值 CSV

文件示例: `data/example_fund_navs.csv`

- `trade_date`: 交易日
- `fund_code`: 基金代码
- `unit_nav`: 单位净值
- `accumulated_nav`: 累计净值, 可为空
- `source`: 数据来源

## 百分数输入与内部计算规则

导入时:

- `weight_pct=9.5` 会入库为 `0.095`
- `return_pct=2.0` 会入库为 `0.020`
- `actual_return_pct=0.62` 会入库为 `0.0062`

估算时:

```text
raw_estimate = Σ(weight_i × return_i)
```

其中:

- `weight_i` 是内部小数
- `return_i` 是内部小数
- `raw_estimate` 也是内部小数

展示时统一转为百分比:

- `0.012` 显示为 `+1.20%`

## 如何导入真实涨跌

直接导入真实涨跌:

```bash
python3 src/main.py import-actuals --csv data/example_actual_returns.csv
```

如果 `actual_return_pct` 的绝对值超过 20%, 会给 warning, 但仍允许导入。

## 如何导入基金净值

导入净值:

```bash
python3 src/main.py import-navs --csv data/example_fund_navs.csv
```

自动计算逻辑:

```text
actual_return = unit_nav_today / unit_nav_previous_trade_day - 1
```

说明:

- 通过净值生成的真实涨跌会自动写入 `actual_returns`
- 如果缺少前一交易日净值, 不会中断导入, 但会给出 warning
- `unit_nav <= 0` 会直接拒绝导入

## 估算输出说明

`estimate` 会输出:

- `原始估值`: `raw_estimate`
- `覆盖权重`: `covered_weight`
- `缺失权重`: `missing_weight`
- `缺失资产`: 当前持仓里缺少行情的资产列表
- `warning`: 缺行情提醒

## reconcile

执行:

```bash
python3 src/main.py reconcile --trade-date 2026-05-21
```

计算逻辑:

```text
error = actual_return - raw_estimate
abs_error = abs(error)
```

`direction_hit` 规则:

- `raw_estimate > 0` 且 `actual_return > 0` -> 命中
- `raw_estimate < 0` 且 `actual_return < 0` -> 命中
- 两者都等于 `0` -> 命中
- 一个为 `0`, 另一个非 `0` -> 不命中

`reconcile` 特性:

- 缺少 `actual_return` 时不会整体失败
- 同一 `trade_date + fund_code` 重复运行会覆盖更新
- 会输出结果表和 warning

输出字段含义:

- `error`: 真实涨跌减去原始估值
- `abs_error`: 误差绝对值
- `direction_hit`: 方向是否命中

## stats

执行:

```bash
python3 src/main.py stats
python3 src/main.py stats --fund-code 000001
python3 src/main.py stats --window 20
```

统计字段:

- `sample_count`: 样本数
- `mean_error`: 平均误差
- `mean_abs_error`: 平均绝对误差
- `max_abs_error`: 最大绝对误差
- `direction_hit_rate`: 方向命中率
- `estimate_actual_corr`: `raw_estimate` 与 `actual_return` 的相关系数
- `latest_error`: 最近一个交易日误差
- `latest_trade_date`: 最近一个交易日

说明:

- 当样本数不足 2, 或波动为 0 时, `corr` 显示为 `N/A`
- `window` 表示只看最近 N 个样本

## 数据质量检查

当前已支持:

- `actual_return` 绝对值超过 20% 时给 warning
- `unit_nav <= 0` 时拒绝导入
- 同一基金同一日期重复净值导入时幂等
- 缺少前一交易日净值时不给 `actual_return`, 但给清晰 warning
- `trade_date` 必须是 `YYYY-MM-DD`
- `fund_code` 全程按字符串处理, 保留前导零

## 导入与估算幂等性

- `funds` 以 `fund_code` 为主键
- `holding_versions` 以 `fund_code + report_date + source` 唯一定位
- `holding_items` 以 `holding_version_id + asset_code` 唯一约束
- `daily_quotes` 以 `trade_date + asset_code` 为主键
- `fund_navs` 以 `trade_date + fund_code` 为主键
- `actual_returns` 以 `trade_date + fund_code` 为主键
- `fund_estimates` 以 `trade_date + fund_code` 为主键
- `estimate_errors` 以 `trade_date + fund_code` 为主键

## 验证方式

运行测试:

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
pytest
```

运行完整演示:

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
python3 src/main.py demo-run --trade-date 2026-05-21
```

`demo-run` 会自动:

1. 初始化数据库
2. 导入基金
3. 导入持仓
4. 导入行情
5. 导入净值并生成真实涨跌
6. 对示例里的多个交易日计算 `raw_estimate`
7. `reconcile` 生成误差
8. 输出 `stats`

## 下一阶段

阶段 3 再实现:

- rolling calibration
- calibrated_estimate
- 基于历史误差的修正逻辑
