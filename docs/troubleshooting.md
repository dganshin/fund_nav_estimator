# 故障排查手册 (Troubleshooting Runbook)

本文档记录曾经遇到过的数据/估值问题，以及快速诊断和修复步骤。
遇到新问题时先查这里，节省全局扫代码的时间。

---

## 目录

1. [某只基金估值显示 `--` / "不可估"](#1-某只基金估值显示-----不可估)
2. [历史校准残差全是 `+0.00%`（raw_estimate_too_small）](#2-历史校准残差全是-000raw_estimate_too_small)
3. [数据库快速诊断命令](#数据库快速诊断命令)

---

## 1. 某只基金估值显示 `--` / "不可估"

### 症状

- 详情页「今日实时估值」显示 `--`
- 可靠性显示「不可估」或「数据不足」
- 穿透持仓表格中对应股票/ETF 的「涨跌幅」列也显示 `--`
- 其他基金估值正常

### 根本原因分类

| 场景 | 原因 | 出现频率 |
|------|------|---------|
| **A. 错误的持仓版本被激活** | `holding_versions` 里有多个版本，`is_active=True` 的版本是底层穿透版而非直接持仓版，导致用于行情抓取的 `asset_codes` 全错 | 较常见（尤其是联接基金/ETF型基金） |
| B. 行情数据源抓不到该股票 | AKShare / efinance 当日无数据（新股/停牌/节假日） | 少见 |
| C. 估值权重全为 None | 持仓版本的 `weight` 字段全是 None，导致贡献为 0 被过滤 | 见场景A |

---

### ✅ 案例记录：020640 广发中证半导体ETF联接C (2026-05-29)

**现象**：基金详情页「今日实时估值」显示 `--`，昨日（5/28）正常。

**诊断过程**：

```python
# 步骤1：看 load_holding_rows 选出了什么 asset_code
from src.frontend_app import get_cached_session_factory, load_holding_rows
sf = get_cached_session_factory()
with sf() as s:
    rows = load_holding_rows(s, '020640')
    for r in rows:
        print(r['asset_code'], r.get('weight_pct'))
# 输出: ['603078.SH', '688361.SH', ...] ← 底层成分股，weight_pct=None

# 步骤2：验证 ETF 本身行情是否可抓
from src.data_sources.akshare_source import AKShareDataSource
from pathlib import Path
src = AKShareDataSource(raw_dir=Path('data/raw'))
recs = src.fetch_stock_live_quotes(['560780.SH'], sleep_seconds=0, timeout_seconds=10)
print(recs)  # 返回 -3.12% ← 行情正常，说明问题在持仓选择
```

**数据库排查**：

```sql
-- 查 holding_versions
SELECT id, report_date, is_active, source, total_weight
FROM holding_versions
WHERE fund_code = '020640'
ORDER BY report_date DESC, id DESC;

-- 结果:
-- id=15  date=2026-03-31  active=0  source=akshare:target_etf      total_w=0.95  ← ETF本身，被禁用！
-- id=14  date=2026-03-31  active=1  source=akshare:public_holdings  total_w=0.0002 ← 底层成分股，被激活！

-- 查各版本明细
SELECT asset_code, weight FROM holding_items WHERE holding_version_id = 14;
-- 返回: 603078.SH, 688361.SH ... (底层5只股，权重约0)

SELECT asset_code, weight FROM holding_items WHERE holding_version_id = 15;
-- 返回: 560780.SH (半导体设备ETF广发) weight=0.95 ← 正确版本
```

**根本原因**：`id=15`（正确版本，含 ETF 直接持仓）被误设为 `is_active=False`，
`id=14`（穿透持仓版本，底层成分股权重近0）被设为 `is_active=True`。
系统选了错误版本，抓到底层成分股行情但权重为0，估值等于0被过滤显示 `--`。

**修复**：

```sql
-- 修复：激活含 ETF 本身的版本，禁用穿透版本
UPDATE holding_versions SET is_active=1 WHERE id=15 AND fund_code='020640';
UPDATE holding_versions SET is_active=0 WHERE id=14 AND fund_code='020640';
```

```python
# Python 执行方式
from src.frontend_app import get_cached_session_factory
from sqlalchemy import text

sf = get_cached_session_factory()
with sf() as s:
    s.execute(text('UPDATE holding_versions SET is_active=1 WHERE id=:vid'), {'vid': 15})
    s.execute(text('UPDATE holding_versions SET is_active=0 WHERE id=:vid'), {'vid': 14})
    s.commit()
```

**修复后验证**：

```python
with sf() as s:
    rows = load_holding_rows(s, '020640')
    # 应输出: 560780.SH  weight_pct=95.0

# 重启服务器清缓存（或等 LIVE_BUNDLE_TTL=12s 自动过期）
```

**结果**：修复后估值 = `-2.97%`（≈ 560780.SH 行情 -3.12% × 权重 0.95）✅

---

### 通用诊断流程（场景A）

```
基金估值显示 "--"
       ↓
检查 load_holding_rows 返回的 asset_codes
       ↓
├─ asset_codes 是底层股票，但基金是联接基金/ETF？
│      ↓
│  查 holding_versions: 有没有 is_active=False 但 total_weight 更大的版本？
│      ↓
│  有 → 激活正确版本（UPDATE is_active），重启/等缓存过期
│
└─ asset_codes 看起来正确？
       ↓
   验证行情能否抓到（AKShareDataSource.fetch_stock_live_quotes）
       ↓
   能抓 → 看 compute_live_fund_estimates 里的 skip 原因
   抓不到 → 数据源问题（停牌/新股/代码格式）
```

---

## 2. 历史校准残差全是 `+0.00%`（raw_estimate_too_small）

### 症状

- 校准残差明细表中，所有历史日期的「修正估值」都是 `+0.00%` 或 `-0.00%`
- skip_reason 列显示 `raw_estimate_too_small(0.000001)` 之类
- 但某天（某次数据同步后）突然变得正常

### 原因

校准历史复演时，用的是 `daily_quotes` 表里对应股票的历史行情。如果某个持仓股/ETF
在历史某段时间的 `daily_quotes` 数据为 0 或缺失，那么估值计算出来就是接近 0 的微小数，
被 `raw_estimate_too_small` 门限过滤掉，记录为跳过。

**联接基金（如 020640）的常见场景**：
- ETF 自身（560780.SH）的日度行情在历史时段没有入库
- 历史行情同步只同步了成分股，没有同步 ETF 本身的行情
- 只有等数据同步到包含 ETF 的那天，`daily_quotes` 才有数据，估值才正常

### 修复思路

强制重演校准（「强制重演校准」按钮）不会改变历史数据缺失的事实。
要修，需要把 ETF 自身的历史行情补录到 `daily_quotes` 表：

```python
# 手动补录 560780.SH 的历史日度行情
# （或在数据同步流程中加入对 ETF 类型 holding_item 的历史行情同步）
```

> **注意**：这是数据补全问题，不是 bug。过去的校准残差变差是因为历史行情确实不在库里。

---

## 数据库快速诊断命令

以下命令可以在项目根目录运行：

```bash
# 查所有活跃持仓版本（及权重）
sqlite3 data/fund_nav_estimator.db "
SELECT hv.fund_code, hv.id, hv.is_active, hv.source, hv.total_weight, COUNT(hi.id) item_count
FROM holding_versions hv
LEFT JOIN holding_items hi ON hi.holding_version_id = hv.id
GROUP BY hv.id
ORDER BY hv.fund_code, hv.report_date DESC;
"

# 找"活跃版本权重却很低（<5%）"的可疑基金（可能是持仓版本选错）
sqlite3 data/fund_nav_estimator.db "
SELECT fund_code, id, is_active, source, total_weight
FROM holding_versions
WHERE is_active = 1 AND total_weight < 0.05
ORDER BY total_weight;
"

# 查某基金的所有持仓版本
sqlite3 data/fund_nav_estimator.db "
SELECT id, report_date, is_active, source, total_weight
FROM holding_versions
WHERE fund_code = '020640'
ORDER BY report_date DESC, id DESC;
"

# 查某个 holding_version 的持仓明细
sqlite3 data/fund_nav_estimator.db "
SELECT asset_code, asset_name, asset_type, weight
FROM holding_items
WHERE holding_version_id = 15;
"

# 修复持仓版本激活状态
sqlite3 data/fund_nav_estimator.db "
UPDATE holding_versions SET is_active=1 WHERE id=<正确版本id> AND fund_code='<基金代码>';
UPDATE holding_versions SET is_active=0 WHERE id=<错误版本id> AND fund_code='<基金代码>';
"
```

---

## 关键代码位置

| 功能 | 文件 | 函数/行 |
|------|------|---------|
| 选择活跃持仓版本并组装 holding_rows | `src/frontend_app.py` | `load_holding_rows()` |
| 从 holding_rows 提取 asset_codes 并抓行情 | `src/frontend_app.py` | `load_live_estimate_bundle()` |
| 估值计算核心 | `src/estimator.py` | `compute_live_fund_estimates()` |
| 持仓版本数据模型 | `src/models.py` | `HoldingVersion`, `HoldingItem` |
| 行情抓取 | `src/data_sources/akshare_source.py` | `fetch_stock_live_quotes()` |

---

*最后更新：2026-05-29*
