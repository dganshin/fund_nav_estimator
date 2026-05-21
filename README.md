# fund_nav_estimator

个人使用的基金盘中涨跌预计修正系统, 当前为阶段 5, 并新增了本地 Web 录入台。

## 阶段 4 目标

- 保留阶段 1 到阶段 3 的手动 CSV 导入和估值链路
- 新增 `data_sources` 抽象层
- 优先接入 AKShare 作为第一版真实数据源
- 自动抓取基金历史净值并回填 `fund_navs` 和 `actual_returns`
- 自动抓取 active holdings 的股票历史日涨跌并回填 `daily_quotes`
- 支持 `estimate-history`、`reconcile-history`、`backfill-history`
- 在真实历史数据上比较 `raw_estimate`、`coverage_adjusted_estimate`、`calibrated_estimate`
- 支持本地 Streamlit Web 页面, 可以直接在表格中维护基金池、持仓、资产配置、行业配置

当前仍不包含:

- 秒级实时行情
- OCR
- QDII
- 买卖规则
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
    backfill.py
    data_sources/
      __init__.py
      akshare_source.py
      base.py
      code_utils.py
    db.py
    estimator.py
    import_data.py
    init_db.py
    main.py
    models.py
    web_app.py
    web_services.py
  tests/
    test_stage1.py
    test_stage3.py
    test_stage2.py
    test_stage4.py
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

## 启动本地 Web

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
source .venv/bin/activate
streamlit run src/web_app.py
```

Web 页面当前支持:

- 直接在表格里录入和修改 `funds`
- 直接在表格里录入和修改 active `holdings`
- 直接在表格里录入和修改 active `asset_allocations`
- 直接在表格里录入和修改 active `industry_allocations`
- 点击按钮抓基金净值、抓股票行情、一键执行 `backfill-history`
- 页面内查看 `stats`、`compare-estimates`、`calibration-stats`、`selected-stats`

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
python3 src/main.py estimate --trade-date 2026-05-21 --fund-code 002207
python3 src/main.py estimate-history --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py estimate-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py reconcile --trade-date 2026-05-21
python3 src/main.py reconcile --trade-date 2026-05-21 --fund-code 002207
python3 src/main.py reconcile-history --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py reconcile-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py stats
python3 src/main.py stats --fund-code 000001
python3 src/main.py stats --window 20
python3 src/main.py stats --fund-code 002207 --start-date 2026-04-22 --end-date 2026-05-20
python3 src/main.py calibrate --trade-date 2026-05-21
python3 src/main.py calibrate --trade-date 2026-05-21 --window 20
python3 src/main.py calibrate --trade-date 2026-05-21 --base coverage_adjusted
python3 src/main.py calibrate-history --start-date 2026-05-16 --end-date 2026-05-21 --window 5 --base coverage_adjusted
python3 src/main.py calibration-stats
python3 src/main.py calibration-stats --fund-code 000001
python3 src/main.py calibration-stats --window 20 --base raw
python3 src/main.py calibration-stats --fund-code 002207 --window 20 --base coverage_adjusted --start-date 2026-04-22 --end-date 2026-05-20
python3 src/main.py compare-estimates --fund-code 002207 --window 20 --base coverage_adjusted
python3 src/main.py compare-estimates --fund-code 002207 --start-date 2026-04-22 --end-date 2026-05-20 --window 20 --base coverage_adjusted
python3 src/main.py select-estimate --trade-date 2026-05-20 --fund-code 002207
python3 src/main.py select-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-20 --selection-window 20
python3 src/main.py selected-stats --fund-code 002207 --start-date 2026-04-22 --end-date 2026-05-20 --selection-window 20
python3 src/main.py fetch-fund-navs --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py fetch-stock-quotes --asset-code 600988.SH --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py fetch-stock-quotes --from-active-holdings --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py backfill-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21 --window 20 --base coverage_adjusted
python3 src/main.py demo-run --trade-date 2026-05-21
streamlit run src/web_app.py
```

## 当前支持的数据源

- `AKShare`

说明:

- 外部接口只封装在 `src/data_sources/`
- 当前优先使用:
  - `fund_open_fund_info_em` 抓开放式基金历史净值
  - `stock_zh_a_hist` 抓 A 股历史日涨跌
- 原始抓取结果会缓存到 `data/raw/akshare/`

## 数据表

阶段 3 新增表:

- `fund_asset_allocations`
- `fund_industry_allocations`
- `calibrated_estimates`

阶段 2 保留并继续使用:

- `funds`
- `holding_versions`
- `holding_items`
- `daily_quotes`
- `fund_estimates`
- `actual_returns`
- `fund_navs`
- `estimate_errors`

阶段 4 没有新增数据库表, 主要新增了数据源层和历史回填命令。

## 核心指标说明

