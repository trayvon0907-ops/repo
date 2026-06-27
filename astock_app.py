# -*- coding: utf-8 -*-
"""
A股分析面板  (Streamlit + akshare 免费数据)  —— 公网部署版
------------------------------------------------------------
功能：输入股票代码 -> 实时行情 + 五档买卖盘 + K线/技术指标 + 多周期共振 + 多股持仓盈亏
部署：上传到 GitHub -> Streamlit Community Cloud 一键部署，得到永久公网网址
本地运行：streamlit run astock_app.py

🔒 已加访问密码：在 Streamlit Cloud 的 Secrets 里设置 app_password 即可生效。
⚠️ 本工具仅做客观数据展示与技术指标机械计算，不构成任何投资建议。
"""

import datetime as dt
import time
import numpy as np
import pandas as pd
import streamlit as st

try:
    import akshare as ak
except Exception as e:  # pragma: no cover
    st.error(f"akshare 没装好：{e}")
    st.stop()

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:
    st.error("plotly 没装好。")
    st.stop()


# ============================================================
# 0. 访问密码（公网部署用）
# ============================================================
def _get_pwd():
    try:
        return st.secrets.get("app_password", None)
    except Exception:
        return None


def check_password() -> bool:
    configured = _get_pwd()
    if not configured:
        st.info("ℹ️ 当前未设置访问密码。部署到公网后，请在 Streamlit 的 Secrets 里加一行 "
                "`app_password = \"你的密码\"` 来保护页面。")
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.markdown("### 🔒 请输入访问密码")
    pwd = st.text_input("密码", type="password", label_visibility="collapsed")
    if pwd:
        if pwd == configured:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("密码错误，请重试。")
    return False


# ============================================================
# 1. 数据获取（带缓存 + 自动重试）
# ============================================================
def _retry(fn, tries=3, gap=0.8):
    err = None
    for _ in range(tries):
        try:
            return fn()
        except Exception as e:
            err = e
            time.sleep(gap)
    raise err


def clean_code(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


@st.cache_data(ttl=86400, show_spinner=False)
def get_stock_name(code: str) -> str:
    """用 stock_info_a_code_name 查股票简称，该接口只返回 [代码, 名称] 两列，最稳定。"""
    df = _retry(lambda: ak.stock_info_a_code_name())
    row = df[df.iloc[:, 0].astype(str).str.zfill(6) == code.zfill(6)]
    if not row.empty:
        return str(row.iloc[0, 1]).strip()
    return ""


@st.cache_data(ttl=86400, show_spinner=False)
def get_stock_info(code: str) -> dict:
    """取单只股票基本信息（名称、行业）。名称优先用 get_stock_name，行业从 individual_info_em 取。"""
    name = get_stock_name(code)
    industry = ""
    try:
        raw = _retry(lambda: ak.stock_individual_info_em(symbol=code))
        # 兼容 2列/3列 两种返回格式
        if raw.shape[1] >= 2:
            d = dict(zip(raw.iloc[:, 0].astype(str), raw.iloc[:, 1].astype(str)))
        else:
            d = {}
        if not name:
            name = (d.get("股票简称") or d.get("名称") or d.get("证券简称") or "")
        industry = (d.get("行业") or d.get("所属行业") or d.get("行业板块") or "")
    except Exception:
        pass
    return {"名称": str(name).strip(), "行业": str(industry).strip()}


@st.cache_data(ttl=8, show_spinner=False)
def get_spot_all():
    """拉取全市场快照，用于持仓组合批量查价。单股分析不用此函数。"""
    return _retry(lambda: ak.stock_zh_a_spot_em())


@st.cache_data(ttl=5, show_spinner=False)
def get_orderbook(code: str):
    """
    返回 (字段字典, 原始 DataFrame)。
    akshare 实际返回两列：item / value，字段名为英文：
      价格：sell_1~sell_5 / buy_1~buy_5
      委量：sell_1_vol~sell_5_vol / buy_1_vol~buy_5_vol
      行情：最新, 均价, 涨幅, 涨跌, 总手, 金额, 换手, 量比, 最高, 最低, 今开, 昨收, 涨停, 跌停
    value 可能为 "-" 或一串短横线（无挂单），统一当作空值处理。
    """
    raw = _retry(lambda: ak.stock_bid_ask_em(symbol=code))
    d = dict(zip(raw.iloc[:, 0], raw.iloc[:, 1]))
    return d, raw


def _safe_num(val):
    """把 '-' / '--' / None 等无效值转成 None，有效数字转成 float。"""
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or set(s) <= {"-", "–", "—"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def get_hist(code: str, period: str = "daily", days: int = 400):
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=days * 2 + 30)).strftime("%Y%m%d")
    df = _retry(lambda: ak.stock_zh_a_hist(symbol=code, period=period,
                                           start_date=start, end_date=end,
                                           adjust="qfq", timeout=20))
    if df is None or df.empty:
        return None
    df = df.rename(columns={"日期": "date"})
    df["date"] = pd.to_datetime(df["date"])
    return df.tail(days).reset_index(drop=True)


