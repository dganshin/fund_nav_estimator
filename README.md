# 基金实时估值

个人版养基宝增强估值工具：输入基金代码和持有金额后，系统用公开持仓、修正权重和实时股票行情估算基金今日涨跌与我的今日盈亏。

## 快速启动

```bash
python3 src/main.py init-db
uvicorn src.frontend_app:app --reload --host 127.0.0.1 --port 8502
```

打开：
http://127.0.0.1:8502/

## 日常使用

1. 首页搜索基金代码
2. 加入自选或按金额买入
3. 系统自动拉取基金名称、净值、公开持仓、资产配置
4. 首页查看实时估值和今日盈亏
5. 点击基金查看股票贡献
6. 最新真实净值公布后点击手动校准
7. 查看校准残差

## 核心公式

published_weight -> effective_weight

final_estimate = Σ effective_weight_i × live_return_i

estimated_today_profit = holding_amount × final_estimate

## 误差口径

首页显示：
预计误差≤±x%

来源：
最近20个有效残差样本的 80% 分位数或平均绝对误差。

## 校准原则

- 不预测股票
- 不使用未来数据
- 今天盘中只用昨天以前的 scale
- 每只基金单独校准
- 每个 holding_version 单独校准
- 默认只校准 scale_factor，不乱拟合每只股票权重

## 当前限制

- 估值仅供参考
- 不做买卖建议
- 不做自动交易
- 主动基金调仓会影响准确度
- 公开持仓可能滞后
- 行情源可能失败或延迟
