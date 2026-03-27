"""
정치 테마주 분석 대시보드 — Streamlit 앱
실행: streamlit run app.py
"""
import sys
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src"))

from collectors.stock_collector import StockCollector
from collectors.poll_collector import PollCollector
from analyzers.theme_mapper import ThemeMapper

matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False

st.set_page_config(
    page_title="정치 테마주 분석",
    page_icon="🗳️",
    layout="wide",
)

# ── 캐시 ──────────────────────────────────────────────
@st.cache_resource
def get_components():
    return StockCollector(), ThemeMapper(), PollCollector()

@st.cache_data(ttl=600)
def load_screening(tickers, surge_ratio):
    sc, _, _ = get_components()
    results = sc.screen_theme_stocks(tickers, surge_ratio=surge_ratio)
    for r in results:
        r["surge"] = bool(r.get("surge", False))
    return results

# ── 사이드바 ──────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 설정")
    surge_ratio = st.slider("거래량 급등 기준 (배수)", 1.5, 5.0, 3.0, 0.5)
    show_all = st.checkbox("전체 종목 표시", value=True)
    st.divider()
    if st.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"최종 로드: {datetime.now().strftime('%H:%M:%S')}")

# ── 메인 ──────────────────────────────────────────────
sc, tm, pc = get_components()

st.title("🗳️ 정치 테마주 분석 대시보드")
st.caption("제9회 전국동시지방선거 (2026.06.03) 기반 테마주 실시간 모니터링")

# ── 선거 현황 배너 ─────────────────────────────────────
phase = pc.get_election_phase()
next_el = pc.get_next_election_info()
days = phase.get("days_until_election", "?")

col1, col2, col3, col4 = st.columns(4)
col1.metric("선거까지", f"D-{days}", next_el.get("name", ""))
col2.metric("현재 단계", phase.get("phase", "-"))
col3.metric("시그널", phase.get("signal", "-"))
col4.metric("추적 종목", f"{len(tm.get_all_tickers())}개")

signal_color = {
    "매수 관심": "🟢",
    "강한 매수 시그널": "🟢",
    "고점 주의, 분할 매도 고려": "🟡",
    "매도 시그널": "🔴",
    "관망": "⚪",
}.get(phase.get("signal", ""), "⚪")

st.info(f"{signal_color} **{phase.get('phase', '')}**: {phase.get('pattern', '')}")

st.divider()

# ── 스크리닝 결과 ─────────────────────────────────────
st.subheader("📊 오늘의 정치 테마주 스크리닝")

with st.spinner("KRX에서 데이터 수집 중..."):
    tickers = tm.get_all_tickers()
    results = load_screening(tuple(tickers), surge_ratio)

df = pd.DataFrame(results)
df = df[df["close"] > 0]
df = df.sort_values("change_pct", ascending=False)

# 요약 지표
up = (df["change_pct"] > 0).sum()
down = (df["change_pct"] < 0).sum()
surge_count = df["surge"].sum()

c1, c2, c3 = st.columns(3)
c1.metric("상승 종목", f"{up}개", f"+{up}")
c2.metric("하락 종목", f"{down}개", f"-{down}")
c3.metric("거래량 급등", f"{surge_count}개", "⚠️" if surge_count > 0 else "")

# 테이블
df_display = df[["name", "ticker", "close", "change_pct", "ratio", "surge"]].copy()
df_display.columns = ["종목명", "코드", "종가", "등락률(%)", "거래량배수", "급등여부"]
df_display["종가"] = df_display["종가"].apply(lambda x: f"{x:,}")
df_display["등락률(%)"] = df_display["등락률(%)"].apply(lambda x: f"{x:+.2f}%")
df_display["거래량배수"] = df_display["거래량배수"].apply(lambda x: f"{x:.2f}x")
df_display["급등여부"] = df_display["급등여부"].apply(lambda x: "🔴 급등" if x else "-")

st.dataframe(df_display, use_container_width=True, height=400)

# ── 차트 ──────────────────────────────────────────────
st.divider()
st.subheader("📈 등락률 차트")

fig, ax = plt.subplots(figsize=(12, max(6, len(df) * 0.4)))
colors = ["#e74c3c" if x > 0 else "#3498db" for x in df["change_pct"]]
bars = ax.barh(df["name"], df["change_pct"], color=colors)
ax.axvline(0, color="black", linewidth=0.8)
ax.set_xlabel("등락률 (%)")
ax.set_title(f"정치 테마주 등락률 | {datetime.now().strftime('%Y-%m-%d')} | D-{days} 지방선거")
ax.grid(axis="x", alpha=0.3)

for bar, val in zip(bars, df["change_pct"]):
    ax.text(
        val + (0.05 if val >= 0 else -0.05),
        bar.get_y() + bar.get_height() / 2,
        f"{val:+.2f}%", va="center",
        ha="left" if val >= 0 else "right", fontsize=8,
    )

plt.tight_layout()
st.pyplot(fig)

# ── 후보별 관련주 ──────────────────────────────────────
st.divider()
st.subheader("🏛️ 후보별 관련주")

politicians = ["이재명", "정원오", "박찬대", "김동연", "박형준", "오세훈"]
tabs = st.tabs(politicians)

for i, pol in enumerate(politicians):
    with tabs[i]:
        stocks = tm.get_tickers_for_politician(pol)
        if not stocks:
            # local_candidates_2026에서도 검색
            data = tm.data.get("local_candidates_2026", [])
            for cand in data:
                if cand.get("name") == pol:
                    stocks = cand.get("related_stocks", [])
                    break

        if stocks:
            pol_tickers = [s["ticker"] for s in stocks]
            pol_results = [r for r in results if r["ticker"] in pol_tickers]
            df_pol = pd.DataFrame(pol_results)
            if not df_pol.empty and "close" in df_pol.columns:
                df_pol = df_pol[df_pol["close"] > 0]
                df_pol_display = df_pol[["name", "ticker", "close", "change_pct", "ratio"]].copy()
                df_pol_display.columns = ["종목명", "코드", "종가", "등락률(%)", "거래량배수"]
                df_pol_display["등락률(%)"] = df_pol_display["등락률(%)"].apply(lambda x: f"{x:+.2f}%")
                st.dataframe(df_pol_display, use_container_width=True)

                for s in stocks:
                    st.caption(f"• {s['name']} ({s['ticker']}): {s.get('relation','')}")
            else:
                st.info("오늘 거래 데이터 없음")
        else:
            st.info("관련주 데이터 없음")

# ── 선거 타임라인 ──────────────────────────────────────
st.divider()
st.subheader("📅 테마주 시즌 타임라인")

timeline = pc._calendar.get("theme_stock_timeline", [])
for t in timeline:
    icon = {"매수 관심": "🟢", "강한 매수 시그널": "🟢",
            "고점 주의, 분할 매도 고려": "🟡", "매도 시그널": "🔴", "관망": "⚪"}.get(t["signal"], "⚪")
    is_current = t["phase"] == phase.get("phase", "")
    prefix = "**▶ [현재]**" if is_current else ""
    st.markdown(
        f"{icon} {prefix} **{t['phase']}** ({t['period']})\n\n"
        f"  패턴: {t['pattern']} | 시그널: `{t['signal']}`"
    )

st.divider()
st.caption("⚠️ 본 대시보드는 교육/연구 목적으로 제작되었으며, 투자 권유가 아닙니다.")
