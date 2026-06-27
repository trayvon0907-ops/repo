# A股分析面板 — 项目进度文档

> 给新电脑 / 新 AI 助手读的快速上手文件。读完这份文档就能接手继续开发。

---

## 一、项目基本信息

| 项目 | 内容 |
|------|------|
| 项目性质 | 个人投资者用的 A 股信息展示面板（**不是荐股工具**） |
| 主入口 | `astock_app.py` |
| 公网地址 | https://astockapppy-7jbasjzgqkdeq65o7jgt2d.streamlit.app/ |
| GitHub 仓库 | https://github.com/trayvon0907-ops/repo |
| 部署平台 | Streamlit Community Cloud（推送 GitHub 自动部署） |
| 本地运行 | `streamlit run astock_app.py` |
| Python 版本 | 3.12 |

---

## 二、技术栈

| 层 | 技术 |
|----|------|
| 框架 | Streamlit（前后端一体） |
| 行情数据 | akshare（东方财富免费接口） |
| K线历史 | **yfinance**（Yahoo Finance CDN，海外服务器访问快）；失败自动降级 akshare |
| 实时盘口 | akshare `stock_bid_ask_em` |
| 股票名称 | akshare `stock_zh_a_spot_em`（全市场快照，缓存 1 小时） |
| 绘图 | Plotly（make_subplots，5 个子图） |
| 自动刷新 | streamlit-autorefresh（交易时段每 5 秒） |

### 当前 requirements.txt
```
streamlit>=1.30
akshare>=1.18
plotly>=5.0
pandas>=2.0
numpy>=1.24
yfinance>=0.2.40
streamlit-autorefresh>=1.0.1
```

---

## 三、已实现功能（当前状态）

### Tab 1：单股分析
| 序号 | 功能模块 | 状态 |
|------|----------|------|
| 一 | 实时行情（最新价、涨跌幅红绿显示、最高最低今开昨收） | ✅ 完成 |
| 二 | 五档买卖盘（中文显示：卖五→卖一 / 买一→买五，含委托量） | ✅ 完成 |
| 三 | K线图（日/周/月切换，5 子图：K线+均线+布林 / 成交量 / MACD / KDJ / RSI） | ✅ 完成 |
| 四 | 客观技术信号表 + 教学说明 | ✅ 完成 |
| 五 | 多周期共振（日线/周线/月线趋势，含教学说明） | ✅ 完成 |
| 六 | 公司最新公告（近 15 天，**点按钮才加载**，不影响主页速度） | ✅ 完成 |
| — | 股票名称 + 行业显示（标题栏） | ✅ 完成 |
| — | 交易时段自动刷新（侧边栏开关，每 5 秒，非交易时段自动暂停） | ✅ 完成 |

### Tab 2：持仓组合
| 功能 | 状态 |
|------|------|
| 多股持仓表（添加/编辑/删除） | ✅ 完成 |
| 批量实时报价（全市场快照取价） | ✅ 完成 |
| 每股盈亏 + 总资产计算 | ✅ 完成 |

### 基础设施
- 科技感深色主题 CSS（#00d4ff 蓝色调，深色背景）
- 访问密码保护（Streamlit Secrets 里设 `app_password`）
- 所有数据请求带 `@st.cache_data` 缓存 + `_retry()` 自动重试
- K线交互：滚轮缩放 ✅、统一悬停十字线 ✅、底部 range slider ✅

---

## 四、关键函数说明

```python
_retry(fn, tries=3, gap=0.8)          # 网络请求自动重试
_in_trading_hours() -> bool            # 判断是否在交易时段（北京时间）
_get_all_names() -> DataFrame          # 全市场代码-名称表，缓存 1 小时（所有股票共用）
get_stock_name(code) -> str            # 从名称表查单只股票简称
get_stock_info(code) -> dict           # 名称 + 行业，缓存 24 小时
get_orderbook(code) -> (dict, df)      # 实时盘口，缓存 5 秒
get_spot_all() -> DataFrame            # 全市场快照（持仓组合用），缓存 8 秒
get_hist(code, period, days) -> df     # K线历史：yfinance 优先，akshare 降级
resample_ohlcv(daily_df, rule) -> df   # 日线本地重采样为周线("W")或月线("ME")
add_indicators(df) -> df               # 计算 MA/MACD/KDJ/RSI/BOLL
trend_of(df) -> str                    # 返回 "多头"/"空头"/"震荡"
get_notices(code) -> df                # 近 15 天公告，缓存 30 分钟
render_portfolio()                     # 渲染持仓组合 Tab
```