- `raw_estimate`: 只基于已知持仓和已抓到的股票日涨跌计算
- `covered_weight`: 当天有行情覆盖的持仓权重之和
- `missing_weight`: 持仓总权重减去 `covered_weight`
- `coverage_adjusted_estimate`: 用 `raw_estimate / covered_weight * stock_weight` 做启发式放大
- `calibrated_estimate`: 用历史滚动线性回归对 base estimate 做校准后的结果
- `error = actual_return - raw_estimate`
- `abs_error = abs(error)`
- `direction_hit`: 估值方向和真实涨跌方向是否一致
- `mean_abs_error`: 历史绝对误差均值
- `direction_hit_rate`: 历史方向命中率
- `corr`: 估值序列和真实涨跌序列的相关系数

说明:

- 百分数输入统一转内部小数
- 例如 `9.5` 表示 `9.5%`, 内部存储为 `0.095`
- 命令行展示时再转回百分比

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

## 真实数据抓取

### 抓基金历史净值

```bash
python3 src/main.py fetch-fund-navs --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
```

行为:

- 从数据源抓历史 `unit_nav` 和 `accumulated_nav`
- 写入 `fund_navs`
- 自动按前一交易日 `unit_nav` 计算 `actual_return`
- 写入 `actual_returns`
- 如果缺少前一交易日净值, 不会中断, 但该日 `actual_return` 会给 warning

### 抓股票历史日行情

单只股票:

```bash
python3 src/main.py fetch-stock-quotes --asset-code 600988.SH --start-date 2026-04-01 --end-date 2026-05-21
```

从 active holdings 自动抓股票池:

```bash
python3 src/main.py fetch-stock-quotes --from-active-holdings --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
```

行为:

- 自动读取 active holding version 的 `asset_code`
- 抓历史日涨跌幅
- 写入 `daily_quotes`
- 对停牌, 空数据, 接口失败给 warning, 但不会破坏已落库数据

### 股票代码格式

内部统一格式:

- `600988.SH`
- `000975.SZ`
- `688981.SH`

`src/data_sources/code_utils.py` 同时支持转成:

- `600988`
- `000975`
- `sh600988`
- `sz000975`

## 历史批量命令

### estimate-history

```bash
python3 src/main.py estimate-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
```

行为:

- 对区间内已落库行情日期逐日运行 estimate
- 没有 active holding 或没有行情时跳过并给 warning
- 可重复运行

### reconcile-history

```bash
python3 src/main.py reconcile-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
```

行为:

- 对区间内已生成的 `fund_estimates` 逐日运行 reconcile
- 缺少 `actual_return` 时只 warning, 不会整体失败
- 可重复运行

### backfill-history

```bash
python3 src/main.py backfill-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21 --window 20 --base coverage_adjusted
```

执行顺序:

1. `fetch-fund-navs`
2. `fetch-stock-quotes --from-active-holdings`
3. `estimate-history`
4. `reconcile-history`
5. `calibrate-history`
6. `calibration-stats`

适用场景:

- 真实基金历史回填
- 快速检查 `raw` 和 `calibrated` 谁更准

## 002207 真实使用示例

在真实数据模式下, 你只需要先手动导入这些静态资料:

- `funds.csv`
- `holdings.csv`
- `asset_allocations.csv`
- `industry_allocations.csv`

然后执行:

```bash
python3 src/main.py import-funds --csv funds.csv
python3 src/main.py import-holdings --csv holdings.csv
python3 src/main.py import-asset-allocation --csv asset_allocations.csv
python3 src/main.py import-industry-allocation --csv industry_allocations.csv

python3 src/main.py fetch-fund-navs --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py fetch-stock-quotes --from-active-holdings --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py estimate-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py reconcile-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21
python3 src/main.py calibrate-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-21 --window 20 --base coverage_adjusted
python3 src/main.py calibration-stats --fund-code 002207 --window 20 --base coverage_adjusted
python3 src/main.py compare-estimates --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-20 --window 20 --base coverage_adjusted
```

注意:

- 仓库不硬编码真实净值或真实抓取结果
- 真实抓取结果只会写到本地数据库和 `data/raw/akshare/`

## 阶段 4.1 评估口径

### 实用校准口径

- 对 `002207` 使用 `2026-04-01` 之后的数据
- 因为持仓报告期末是 `2026-03-31`
- 适合当前提高估值准确度

### 严格回测口径

- 对 `002207` 使用 `2026-04-22` 之后的数据
- 因为 `2026` 年一季报披露日是 `2026-04-22`
- 适合避免 look-ahead bias

注意:

- 不要用 `2026-03-31` 这版持仓去回填 `2025` 年数据
- 如果 `holding_version.report_date` 晚于回填区间, 系统会跳过该基金并给 warning
- `002207` 的真实回填示例日期统一使用 `2026`

### calibration-stats

`calibration-stats` 现在的 `base_MAE` 会跟 `--base` 绑定:

- `--base raw`: 比较 `raw_estimate` 和 `calibrated_estimate`
- `--base coverage_adjusted`: 比较 `coverage_adjusted_estimate` 和 `calibrated_estimate`

