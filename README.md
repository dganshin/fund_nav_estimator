# 基金实时估值

通过基金公开持仓、修正权重和实时股票行情，估算基金今日涨跌与我的今日盈亏。

## 快速启动

```bash
pip install -r requirements.txt
python3 src/main.py init-db
uvicorn src.frontend_app:app --reload --host 127.0.0.1 --port 8502
```

打开 <http://127.0.0.1:8502/>

系统首次启动自动建表，无需手动 init-db。

## 数据流概览

```text
基金建档 → 拉取公开持仓 / 净值 / 资产配置
        → 计算修正权重（effective_weight）
        → 因果校准（逐日 walk-forward，不偷看未来）
        → 盘中实时行情 → 估值输出
```

### 数据源架构

- **盘中实时行情**: 东财实时 API → 腾讯 → 个股查漏
- **盘后日K回填**: 东财日K → efinance 备用
- **校准数据源**: 盘中估值快照 (FundEstimate) → 实时行情 API → 日K 兜底

盘中估值和盘后校准使用**同一数据源（东财）**，避免不同数据源"昨收价"口径不一致导致的涨跌幅差异。当日校准优先使用盘中保存的估值快照，与用户看到的数值完全一致。

## 页面结构

| 页面 | 路径 | 说明 |
| --- | --- | --- |
| 首页 | `/` | 实时估值排行榜，按持仓/自选/其他分组，支持搜索和排序 |
| 基金详情 | `/fund/{code}` | 持仓股票贡献明细、历史图表、误差统计，面板操作买卖清仓 |
| 我的持仓 | `/portfolio` | 录入持仓、批量导入自选、批量强制覆盖金额 |
| 组合穿透 | `/exposure` | 跨基金底层资产聚合穿透，按股票/行业/基金维度展开 |
| 管理 | `/manage` | 基金池、持仓版本、资产配置、行业配置、修正权重 |

## Web 页面功能

### 首页

- 搜索框输入基金代码 → "加入自选"（后台自动建档）或 "按金额买入"（填写持有金额）
- 持仓基金显示今日估算盈亏 = 持有金额 × 实时估值
- 自选基金显示估值但无盈亏
- 支持自动刷新（5/10/30秒）

### 基金详情 (`/fund/{code}`)

- 实时估值 + 今日盈亏 + 置信度 + 持仓股票贡献表
- 三大模型估值（覆盖修正/单因子/双因子 + ensemble）在高级信息中可查看
- 面板操作：修改总金额、加仓、减仓、清仓
- 历史分析：估值 vs 真实净值对比图、误差趋势图、三种估值方法统计对比
- 手动校准 / 强制重演校准

### 我的持仓 (`/portfolio`)

- 单只录入：基金代码 + 金额 + 份额 + 成本净值
- 批量强制覆盖金额：粘贴 "基金名称 金额" 文本，系统模糊匹配后覆盖
- 批量导入自选：粘贴六位数基金代码（每行一个），后台自动建档加入自选
- 当前持仓列表：表格批量编辑金额

### 组合穿透 (`/exposure`)

- 按持仓金额加权汇总所有基金的底层股票敞口
- 支持按股票/行业/基金维度切换视图

### 管理 (`/manage`)

- 基金池管理：增删改基金、启用/停用
- 持仓版本管理：编辑公开持仓（权重、来源、报告日）
- 资产配置管理：股票/债券/现金/其他的仓位比例
- 行业配置管理
- 修正权重生成
- **一键同步并校准**：增量更新所有活跃基金的净值/行情并校准
- **强制全量重建**：清空所有校准残差和状态，从头拉数据重跑校准（切换数据源后执行一次）

## 校准系统

### 因果正确性

三个约束条件全部满足：

```python
# 训练样本必须早于当前日期（不偷看未来）
CalibrationResidual.trade_date < trade_date

# 训练样本必须属于当前 active holding_version（跨版本隔离）
CalibrationResidual.holding_version_id == holding_version.id

# 只使用被标记为有效的历史样本（异常日自动排除）
CalibrationResidual.is_used_for_update.is_(True)
```

### 在线学习参数

| 参数 | 值 | 说明 |
| --- | --- | --- |
| RIDGE_WINDOW | 30 天 | 滑动窗口 |
| RIDGE_DECAY | 0.90 | 指数衰减权重 |
| MAX_ABS_RESIDUAL | 2% | 异常日阈值，超标跳过不参与训练 |
| TWO_FACTOR_MIN_SAMPLES | 15 | 双因子模型最少样本数 |

### 估值快照机制

盘中刷新页面时，实时估值结果（含三个模型值 + ensemble）自动保存为 FundEstimate 快照。盘后校准时优先读取快照，确保残差反映的是用户盘中实际看到的精度，而非事后用另一数据源重算的值。

## 核心公式

```text
published_weight → effective_weight = published_weight × (stock_weight / covered_weight)
known_estimate = Σ published_weight_i × return_i
unknown_estimate = max(stock_weight - covered_weight, 0) × known_avg
base_estimate = known_estimate + unknown_estimate          (覆盖修正)
single_scale_estimate = scale_factor × base_estimate       (单因子)
two_factor_estimate = β_known × known + β_unknown × unknown + α (双因子)
final_estimate = ensemble(覆盖, 单因子, 双因子)
estimated_today_profit = holding_amount × final_estimate
```

## ETF 联接处理

ETF 联接基金优先拉取实际公开持仓（含目标 ETF 和其他持股），无法获取时才回退到目标 ETF 单一持仓。不再硬编码 95% 权重。部分 ETF 联接维护了已知目标 ETF 映射表作为兜底。

## 当前限制

- 估值仅供参考，不做买卖建议
- 主动基金调仓会影响准确度
- 公开持仓可能滞后（季报延迟）
- QDII/海外基金不支持估值
- 白银期货等商品型基金无股票持仓，不适用
- 不接入实盘交易

## 故障排查与性能

遇到估值显示 `--`、数据异常等问题，请先查阅：

📖 **[docs/troubleshooting.md](docs/troubleshooting.md)** (故障排查手册)

如果需要了解系统关于“实时高频刷新”、“缓存策略”或“异步写库”的实现细节与架构决策，请查阅：

🚀 **[docs/performance_tuning.md](docs/performance_tuning.md)** (性能优化与架构决策)

包含：
- 持仓版本选错导致估值为 `--` 的排查步骤
- 历史校准残差全为 0 的原因说明
- 数据库快速诊断命令
- 关键代码位置速查表