# ============================================================
# 2. 技术指标
# ============================================================
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l = df["收盘"], df["最高"], df["最低"]
    for n in (5, 10, 20, 60):
        df[f"MA{n}"] = c.rolling(n).mean()
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD"] = (df["DIF"] - df["DEA"]) * 2
    low_n, high_n = l.rolling(9).min(), h.rolling(9).max()
    rsv = (c - low_n) / (high_n - low_n) * 100
    df["K"] = rsv.ewm(com=2, adjust=False).mean()
    df["D"] = df["K"].ewm(com=2, adjust=False).mean()
    df["J"] = 3 * df["K"] - 2 * df["D"]
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + gain / loss)
    mid, std = c.rolling(20).mean(), c.rolling(20).std()
    df["BOLL_M"], df["BOLL_U"], df["BOLL_L"] = mid, mid + 2 * std, mid - 2 * std
    return df


def trend_of(df: pd.DataFrame) -> str:
    if df is None or len(df) < 25:
        return "数据不足"
    d = add_indicators(df)
    last = d.iloc[-1]
    if pd.isna(last["MA20"]):
        return "数据不足"
    slope_up = d["MA20"].iloc[-1] > d["MA20"].iloc[-5]
    above = last["收盘"] > last["MA20"]
    if above and slope_up:
        return "多头"
    if (not above) and (not slope_up):
        return "空头"
    return "震荡"