---

## 五、重要技术决策 & 踩过的坑

### 数据源
| 问题 | 解决方案 |
|------|----------|
| `stock_individual_info_em` 在云端报 "Length mismatch" 异常 | 改用 `stock_zh_a_spot_em` 全市场快照查名称 |
| `stock_info_a_code_name` 需连深交所官网，SSL 握手失败 | 放弃，改用东方财富接口 |
| 全市场快照按股票代码分别缓存 → 每换一只都要等 5 分钟 | 改为 `_get_all_names()` 统一缓存，所有代码共用 |
| akshare K线从国内服务器拉，Streamlit 云端延迟高 | K线改用 yfinance（Yahoo CDN），akshare 作降级 |
| yfinance 成交量单位是"股" | 除以 100 转换为"手" |
| 盘口字段名是英文（sell_1/buy_1） | 原始展开栏做映射显示中文，主表已是中文 |

### Streamlit 特性
| 问题 | 解决方案 |
|------|----------|
| Candlestick 自动生成 rangeslider 导致子图标题错位 | 先全局关闭所有 rangeslider，再单独给第 5 子图开启 |
| K线 hover 默认英文 open/high/low/close | 用 `hovertext` + `hoverinfo="x+text"` 覆盖为中文 |
| `st.dataframe` 背景白色无法被 CSS 覆盖 | 盘口表改用 `st.table` |
| 公告自动加载拖慢整页 | 改为按钮触发（`st.button` + `st.spinner`） |

### 合规红线（不能碰）
1. 不输出买入/卖出/加仓/止损/止盈/持有等操作建议
2. 不对个股做看涨/看跌方向性预测
3. 页面必须保留免责声明
4. 北向资金不展示为"实时"（2024-05-13 起已停止披露）

---

## 六、待开发 / 规划中功能

| 功能 | 优先级 | 备注 |
|------|--------|------|
| 板块联动（同板块涨跌情况） | 中 | 需要板块分类接口 |
| 相似 K 线形态匹配 | 低 | 算法复杂，可用 DTW 距离 |
| 近期资讯（新闻舆情） | 中 | akshare 有 `stock_news_em` |
| 利好利空面分析 | 中 | 客观财务指标展示，不做预测 |
| Tushare Pro 财务数据接入 | 低 | 需要用户自己申请 token |

---

## 七、部署 & 本地开发快速上手

### 新电脑本地跑
```bash
git clone https://github.com/trayvon0907-ops/repo.git
cd repo
pip install -r requirements.txt
streamlit run astock_app.py
```

### 改完代码推送（自动触发云端重新部署）
```bash
git add astock_app.py
git commit -m "描述改了什么"
git push
```

### 访问密码设置
在 Streamlit Cloud 后台 → App Settings → Secrets 里加：
```toml
app_password = "你的密码"
```

---

## 八、git 提交历史摘要（关键节点）

| commit | 内容 |
|--------|------|
| 初始 | 基础面板：行情+盘口+K线+技术指标 |
| 持仓组合 | 多股持仓，批量取价，盈亏计算 |
| 盘口修复 | 字段名从中文改为英文（sell_1/buy_1 等） |
| K线优化 | 滚轮缩放、成交量子图、统一悬停、rangeslider |
| 科技主题 | 深色 CSS，#00d4ff 蓝色调 |
| 公告模块 | 近 15 天公告，按需加载 |
| 名称修复 | 改用 stock_zh_a_spot_em 查名称，_get_all_names 统一缓存 |
| 提速 | K线换 yfinance，公告改按钮加载 |
| 实时刷新 | streamlit-autorefresh，交易时段每 5 秒，侧边栏开关 |
| 中文化 | K线 tooltip 中文，盘口原始字段中文映射 |
