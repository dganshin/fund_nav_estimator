# 基金实时估值

个人版养基宝增强估值工具：用修正权重 `effective_weight` 和实时股票涨跌，估算基金今日涨跌与我的今日盈亏。

## 1. 当前主入口

```bash
cd /Users/jiangxing/Documents/repo/fund_nav_estimator
source .venv/bin/activate
uvicorn src.frontend_app:app --reload --host 127.0.0.1 --port 8502
```

打开浏览器：`http://127.0.0.1:8502/`

| 页面 | 地址 | 用途 |
|------|------|------|
| 首页实时估值榜 | `/` | 看所有基金今日估值 |
| 基金详情 | `/fund/{code}` | 看持仓贡献明细 |
| 我的持仓 | `/portfolio` | 录入持有金额、自选 |
| 管理页 | `/manage` | 基金/持仓/资产配置/修正权重 CRUD |

> Streamlit (`src/web_app.py`) 仅用于后台调试，不是日常入口。

## 2. 核心功能

- 首页实时估值榜（自动局部刷新，不打断搜索框输入）
- 今日估算盈亏（基于持有金额）
- 基金详情持仓贡献（按贡献绝对值排序）
- 完整管理 CRUD：基金、持仓版本、资产配置、修正权重
- 修正权重 `effective_weight`（基于资产配置放大持仓覆盖率）
- 每日真实净值校准（后台调试）

## 3. 核心公式

```text
published_weight  →  effective_weight（修正后）

final_estimate = Σ effective_weight_i × live_return_i

estimated_today_profit = holding_amount × final_estimate
```

## 4. 快速启动

```bash
# 1. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化数据库
python3 src/main.py init-db

# 4. 启动前台
uvicorn src.frontend_app:app --reload --host 127.0.0.1 --port 8502
```

## 5. 如何添加一只基金

通过前台 `/manage` → **基金管理** Tab：

1. 填写基金代码（保留前导零，如 `002207`）、名称、类型
2. 切到 **持仓管理** Tab → 填写前十大持仓（股票代码、名称、权重%）→ 保存
3. 切到 **资产配置** Tab → 填写股票仓位% → 保存
4. 切到 **修正权重** Tab → 选择基金和日期 → 生成修正权重
5. 回首页刷新，即可看到实时估值

## 6. 如何录入我的持仓

通过前台 `/portfolio`：

- 选择基金
- 填入**持有金额**（元）
- 可选：持有份额、成本净值、平台
- 保存后首页自动显示**今日估算盈亏**

## 7. 数据口径

| 字段 | 说明 |
|------|------|
| 今日估值日期 | 本地今天（自动，不可手动选历史日期） |
| 行情时间 | 实时抓取时间（非交易时段回退到最近收盘缓存） |
| 最新真实净值日 | 基金官方净值最新日期 |
| 持仓报告日 | active holding version 的 `report_date` |
| 置信度 A/B/C/D | 基于历史样本数和误差评估 |

## 8. 后台调试

### Streamlit（历史分析可视化）

```bash
streamlit run src/web_app.py
```

### 常用 CLI 命令

```bash
# 回填历史数据（抓净值、抓行情、估值、校准）
python3 src/main.py backfill-history --fund-code 002207 \
  --start-date 2026-04-01 --end-date 2026-05-21 \
  --window 20 --base coverage_adjusted

# 抓基金历史净值
python3 src/main.py fetch-fund-navs --fund-code 002207 \
  --start-date 2026-04-01 --end-date 2026-05-21

# 抓股票历史行情（从 active 持仓）
python3 src/main.py fetch-stock-quotes \
  --from-active-holdings --fund-code 002207 \
  --start-date 2026-04-01 --end-date 2026-05-21

# 查看估值选择统计
python3 src/main.py selected-stats --fund-code 002207 \
  --start-date 2026-04-22 --end-date 2026-05-20 --selection-window 20

# 比较三种估值方法
python3 src/main.py compare-estimates --fund-code 002207 \
  --start-date 2026-04-22 --end-date 2026-05-20 \
  --window 20 --base coverage_adjusted
```

### 数据库

默认路径：`data/fund_nav_estimator.db`

```bash
# 自定义数据库路径
export FUND_NAV_DB_URL="sqlite:////absolute/path/to/fund_nav_estimator.db"
python3 src/main.py init-db
```

## 9. 当前限制

- 估值仅供参考，不做买卖建议
- 不做自动交易，不预测股票
- 依赖公开持仓（前十大）和实时行情源（AKShare）
- 主动基金调仓会影响估值准确度
- 非交易时段行情回退到最近收盘缓存
- 当前不支持 QDII、港股、ETF

## 10. 测试

```bash
pytest
```

主要测试文件：

| 文件 | 覆盖内容 |
|------|---------|
| `test_frontend_app.py` | API 接口、前台路由、持仓计算 |
| `test_stage4.py` | 估值引擎 |
| `test_stage5.py` | best_estimate 选择策略 |
| `test_stage6_web.py` | Web 服务层 |
