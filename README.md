# 基金实时估值

一句话说明：
个人版养基宝增强估值工具：用修正权重 effective_weight 和实时股票行情，估算基金今日涨跌与我的今日盈亏。

## 快速启动
- 创建虚拟环境
- pip install -r requirements.txt
- python3 src/main.py init-db
- uvicorn src.frontend_app:app --reload --host 127.0.0.1 --port 8502
- 打开 http://127.0.0.1:8502/

## 日常使用流程
1. 首页搜索基金代码
2. 加入自选或按金额买入
3. 回首页看今日实时估值和今日估算盈亏
4. 点基金详情看股票贡献
5. 最新真实净值出来后点手动校准
6. 查看校准残差

## 核心公式
published_weight -> effective_weight
final_estimate = Σ effective_weight_i × live_return_i
estimated_today_profit = holding_amount × final_estimate

## 校准原则
- 不预测股票
- 不使用未来数据
- 今天盘中只用昨天及以前已公布真实净值校准出的 scale
- 每只基金单独校准
- 每个 holding_version 单独校准
- 不把所有历史数据 batch 训练
- 默认只校准低维 scale_factor

## 页面说明
- 首页：实时估值榜
- 我的持仓：持仓金额
- 详情页：持仓股票贡献和校准残差
- 管理页：基金、持仓、资产配置、修正权重

## 当前限制
- 估值仅供参考
- 不做买卖建议
- 不做自动交易
- 不预测股票
- 主动基金调仓会影响准确度
- 实时行情源可能延迟或失败

---
## 开发与调试

### CLI 常用命令
```bash
# 导入数据（持仓、资金流等，需按模板准备 CSV）
python3 src/main.py import-holdings --fund 002207 --file holdings.csv

# 运行历史回溯与权重校准
python3 src/main.py generate-estimate-history --fund 002207 --start 2026-01-01 --end 2026-05-20
python3 src/main.py auto-select-best-estimates --fund 002207 --start 2026-01-01 --end 2026-05-20
```

### Streamlit 历史后台
如果您需要更复杂的表格数据预览、模型策略验证与批量生成记录的交互页面，可以启动：
```bash
streamlit run src/app.py
```
（注：Streamlit 主要定位为投研、调试后台，不适合日常看板）