def resample_ohlcv(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    从日线 DataFrame 本地重采样为周线("W")或月线("ME")。
    只需一次网络请求，无需额外联网。
    """
    df = daily.set_index("date")
    agg = {
        "开盘": "first", "最高": "max",
        "最低": "min",  "收盘": "last",
        "成交量": "sum",
    }
    # 只保留有数值的列
    agg = {k: v for k, v in agg.items() if k in df.columns}
    rs = df.resample(rule, label="right", closed="right").agg(agg).dropna()
    return rs.reset_index()


def dominant_cycle(df: pd.DataFrame):
    if df is None or len(df) < 60:
        return None
    x = df["收盘"].diff().dropna().values
    x = x - x.mean()
    mag = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x))
    mag[0] = 0
    peak = int(np.argmax(mag))
    return round(1 / freqs[peak], 1) if freqs[peak] > 0 else None


# ============================================================
# 3. 持仓组合模块
# ============================================================
_PORTFOLIO_KEY = "portfolio_df"

_PORTFOLIO_COLS = {
    "代码":       st.column_config.TextColumn("股票代码（6位）", max_chars=6, width="small"),
    "成本价":     st.column_config.NumberColumn("成本价（元）",   min_value=0.0, format="%.3f", width="small"),
    "数量（股）": st.column_config.NumberColumn("持仓数量（股）", min_value=0,   step=100,      width="small"),
}


def _init_portfolio():
    if _PORTFOLIO_KEY not in st.session_state:
        st.session_state[_PORTFOLIO_KEY] = pd.DataFrame(
            columns=["代码", "成本价", "数量（股）"]
        )


def _valid_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """过滤掉空行、格式错误的行。"""
    if df.empty:
        return df
    df = df.dropna(subset=["代码", "成本价", "数量（股）"])
    df = df[df["代码"].astype(str).str.strip().str.len() == 6]
    df = df[pd.to_numeric(df["成本价"], errors="coerce") > 0]
    df = df[pd.to_numeric(df["数量（股）"], errors="coerce") > 0]
    return df.copy()


def render_portfolio():
    st.subheader("💼 我的持仓组合")
    st.caption(
        "在表格里直接填写或修改；点末行空白处或左侧「＋」新增一行；"
        "勾选行后点表格右上角垃圾桶图标删除。填好后点「刷新报价」。"
    )

    _init_portfolio()

    edited = st.data_editor(
        st.session_state[_PORTFOLIO_KEY],
        num_rows="dynamic",
        column_config=_PORTFOLIO_COLS,
        hide_index=True,
        use_container_width=True,
        key="portfolio_editor",
    )
    # 把编辑结果同步回 session_state
    st.session_state[_PORTFOLIO_KEY] = edited

    valid = _valid_holdings(edited)

    col_btn, col_clear = st.columns([1, 1])
    with col_btn:
        refresh = st.button("🔄 刷新报价", type="primary",
                            disabled=valid.empty, use_container_width=True)
    with col_clear:
        if st.button("🗑️ 清空全部持仓", use_container_width=True):
            st.session_state[_PORTFOLIO_KEY] = pd.DataFrame(
                columns=["代码", "成本价", "数量（股）"]
            )
            st.rerun()

    if valid.empty:
        st.info("还没有有效持仓记录。请在上方表格填入股票代码（6位）、成本价和数量。")
        return

    if refresh:
        st.cache_data.clear()

    # 批量拉价格（一次请求覆盖全部持仓）
    try:
        with st.spinner("正在获取行情…"):
            spot_all = get_spot_all()
    except Exception as e:
        st.warning(f"行情获取失败（非交易时段或网络问题）：{e}")
        return

    rows = []
    total_cost_amount = 0.0
    total_mv = 0.0

    for _, r in valid.iterrows():
        code = clean_code(str(r["代码"]))
        cost_p = float(r["成本价"])
        qty = int(r["数量（股）"])
        cost_amount = cost_p * qty

        row_spot = spot_all[spot_all["代码"] == code]
        if row_spot.empty:
            rows.append({
                "代码": code, "名称": "未找到",
                "现价（元）": "—", "成本价（元）": f"{cost_p:.3f}",
                "数量（股）": qty,
                "持仓成本（元）": f"{cost_amount:,.0f}",
                "当前市值（元）": "—",
                "浮动盈亏（元）": "—", "收益率": "—",
            })
            continue

        price_now = float(row_spot.iloc[0].get("最新价") or 0)
        name = str(row_spot.iloc[0].get("名称", "—"))
        mv = price_now * qty
        pnl = (price_now - cost_p) * qty
        pct = (price_now / cost_p - 1) * 100 if cost_p > 0 else 0.0

        total_cost_amount += cost_amount
        total_mv += mv

        rows.append({
            "代码": code,
            "名称": name,
            "现价（元）": f"{price_now:.2f}",
            "成本价（元）": f"{cost_p:.3f}",
            "数量（股）": qty,
            "持仓成本（元）": f"{cost_amount:,.0f}",
            "当前市值（元）": f"{mv:,.0f}",
            "浮动盈亏（元）": f"{pnl:+,.0f}",
            "收益率": f"{pct:+.2f}%",
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # 汇总行
    if total_cost_amount > 0:
        total_pnl = total_mv - total_cost_amount
        total_pct = (total_mv / total_cost_amount - 1) * 100

        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("总持仓成本", f"{total_cost_amount:,.0f} 元")
        m2.metric("总当前市值", f"{total_mv:,.0f} 元")
        m3.metric("总浮动盈亏", f"{total_pnl:+,.0f} 元", f"{total_pct:+.2f}%")
        m4.metric("持仓只数", f"{len(rows)} 只")

    st.caption("以上均为客观数值计算，不构成任何买卖操作建议。")


# ============================================================
# 4. 公告数据
# ============================================================
@st.cache_data(ttl=1800, show_spinner=False)
def get_notices(code: str):
    """
    获取个股近期公告。
    优先用 stock_notice_report，失败则用 stock_announcement_em，
    不依赖当日是否为交易日，返回最近 5 日内公告（最多10条）。
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=5)

    # 方法一
    try:
        df = _retry(lambda: ak.stock_notice_report(symbol=code))
        if df is not None and not df.empty:
            return df.head(10)
    except Exception:
        pass

    # 方法二：东财公告接口
    try:
        df = _retry(lambda: ak.stock_announcement_em(
            symbol=code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        ))
        if df is not None and not df.empty:
            return df.head(10)
    except Exception:
        pass

    return None