输出字段:

- `base类型`
- `base_MAE`
- `calibrated_MAE`
- `改进比例`
- `base方向命中率`
- `calibrated方向命中率`
- `base_corr`
- `calibrated_corr`

### compare-estimates

`compare-estimates` 会直接比较三种估值:

- `raw_estimate`
- `coverage_adjusted_estimate`
- `calibrated_estimate`

示例:

```bash
python3 src/main.py compare-estimates --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-20 --window 20 --base coverage_adjusted
python3 src/main.py compare-estimates --fund-code 002207 --start-date 2026-04-22 --end-date 2026-05-20 --window 20 --base coverage_adjusted
```

## 阶段 5: best_estimate

为什么不能默认使用 `calibrated_estimate`:

- `calibrated_estimate` 可能在某些区间优于 `coverage_adjusted_estimate`
- 但也可能只是微弱领先, 甚至反而更差
- 因此系统需要一个选择保护层, 而不是默认把回归结果当最终答案

四种估值字段:

- `raw_estimate`: 原始持仓估值
- `coverage_adjusted_estimate`: 覆盖率修正估值
- `calibrated_estimate`: 滚动校准估值
- `best_estimate`: 三者中按历史表现选出的最终估值

### best_method 选择规则

- `selection_window`: 默认 `20`, 只看目标 `trade_date` 之前最近 20 个有效样本
- `min_samples`: 默认 `10`, 样本不足时走 fallback
- `min_improvement_bps`: 默认 `3`, 即 `0.03%`

选择顺序:

- 样本不足时:
  - `coverage_adjusted_estimate` 可用则优先选它
  - 否则退回 `raw_estimate`
- 样本足够时:
  - 先比较 `raw` 和 `coverage_adjusted`
  - 只有当 `coverage_adjusted` 至少好 `3 bps` 才切换
  - 再比较当前最佳 base 方法和 `calibrated`
  - 只有当 `calibrated` 至少好 `3 bps` 才允许切换
- 如果 `calibrated` 只是微弱领先, 会继续保留更简单更稳的 base 方法
- 选择时不能使用当天和未来数据, 避免 look-ahead bias

### select-estimate

```bash
python3 src/main.py select-estimate --trade-date 2026-05-20 --fund-code 002207
python3 src/main.py select-estimate --trade-date 2026-05-20 --fund-code 002207 --selection-window 20
python3 src/main.py select-estimate --trade-date 2026-05-20 --fund-code 002207 --min-samples 10
python3 src/main.py select-estimate --trade-date 2026-05-20 --fund-code 002207 --min-improvement-bps 3
```

输出:

- `raw`
- `coverage`
- `calibrated`
- `best`
- `方法`
- `样本数`
- `raw_MAE`
- `coverage_MAE`
- `calibrated_MAE`
- `置信度`
- `状态`
- `理由`

### select-history

```bash
python3 src/main.py select-history --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-20 --selection-window 20
python3 src/main.py select-history --fund-code 002207 --start-date 2026-04-22 --end-date 2026-05-20 --selection-window 20
```

说明:

- 对区间内每个交易日生成 `selected_estimates`
- 每一天都只使用该日期之前的样本
- 可重复运行, 不会生成重复脏数据

### selected-stats

```bash
python3 src/main.py selected-stats --fund-code 002207 --start-date 2026-04-01 --end-date 2026-05-20 --selection-window 20
python3 src/main.py selected-stats --fund-code 002207 --start-date 2026-04-22 --end-date 2026-05-20 --selection-window 20
```

输出:

- `raw_MAE`
- `coverage_MAE`
- `calibrated_MAE`
- `best_MAE`
- `最优单一方法`
- `best_method分布`
- `best方向命中率`
- `best_corr`

`best_method分布` 示例:

- `coverage_adjusted: 80%, calibrated: 20%`

### backfill-history

`backfill-history` 现在会自动执行:

1. `fetch-fund-navs`
2. `fetch-stock-quotes`
3. `estimate-history`
4. `reconcile-history`
5. `calibrate-history`
6. `select-history`
7. `selected-stats`

最终汇总优先展示:

- `raw_MAE`
- `coverage_MAE`
- `calibrated_MAE`
- `best_MAE`
- `best方法分布`
- `置信等级`

## 接口失败和缺失数据处理

- 网络失败: 抛出清晰错误, 不会删除已有数据库数据
- 字段变化: 会直接输出实际 columns, 便于调试 AKShare 接口变化
- 空返回: 输出 warning, 不写入错误数据
- 批量抓股票时可通过 `--sleep-seconds` 控制节流
- 缺失股票行情时, `raw_estimate` 只按已覆盖部分计算, 不会中断

## demo-run

```bash
python3 src/main.py demo-run --trade-date 2026-05-21
```

说明:

- `demo-run` 仍然只使用仓库内 synthetic 示例数据
- 不依赖真实网络
- 可以重复运行

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