# ============================================================
# 5. 页面布局
# ============================================================
st.set_page_config(page_title="A股分析面板", page_icon="📈", layout="wide")

if not check_password():
    st.stop()

# 科技感深色风格 CSS
st.markdown("""
<style>
/* 全局背景与字体 */
[data-testid="stAppViewContainer"] {
    background: linear-gradient(135deg, #0a0e1a 0%, #0d1b2a 60%, #0a1628 100%);
    color: #e0e6f0;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d1b2a 0%, #0a1220 100%);
    border-right: 1px solid #1e3a5f;
}
/* 标题 */
h1 { color: #00d4ff !important; letter-spacing: 2px; }
h2, h3 { color: #7eb8f7 !important; }
/* metric 卡片 */
[data-testid="metric-container"] {
    background: rgba(0, 180, 255, 0.06);
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 10px 14px;
}
[data-testid="stMetricValue"] { color: #00d4ff !important; font-size: 1.3em !important; }
[data-testid="stMetricLabel"] { color: #7eb8f7 !important; }
/* 按钮 */
[data-testid="baseButton-primary"] {
    background: linear-gradient(90deg, #005f8a, #0096c7) !important;
    border: none !important; color: #fff !important;
    box-shadow: 0 0 12px rgba(0,180,255,0.4);
}
/* st.table — 强制覆盖所有内置样式 */
[data-testid="stTable"] table,
div[data-testid="stTable"] table {
    background: rgba(8,18,38,0.95) !important;
    border-collapse: collapse !important;
}
[data-testid="stTable"] th,
div[data-testid="stTable"] th,
[data-testid="stTable"] thead tr th {
    background: #0a1e38 !important;
    color: #00d4ff !important;
    font-weight: 700 !important;
    border: 1px solid #1e3a5f !important;
    padding: 8px 12px !important;
}
[data-testid="stTable"] td,
div[data-testid="stTable"] td,
[data-testid="stTable"] tbody tr td {
    color: #d4eaff !important;
    border: 1px solid #152a45 !important;
    padding: 7px 12px !important;
}
[data-testid="stTable"] tr:nth-child(odd) td,
[data-testid="stTable"] tbody tr:nth-child(odd) td {
    background: rgba(0,80,160,0.10) !important;
}
[data-testid="stTable"] tr:hover td,
[data-testid="stTable"] tbody tr:hover td {
    background: rgba(0,180,255,0.14) !important;
}
/* st.dataframe */
[data-testid="stDataFrame"] { border: 1px solid #1e3a5f; border-radius: 6px; }
[data-testid="stDataFrame"] * { color: #d4eaff !important; }
[data-testid="stDataFrame"] th { color: #00d4ff !important; background: #0a1e38 !important; }
/* 分割线 */
hr { border-color: #1e3a5f !important; }
/* tab */
[data-baseweb="tab-list"] { background: transparent !important; border-bottom: 1px solid #1e3a5f; }
[data-baseweb="tab"] { color: #7eb8f7 !important; }
[aria-selected="true"] { color: #00d4ff !important; border-bottom: 2px solid #00d4ff !important; }
/* 公告卡片 */
.notice-card {
    background: rgba(0,100,180,0.10);
    border: 1px solid #1e3a5f;
    border-left: 3px solid #00d4ff;
    border-radius: 6px;
    padding: 8px 14px;
    margin-bottom: 8px;
    font-size: 0.92em;
    color: #c8d8f0;
}
.notice-card .notice-date { color: #5a8abf; font-size: 0.85em; margin-top: 4px; }
/* expander */
[data-testid="stExpander"] {
    background: rgba(0,50,100,0.15) !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 6px !important;
}
</style>
""", unsafe_allow_html=True)

st.title("📈 A股分析面板")
st.caption("数据来源：东方财富（经 akshare）｜ 仅供学习参考，不构成投资建议")

with st.sidebar:
    st.header("① 单股分析")
    code_in = st.text_input("股票代码（6位）", value="600519",
                            help="例如 600519 贵州茅台、000001 平安银行")
    st.divider()
    run = st.button("🔍 开始分析", use_container_width=True, type="primary")
    st.button("🔄 刷新实时数据", use_container_width=True,
              on_click=st.cache_data.clear)

tab_analysis, tab_portfolio = st.tabs(["📊 单股分析", "💼 持仓组合"])

# ============================================================
# Tab 1 — 单股分析
# ============================================================
with tab_analysis:
    code = clean_code(code_in)

    if not run and "ran_once" not in st.session_state:
        st.info("👈 在左侧输入股票代码，点「开始分析」。云端首次拉数据可能要等十几秒。")
        st.stop()
    st.session_state["ran_once"] = True

    if len(code) != 6:
        st.error("代码格式不对，请输入 6 位数字（如 600519）。")
        st.stop()

    # ---------- 一、实时行情 ----------
    # 名称/行业：单独调用轻量接口，失败时降级为代码本身，不阻断页面
    stock_name, stock_industry = code, ""
    try:
        _info = get_stock_info(code)
        stock_name     = _info["名称"] or code
        stock_industry = _info["行业"]
    except Exception:
        pass

    # 名称仍为代码则再用 stock_info_a_code_name 直接查一次（绕过缓存的失败结果）
    if stock_name == code:
        try:
            _n = get_stock_name(code)
            if _n:
                stock_name = _n
        except Exception:
            pass

    _name_part = stock_name if stock_name != code else code
    _industry_part = f" · {stock_industry}" if stock_industry else ""
    _title_html = (
        f'<span style="color:#00d4ff;font-size:1.25em;font-weight:700">{_name_part}</span>'
        f'<span style="color:#5a8abf;font-size:1em;margin-left:10px">({code})</span>'
        f'<span style="color:#7eb8f7;font-size:0.95em;margin-left:12px">{_industry_part}</span>'
    )
    st.markdown(f"### 一、实时行情 &nbsp; {_title_html}", unsafe_allow_html=True)

    price = 0.0
    ob_d, ob_raw = None, None
    try:
        ob_d, ob_raw = get_orderbook(code)
    except Exception:
        # SSL / 网络瞬断：友好提示，不打印堆栈，不阻断 K 线等后续区块
        st.info("⚠️ 行情暂时获取失败，请点『刷新实时数据』重试。K线与技术指标不受影响。")

    if ob_d:
        _pnow  = _safe_num(ob_d.get("最新"))
        price  = _pnow if _pnow else 0.0
        _chg   = ob_d.get("涨幅",  "—")
        _high  = ob_d.get("最高",  "—")
        _low   = ob_d.get("最低",  "—")
        _vol   = ob_d.get("总手",  "—")
        _turn  = ob_d.get("换手",  "—")
        _ratio = ob_d.get("量比",  "—")

        # 涨跌幅：红涨绿跌大号彩色文字（A股习惯）
        _chg_num = _safe_num(_chg)
        if _chg_num is not None:
            if _chg_num > 0:
                _chg_html = (f'<span style="color:#e63946;font-size:1.5em;font-weight:bold">'
                             f'▲ +{_chg_num:.2f}%</span>')
            elif _chg_num < 0:
                _chg_html = (f'<span style="color:#2a9d8f;font-size:1.5em;font-weight:bold">'
                             f'▼ {_chg_num:.2f}%</span>')
            else:
                _chg_html = '<span style="font-size:1.5em;color:gray">— 0.00%</span>'
        else:
            _chg_html = '<span style="font-size:1.5em;color:gray">—</span>'

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("最新价",      f"{price:.2f}" if price else "—")
        with c2:
            st.markdown("**涨跌幅**")
            st.markdown(_chg_html, unsafe_allow_html=True)
        c3.metric("最高",        str(_high))
        c4.metric("最低",        str(_low))
        c5.metric("总成交(手)",  str(_vol))
        c6.metric("换手率 / 量比",
                  f"{_turn}% / {_ratio}" if _turn not in ("—", None) else "—")

    # ---------- 二、五档买卖盘 ----------
    st.subheader("二、五档买卖盘")
    if ob_d is not None:
        try:
            # sell_5→sell_1 显示卖五→卖一；buy_1→buy_5 显示买一→买五
            book_rows = []
            for lvl in range(5, 0, -1):
                p = _safe_num(ob_d.get(f"sell_{lvl}"))
                v = _safe_num(ob_d.get(f"sell_{lvl}_vol"))
                book_rows.append({
                    "档位": f"卖{['一','二','三','四','五'][lvl-1]}",
                    "价格": f"{p:.2f}" if p is not None else "—",
                    "委托量(手)": f"{int(v):,}" if v is not None else "—",
                })
            for lvl in range(1, 6):
                p = _safe_num(ob_d.get(f"buy_{lvl}"))
                v = _safe_num(ob_d.get(f"buy_{lvl}_vol"))
                book_rows.append({
                    "档位": f"买{['一','二','三','四','五'][lvl-1]}",
                    "价格": f"{p:.2f}" if p is not None else "—",
                    "委托量(手)": f"{int(v):,}" if v is not None else "—",
                })
            st.table(pd.DataFrame(book_rows))
        except Exception as e:
            st.warning(f"盘口解析异常：{e}")
        with st.expander("查看完整原始盘口字段"):
            st.dataframe(ob_raw, hide_index=True, use_container_width=True)
    else:
        st.info("盘口数据未取到（多在收盘后/周末发生）。")

    # ---------- 三、K线 + 指标 ----------
    st.subheader("三、K线与技术指标")
    tf_map = {"日线": "daily", "周线": "weekly", "月线": "monthly"}
    tf_label = st.radio("K线周期", list(tf_map.keys()), horizontal=True)

    with st.spinner("正在加载数据，请稍候…"):
        # 只联网取一次日线，周线/月线本地重采样
        try:
            hist_daily = get_hist(code, "daily", days=500)
        except Exception as e:
            hist_daily = None
            st.error(f"K线获取失败：{e}")

        if tf_label == "日线":
            hist = hist_daily
        elif tf_label == "周线":
            hist = resample_ohlcv(hist_daily, "W") if hist_daily is not None else None
        else:
            hist = resample_ohlcv(hist_daily, "ME") if hist_daily is not None else None

    if hist is not None and len(hist) > 30:
        d = add_indicators(hist)

        # 成交量涨跌色：收盘>=开盘红，否则绿
        vol_colors = np.where(d["收盘"] >= d["开盘"], "#e63946", "#2a9d8f")

        # 严格按顺序：行1=K线  行2=成交量  行3=MACD  行4=KDJ  行5=RSI
        fig = make_subplots(
            rows=5, cols=1,
            shared_xaxes=True,
            row_heights=[0.38, 0.12, 0.18, 0.16, 0.16],
            vertical_spacing=0.03,
            subplot_titles=("K线 + 均线 + 布林", "成交量", "MACD", "KDJ", "RSI"),
        )

        # 行1：K线 + 均线 + 布林带
        # ── Candlestick 自带 rangeslider，必须在 trace 级别先关掉，再在 layout 里统一关
        fig.add_trace(
            go.Candlestick(
                x=d["date"], open=d["开盘"], high=d["最高"],
                low=d["最低"], close=d["收盘"], name="K线",
                increasing_line_color="#e63946",
                decreasing_line_color="#2a9d8f",
            ),
            row=1, col=1,
        )
        for n, clr in [(5, "#f1a208"), (20, "#3a86ff"), (60, "#8338ec")]:
            fig.add_trace(go.Scatter(x=d["date"], y=d[f"MA{n}"], name=f"MA{n}",
                                     line=dict(width=1, color=clr)), row=1, col=1)
        for bnd, dash in [("BOLL_U", "dot"), ("BOLL_L", "dot")]:
            fig.add_trace(go.Scatter(x=d["date"], y=d[bnd], name=bnd, showlegend=False,
                                     line=dict(width=1, color="#adb5bd", dash=dash)), row=1, col=1)

        # 行2：成交量柱
        fig.add_trace(go.Bar(x=d["date"], y=d["成交量"], name="成交量",
                             marker_color=vol_colors, showlegend=False), row=2, col=1)

        # 行3：MACD（柱 + DIF + DEA）
        fig.add_trace(go.Bar(x=d["date"], y=d["MACD"], name="MACD柱",
                             marker_color=np.where(d["MACD"] >= 0, "#e63946", "#2a9d8f")),
                      row=3, col=1)
        fig.add_trace(go.Scatter(x=d["date"], y=d["DIF"], name="DIF",
                                 line=dict(width=1)), row=3, col=1)
        fig.add_trace(go.Scatter(x=d["date"], y=d["DEA"], name="DEA",
                                 line=dict(width=1)), row=3, col=1)

        # 行4：KDJ
        for k, clr in [("K", "#3a86ff"), ("D", "#f1a208"), ("J", "#e63946")]:
            fig.add_trace(go.Scatter(x=d["date"], y=d[k], name=k,
                                     line=dict(width=1, color=clr)), row=4, col=1)

        # 行5：RSI（含超买/超卖参考线）
        fig.add_trace(go.Scatter(x=d["date"], y=d["RSI"], name="RSI",
                                 line=dict(width=1, color="#8338ec")), row=5, col=1)
        fig.add_hline(y=70, line=dict(color="#ccc", dash="dot"), row=5, col=1)
        fig.add_hline(y=30, line=dict(color="#ccc", dash="dot"), row=5, col=1)

        # 子图标题加粗放大（遍历 annotation 修改 font）
        for ann in fig.layout.annotations:
            ann.font = dict(size=15, color="#333")

        # 全部 x 轴：禁用默认 rangeslider（含 Candlestick 自动生成的那个）+ 统一竖线
        fig.update_xaxes(
            rangeslider_visible=False,
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            spikecolor="#aaa",
            spikethickness=1,
            spikedash="dot",
        )
        # 仅在最底部（行5）x 轴开启范围滑动条，shared_xaxes 使所有子图联动
        fig.update_xaxes(rangeslider_visible=True, rangeslider_thickness=0.04,
                         row=5, col=1)

        fig.update_layout(
            height=1080,
            dragmode=False,
            hovermode="x unified",
            legend=dict(orientation="h", y=1.02),
            margin=dict(t=70, b=20),
        )
        st.plotly_chart(fig, use_container_width=True,
                        config={"scrollZoom": True, "displayModeBar": True})

        # ---------- 四、客观技术信号 ----------
        st.subheader("四、客观技术信号")
        last = d.iloc[-1]

        # 每一项：(指标名, 当前读数, 判断依据/说明)
        sig = []
        sig.append((
            "均线排列",
            ("多头排列(MA5>MA20>MA60)" if last["MA5"] > last["MA20"] > last["MA60"]
             else "空头排列(MA5<MA20<MA60)" if last["MA5"] < last["MA20"] < last["MA60"]
             else "均线交织（无明显排列）"),
            "MA5/20/60 从上到下依次排列为多头（短中长期均价向上），趋势偏强；"
            "反之为空头排列；交织时说明趋势不明朗。",
        ))
        sig.append((
            "MACD",
            "DIF 在 DEA 上方（多头区）" if last["DIF"] > last["DEA"] else "DIF 在 DEA 下方（空头区）",
            "DIF 上穿 DEA 为金叉（多头区），下穿为死叉（空头区）；"
            "反映中期动能方向，柱线由负转正或由正转负是动能切换的信号。",
        ))
        rsi = last["RSI"]
        sig.append((
            "RSI(14)",
            f"{rsi:.1f}　" + (">70 超买区" if rsi > 70 else "<30 超卖区" if rsi < 30 else "中性区"),
            ">70 表示短期涨幅偏大、动能过热；<30 表示短期跌幅偏大、动能偏弱；"
            "30–70 为中性区。RSI 是动量指标，反映一段时间内涨跌力度对比。",
        ))
        j = last["J"]
        sig.append((
            "KDJ-J值",
            f"{j:.1f}　" + (">100 过热" if j > 100 else "<0 过冷" if j < 0 else "正常区间"),
            "J 值由 K、D 推导，波动最剧烈；>100 为短期过热区，<0 为短期过冷区，"
            "常用于捕捉短期超买超卖节点，需结合其他指标确认。",
        ))
        pos = ((last["收盘"] - last["BOLL_L"]) / (last["BOLL_U"] - last["BOLL_L"]) * 100
               if last["BOLL_U"] != last["BOLL_L"] else 50)
        sig.append((
            "布林带位置",
            f"价格处于通道 {pos:.0f}% 位置（0%=下轨, 100%=上轨）",
            "布林带以 MA20 为中轨，±2 倍标准差为上下轨；价格靠近上轨（100%）说明偏强但有压力，"
            "靠近下轨（0%）说明偏弱但有支撑；带宽收窄往往预示波动率变化。",
        ))

        st.table(pd.DataFrame(sig, columns=["指标", "当前读数", "判断依据 / 说明"]))

        # ---------- 五、多周期趋势共振（复用日线本地重采样，无需额外联网）----------
        st.subheader("五、多周期趋势共振")
        try:
            if hist_daily is not None and len(hist_daily) > 30:
                hist_w = resample_ohlcv(hist_daily, "W")
                hist_m = resample_ohlcv(hist_daily, "ME")
                trends = {
                    "日线": trend_of(hist_daily),
                    "周线": trend_of(hist_w),
                    "月线": trend_of(hist_m),
                }
                tdf = pd.DataFrame([trends]).T.reset_index()
                tdf.columns = ["周期", "趋势"]
                st.table(tdf)
                vals = list(trends.values())
                if vals.count("多头") == 3:
                    st.success("三周期同向【多头】：技术上属于多头共振。")
                elif vals.count("空头") == 3:
                    st.error("三周期同向【空头】：技术上属于空头共振。")
                else:
                    st.info("各周期方向不一致：趋势处于分歧/转换状态，技术信号偏弱。")
            else:
                st.info("日线数据不足，无法计算多周期共振。")
        except Exception as e:
            st.warning(f"多周期计算失败：{e}")

        st.caption(
            "📖 判断方法：每个周期用收盘价与 MA20 的相对位置以及 MA20 的斜率来判断——"
            "收盘在 MA20 上方且 MA20 向上为「多头」，收盘在 MA20 下方且 MA20 向下为「空头」，"
            "其余为「震荡」。当日线、周线、月线三个周期方向一致时称为「共振」，"
            "趋势一致性更高、技术参考意义更强；方向分歧时说明趋势处于转换或不明朗阶段。"
            "以上均为客观指标描述，不构成操作建议。"
        )

        cyc = dominant_cycle(hist_daily if hist_daily is not None else hist)
        if cyc:
            st.caption(f"📊 FFT 主导周期估计：约 {cyc} 个日（实验性，仅作节奏参考，勿单独依赖）")
    else:
        st.warning("K线数据不足，无法计算指标。")

    # ---------- 六、公司最新公告 ----------
    st.subheader("六、公司最新公告")
    st.caption("数据来源：东方财富，每 30 分钟刷新一次。公告内容请以交易所官网为准。")
    try:
        notices = get_notices(code)
        if notices is not None and not notices.empty:
            # 兼容不同版本 akshare 的字段名
            title_col = next((c for c in notices.columns if "标题" in c or "title" in c.lower()), None)
            date_col  = next((c for c in notices.columns if "时间" in c or "日期" in c or "date" in c.lower()), None)
            url_col   = next((c for c in notices.columns if "链接" in c or "url" in c.lower() or "http" in str(notices[c].iloc[0]).lower()), None)

            for _, row in notices.iterrows():
                title = str(row[title_col]) if title_col else "（公告）"
                date  = str(row[date_col])  if date_col  else ""
                url   = str(row[url_col])   if url_col   else ""
                link  = f'<a href="{url}" target="_blank" style="color:#00d4ff;text-decoration:none;">{title}</a>' if url else title
                st.markdown(
                    f'<div class="notice-card">{link}'
                    f'<div class="notice-date">📅 {date}</div></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("近 5 日暂无公告数据，或接口暂时不可用。")
    except Exception as e:
        st.info(f"公告获取失败：{e}")

# ============================================================
# Tab 2 — 持仓组合
# ============================================================
with tab_portfolio:
    render_portfolio()

# ============================================================
# 底部免责声明
# ============================================================
st.divider()
st.warning(
    "⚠️ 免责声明：本面板所有内容均为客观行情数据与技术指标的机械计算结果，"
    "**不构成任何投资建议，也不预测涨跌**。技术指标存在滞后性和失效可能，"
    "股市有风险，决策及后果由你自行承担。如需投资建议，请咨询持牌专业机构。")
