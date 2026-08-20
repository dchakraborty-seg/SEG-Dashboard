"""
app.py
------
WEE / M&E Monitoring Dashboard — Streamlit + Plotly

Run with:
    streamlit run app.py

Data source (in priority order):
1. A private GitHub repo, fetched automatically using credentials in
   st.secrets["data_source"] — this is the "push model" setup: you push an
   updated file to that repo on your own schedule, and every viewer of the
   deployed app sees the refreshed data on their next load / refresh click,
   without uploading anything themselves. See README.md for setup.
2. Manual upload via the sidebar (fallback — used for local dev/testing, or
   if the remote source isn't configured).
3. A local all_data.xlsx in the working directory (fallback for local dev).
"""

import io
import os
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import requests
import streamlit as st

from data_utils import load_data, apply_filters, split_multiselect_counts, DATE_COL, LOAN_SOURCE_COLS
from kpi_utils import compute_kpis, format_indian_number
from targets_utils import target_vs_achieved, METRIC_LABELS

st.set_page_config(page_title="WEE / M&E Dashboard", layout="wide", page_icon="📊")

# ---------------------------------------------------------------------------
# Brand tokens — single source of truth for every color/font in the app.
# Palette rationale: deep forest (growth, sustainability, agri-livelihoods)
# as the primary; marigold as the warm secondary (India, optimism); clay
# reserved for alerts only, never decoration. Everything below — CSS, the
# Plotly theme, and every chart's color arguments — derives from these.
# ---------------------------------------------------------------------------

INK      = "#20281F"   # primary text
FOREST   = "#1B4B3F"   # primary brand — deep forest teal-green
FOREST_D = "#0F332A"   # darker forest, for chrome (sidebar/header)
MARIGOLD = "#E3A008"   # secondary accent — warmth, pending/attention
CLAY     = "#B5533C"   # alert / negative — used sparingly, semantic only
TEAL     = "#2C6E7F"   # neutral data color
SAGE     = "#3F7D5C"   # positive / verified / green-enterprise data color
STONE    = "#C9C2B4"   # neutral / target / inactive
MIST     = "#E7E2D6"   # card borders, dividers, chart gridlines
PAPER    = "#F7F4EC"   # page background
CARD     = "#FFFFFF"   # card background

BRAND_COLORWAY = [FOREST, MARIGOLD, TEAL, CLAY, SAGE, "#7A6C50"]

DISPLAY_FONT = "'Spectral', Georgia, serif"
BODY_FONT = "'IBM Plex Sans', 'Segoe UI', sans-serif"
MONO_FONT = "'IBM Plex Mono', 'Courier New', monospace"

pio.templates["seg_brand"] = go.layout.Template(
    layout=go.Layout(
        colorway=BRAND_COLORWAY,
        font=dict(family=BODY_FONT, color=INK, size=13),
        title=dict(font=dict(family=DISPLAY_FONT, size=19, color=FOREST_D)),
        legend=dict(font=dict(size=12), bgcolor="rgba(0,0,0,0)"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=48, l=8, r=8, b=8),
        xaxis=dict(gridcolor=MIST, zerolinecolor=MIST, linecolor=MIST),
        yaxis=dict(gridcolor=MIST, zerolinecolor=MIST, linecolor=MIST),
        coloraxis=dict(colorscale=[[0, PAPER], [1, FOREST]]),
    )
)

PLOTLY_TEMPLATE = "plotly_white+seg_brand"
COLOR_SEQ = BRAND_COLORWAY


def inject_theme():
    st.markdown(f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Spectral:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
    <style>
        html, body, [class*="css"] {{ font-family: {BODY_FONT}; color: {INK}; }}
        .stApp {{ background-color: {PAPER}; }}

        h1, h2, h3 {{ font-family: {DISPLAY_FONT} !important; color: {FOREST_D} !important;
                       font-weight: 600 !important; letter-spacing: -0.01em; }}
        h2 {{ border-bottom: 2px solid {MARIGOLD}; padding-bottom: 0.35rem; margin-top: 2.2rem !important; }}
        h3 {{ color: {FOREST} !important; font-size: 1.05rem !important; }}

        /* Sidebar */
        section[data-testid="stSidebar"] {{
            background-color: {FOREST_D};
        }}
        section[data-testid="stSidebar"] * {{ color: {PAPER} !important; }}
        section[data-testid="stSidebar"] h1 {{
            font-family: {DISPLAY_FONT} !important; color: {PAPER} !important;
            border-bottom: none; font-size: 1.3rem !important;
        }}
        section[data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] {{
            background-color: {MARIGOLD} !important; color: {FOREST_D} !important;
        }}
        section[data-testid="stSidebar"] hr {{ border-color: rgba(247,244,236,0.2); }}

        /* KPI metric cards */
        div[data-testid="stMetric"] {{
            background-color: {CARD};
            border: 1px solid {MIST};
            border-left: 4px solid {FOREST};
            border-radius: 6px;
            padding: 0.9rem 1rem 0.7rem 1rem;
            box-shadow: 0 1px 3px rgba(15,51,42,0.06);
        }}
        div[data-testid="stMetricLabel"] {{
            font-family: {BODY_FONT}; font-size: 0.74rem !important;
            text-transform: uppercase; letter-spacing: 0.03em; color: {FOREST} !important;
            font-weight: 600 !important; white-space: normal !important; line-height: 1.25;
        }}
        div[data-testid="stMetricValue"] {{
            font-family: {MONO_FONT}; color: {INK} !important; font-weight: 600 !important;
            white-space: normal !important; overflow-wrap: break-word; font-size: 1.55rem !important;
            line-height: 1.2;
        }}

        /* Tabs */
        .stTabs [data-baseweb="tab"] {{ font-family: {BODY_FONT}; font-weight: 500; }}
        .stTabs [aria-selected="true"] {{ color: {FOREST} !important; }}
        .stTabs [data-baseweb="tab-highlight"] {{ background-color: {MARIGOLD} !important; }}

        /* Radio / selectbox labels */
        .stRadio label, .stSelectbox label {{ font-weight: 500 !important; color: {FOREST_D} !important; }}

        /* Dataframes */
        div[data-testid="stDataFrame"] {{ border: 1px solid {MIST}; border-radius: 6px; }}

        /* Captions */
        .stCaption, [data-testid="stCaptionContainer"] {{ color: #5B5A4F !important; }}

        /* Divider */
        hr {{ border-color: {MIST} !important; }}

        /* Report banner */
        .seg-banner {{
            background: linear-gradient(135deg, {FOREST_D} 0%, {FOREST} 100%);
            border-radius: 10px;
            padding: 1.6rem 2rem;
            margin-bottom: 1.4rem;
            border-bottom: 4px solid {MARIGOLD};
        }}
        .seg-banner h1 {{
            font-family: {DISPLAY_FONT} !important; color: {PAPER} !important;
            font-size: 1.9rem !important; margin: 0 0 0.3rem 0 !important;
            border-bottom: none !important;
        }}
        .seg-banner p {{
            color: {MIST} !important; font-family: {BODY_FONT}; font-size: 0.95rem;
            margin: 0;
        }}
        .seg-footer {{
            margin-top: 3rem; padding-top: 1rem; border-top: 1px solid {MIST};
            font-family: {BODY_FONT}; font-size: 0.78rem; color: #8A8877;
            display: flex; justify-content: space-between;
        }}
    </style>
    """, unsafe_allow_html=True)


inject_theme()

# ---------------------------------------------------------------------------
# 0. Data load
# ---------------------------------------------------------------------------

st.sidebar.title("🌾 SEG Dashboard")
st.sidebar.caption("Sustainable Entrepreneurship Group · Development Alternatives")

DEFAULT_PATH_CANDIDATES = ["all_data.parquet", "all_data.xlsx"]
REMOTE_CACHE_TTL_SECONDS = 900  # 15 min — balances "feels live" vs. GitHub API rate limits


@st.cache_data(ttl=REMOTE_CACHE_TTL_SECONDS, show_spinner="Fetching latest data…")
def fetch_remote_bytes(owner: str, repo: str, path: str, branch: str, token: str) -> bytes:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3.raw"}
    resp = requests.get(url, headers=headers, params={"ref": branch}, timeout=30)
    resp.raise_for_status()
    return resp.content


def load_remote_df():
    """Returns a cleaned dataframe from the configured private data repo, or
    None if no data-source secrets are configured (caller falls back to
    manual upload)."""
    try:
        cfg = st.secrets["data_source"] if "data_source" in st.secrets else {}
    except Exception:
        cfg = {}
    owner, repo, path, token = cfg.get("owner"), cfg.get("repo"), cfg.get("path"), cfg.get("token")
    branch = cfg.get("branch", "main")
    if not all([owner, repo, path, token]):
        return None
    raw_bytes = fetch_remote_bytes(owner, repo, path, branch, token)
    return load_data(io.BytesIO(raw_bytes))


df = load_remote_df()
using_remote = df is not None

if using_remote:
    st.sidebar.success("🔄 Auto-synced from private data repo")
    if st.sidebar.button("Refresh now"):
        fetch_remote_bytes.clear()
        st.rerun()
else:
    st.sidebar.info("No remote data source configured — using manual upload.")
    uploaded = st.sidebar.file_uploader("Upload data extract (.xlsx or .parquet)", type=["xlsx", "parquet"])
    local_default = next((p for p in DEFAULT_PATH_CANDIDATES if os.path.exists(p)), None)
    if uploaded is not None:
        df = load_data(uploaded)
    elif local_default is not None:
        df = load_data(local_default)
    else:
        st.info("Upload the data extract in the sidebar to get started.")
        st.stop()

st.sidebar.caption(f"{len(df):,} raw records loaded")

if not df["date_valid"].all():
    n_bad = int((~df["date_valid"]).sum())
    st.sidebar.warning(f"⚠️ {n_bad:,} records have an out-of-range onboarding date and are "
                        f"excluded from FY / time-trend views (but included in KPI totals).")

# ---------------------------------------------------------------------------
# 1. Global filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

def multiselect_sorted(label, col, container=st.sidebar):
    opts = sorted(df[col].dropna().unique().tolist()) if col in df.columns else []
    return container.multiselect(label, opts)

f_districts = multiselect_sorted("District", "district1")

# cascading block -> village based on selected districts
_block_pool = df[df["district1"].isin(f_districts)] if f_districts else df
f_blocks = st.sidebar.multiselect(
    "Block", sorted(_block_pool["block"].dropna().unique().tolist())
)

_village_pool = _block_pool[_block_pool["block"].isin(f_blocks)] if f_blocks else _block_pool
f_villages = st.sidebar.multiselect(
    "Village", sorted(_village_pool["village"].dropna().unique().tolist())
)

f_agencies = multiselect_sorted("Agency", "agency")
f_coordinators = multiselect_sorted("Field Coordinator", "name_of_field_coordinator")
f_phases = multiselect_sorted("Phase", "phase")

fy_opts = sorted([x for x in df["financial_year"].dropna().unique()])
f_fys = st.sidebar.multiselect("Financial Year", fy_opts)

min_d, max_d = df[DATE_COL].min(), df[DATE_COL].max()
f_date_range = st.sidebar.date_input(
    "Onboarding date range", value=(), min_value=min_d, max_value=max_d
)
f_date_range = f_date_range if isinstance(f_date_range, tuple) and len(f_date_range) == 2 else None

fdf = apply_filters(
    df,
    districts=f_districts, blocks=f_blocks, villages=f_villages,
    agencies=f_agencies, coordinators=f_coordinators,
    financial_years=f_fys, phases=f_phases, date_range=f_date_range,
)

st.sidebar.caption(f"**{len(fdf):,}** records match current filters")

_generated_at = pd.Timestamp.now().strftime("%d %b %Y, %H:%M")
st.markdown(f"""
<div class="seg-banner">
    <h1>Women's Economic Empowerment — M&E Dashboard</h1>
    <p>Executive progress, financial, sector, geographic, temporal, and sustainability/support views &middot;
    generated {_generated_at}</p>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 2. Section 1 — Executive KPI cards
# ---------------------------------------------------------------------------

st.header("1 · Executive Summary (Till-Date)")
k = compute_kpis(fdf)

r1 = st.columns(3)
r1[0].metric("Total Entrepreneurs", f"{k['total_entrepreneurs']:,}")
r1[1].metric("Youth (≤29)", f"{k['youth_entrepreneurs']:,}",
             f"{k['youth_entrepreneurs'] / max(k['total_entrepreneurs'],1) * 100:.1f}%")
r1[2].metric("Jobs Created", f"{k['total_jobs_created']:,.0f}")

r2 = st.columns(3)
r2[0].metric("High-Growth Enterprises", f"{k['high_growth_enterprises']:,}", "> 2 employees",
              delta_color="off")
r2[1].metric("Green Enterprises", f"{k['green_enterprises']:,}", f"{k['green_pct']:.1f}% of total")
r2[2].metric("CO₂ Mitigated Till Date", f"{k['co2_mitigated_till_date']:,.1f} t")

r3 = st.columns(3)
r3[0].metric("Total Savings Invested", format_indian_number(k['total_savings_invested']))
r3[1].metric("Total Loan Mobilized", format_indian_number(k['total_loan_mobilized']))
r3[2].metric("Verification Rate", f"{k['verification_rate_pct']:.1f}%",
             f"{k['data_correct_rate_pct']:.1f}% flagged correct")

st.divider()

# ---------------------------------------------------------------------------
# 3. Section 2 — Financial & Loan Breakdown
# ---------------------------------------------------------------------------

st.header("2 · Financial & Loan Breakdown")
c1, c2 = st.columns(2)

with c1:
    st.subheader("Loan amount by source")
    loan_by_source = fdf[LOAN_SOURCE_COLS].sum().sort_values(ascending=False)
    loan_by_source = loan_by_source[loan_by_source > 0]
    if len(loan_by_source):
        fig = px.treemap(
            names=[c.replace("from_", "").replace("_", " ").title() for c in loan_by_source.index],
            parents=[""] * len(loan_by_source),
            values=loan_by_source.values,
            color=loan_by_source.values,
            color_continuous_scale=[[0, PAPER], [1, SAGE]],
            template=PLOTLY_TEMPLATE,
        )
        fig.update_traces(texttemplate="%{label}<br>₹%{value:,.0f}")
        st.plotly_chart(fig, width='stretch')
    else:
        st.caption("No loan data for current filter selection.")

with c2:
    st.subheader("Loan amount by source (stacked, by sector)")
    melt_cols = [c for c in LOAN_SOURCE_COLS if c in fdf.columns]
    if len(melt_cols) and "sector1" in fdf.columns:
        stacked = (
            fdf.groupby("sector1")[melt_cols].sum()
            .rename(columns=lambda c: c.replace("from_", "").replace("_", " ").title())
        )
        stacked = stacked.loc[stacked.sum(axis=1).sort_values(ascending=False).index]
        fig = px.bar(
            stacked, barmode="stack", template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ,
            labels={"value": "Loan Amount (₹)", "sector1": "Sector", "variable": "Source"},
        )
        st.plotly_chart(fig, width='stretch')

st.subheader("Borrowers & Average Loan Size by Source")
if len(melt_cols):
    borrower_rows = []
    for col in melt_cols:
        label = col.replace("from_", "").replace("_", " ").title()
        borrowed = fdf[col] > 0
        borrower_rows.append({
            "source": label,
            "borrowers": int(borrowed.sum()),
            "avg_loan_size": float(fdf.loc[borrowed, col].mean()) if borrowed.any() else 0.0,
        })
    borrower_df = pd.DataFrame(borrower_rows)

    total_took_loan = int((fdf["total_loan_amount"] > 0).sum()) if "total_loan_amount" in fdf.columns else 0
    total_avg_loan = float(fdf.loc[fdf["total_loan_amount"] > 0, "total_loan_amount"].mean()) \
        if "total_loan_amount" in fdf.columns and total_took_loan else 0.0

    m1, m2 = st.columns(2)
    m1.metric("Total Entrepreneurs with a Loan (any source)", f"{total_took_loan:,}")
    m2.metric("Average Loan Size — All Sources Combined", format_indian_number(total_avg_loan))

    c1, c2 = st.columns(2)
    with c1:
        st.caption("Number of borrowers by source")
        bd = borrower_df.sort_values("borrowers", ascending=True)
        fig = px.bar(bd, x="borrowers", y="source", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[TEAL])
        fig.update_layout(yaxis_title="", xaxis_title="Entrepreneurs")
        st.plotly_chart(fig, width='stretch')
    with c2:
        st.caption("Average loan size by source (among that source's borrowers)")
        ad = borrower_df.sort_values("avg_loan_size", ascending=True)
        fig = px.bar(ad, x="avg_loan_size", y="source", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[MARIGOLD])
        fig.update_layout(yaxis_title="", xaxis_title="Average Loan Amount (₹)")
        st.plotly_chart(fig, width='stretch')

    st.caption("Borrower counts and per-source averages count each entrepreneur under every source they "
               "used (someone can borrow from both CLF and a bank) — they won't sum to the total above. "
               "'Average loan size' is computed only among entrepreneurs who actually borrowed from that "
               "source, not averaged over everyone.")

st.subheader("Investment vs. Loan Amount by Sector")
if {"total_investment", "total_loan_amount", "sector1"}.issubset(fdf.columns):
    plot_df = fdf.dropna(subset=["total_investment", "total_loan_amount", "sector1"])
    tab_a, tab_b = st.tabs(["Scatter", "Box plot"])
    with tab_a:
        fig = px.scatter(
            plot_df, x="total_investment", y="total_loan_amount", color="sector1",
            opacity=0.55, template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ,
            labels={"total_investment": "Total Investment (₹)", "total_loan_amount": "Total Loan Amount (₹)"},
            hover_data=["district1", "agency"] if {"district1", "agency"}.issubset(plot_df.columns) else None,
        )
        st.plotly_chart(fig, width='stretch')
    with tab_b:
        melt = plot_df.melt(id_vars="sector1", value_vars=["total_investment", "total_loan_amount"],
                             var_name="metric", value_name="amount")
        fig = px.box(
            melt, x="sector1", y="amount", color="metric", template=PLOTLY_TEMPLATE,
            color_discrete_sequence=COLOR_SEQ, points=False,
            labels={"amount": "Amount (₹)", "sector1": "Sector"},
        )
        st.plotly_chart(fig, width='stretch')

st.subheader("Loan-to-Savings Ratio")
if {"total_loan_amount", "individual_saving_invested"}.issubset(fdf.columns):
    ratio_dim = st.radio("Break down by", ["sector1", "district1"], horizontal=True,
                          format_func=lambda x: "Sector" if x == "sector1" else "District",
                          key="ratio_dim")
    if ratio_dim in fdf.columns:
        ratio_agg = fdf.groupby(ratio_dim).agg(
            loan=("total_loan_amount", "sum"),
            savings=("individual_saving_invested", "sum"),
        ).reset_index()
        ratio_agg = ratio_agg[ratio_agg["savings"] > 0]
        ratio_agg["ratio"] = ratio_agg["loan"] / ratio_agg["savings"]
        ratio_agg = ratio_agg.sort_values("ratio", ascending=True)
        fig = px.bar(
            ratio_agg, x="ratio", y=ratio_dim, orientation="h", template=PLOTLY_TEMPLATE,
            color_discrete_sequence=[CLAY],
            labels={"ratio": "Loan ÷ Savings", ratio_dim: "Sector" if ratio_dim == "sector1" else "District"},
        )
        fig.add_vline(x=1, line_dash="dash", line_color="gray",
                       annotation_text="1:1 (loan = savings)", annotation_position="top")
        st.plotly_chart(fig, width='stretch')
        st.caption("A ratio above 1 means loan mobilization outpaces personal savings invested in that "
                   "group — useful as a rough proxy for reliance on external credit vs. self-funding. "
                   "Groups with zero recorded savings are excluded to avoid a divide-by-zero distortion.")

st.divider()

# ---------------------------------------------------------------------------
# 4. Section 3 — Sector & Enterprise Deep-Dive
# ---------------------------------------------------------------------------

st.header("3 · Sector & Enterprise Deep-Dive")

c1, c2 = st.columns([1, 1])
with c1:
    st.subheader("Enterprises per sector")
    if "sector1" in fdf.columns:
        counts = fdf["sector1"].value_counts().reset_index()
        counts.columns = ["sector1", "count"]
        tab_bar, tab_pie = st.tabs(["Bar", "Pie"])
        with tab_bar:
            fig = px.bar(counts, x="count", y="sector1", orientation="h", template=PLOTLY_TEMPLATE,
                         color="sector1", color_discrete_sequence=COLOR_SEQ)
            fig.update_layout(showlegend=False, yaxis_title="", xaxis_title="Entrepreneurs")
            st.plotly_chart(fig, width='stretch')
        with tab_pie:
            fig = px.pie(counts, names="sector1", values="count", template=PLOTLY_TEMPLATE,
                         color_discrete_sequence=COLOR_SEQ, hole=0.35)
            fig.update_traces(textinfo="percent+label")
            st.plotly_chart(fig, width='stretch')

with c2:
    st.subheader("Top 10 enterprise types (overall)")
    if "enterprise_type" in fdf.columns:
        top10 = fdf["enterprise_type"].value_counts().head(10).reset_index()
        top10.columns = ["enterprise_type", "count"]
        fig = px.bar(top10, x="count", y="enterprise_type", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[SAGE])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Entrepreneurs")
        st.plotly_chart(fig, width='stretch')

if {"sector1", "enterprise_type"}.issubset(fdf.columns):
    st.subheader("Top 10 enterprise types — by selected sector")
    sector_pick = st.selectbox("Sector", sorted(fdf["sector1"].dropna().unique()))
    sub = fdf[fdf["sector1"] == sector_pick]
    top10_sector = sub["enterprise_type"].value_counts().head(10).reset_index()
    top10_sector.columns = ["enterprise_type", "count"]
    fig = px.bar(top10_sector, x="count", y="enterprise_type", orientation="h", template=PLOTLY_TEMPLATE,
                 color_discrete_sequence=[FOREST])
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Entrepreneurs")
    st.plotly_chart(fig, width='stretch')

c3, c4 = st.columns(2)
with c3:
    st.subheader("Gender distribution by sector")
    if {"sector1", "gender"}.issubset(fdf.columns):
        ct = fdf.groupby(["sector1", "gender"]).size().reset_index(name="count")
        fig = px.bar(ct, x="sector1", y="count", color="gender", barmode="stack",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ)
        fig.update_layout(xaxis_title="", yaxis_title="Entrepreneurs")
        st.plotly_chart(fig, width='stretch')

with c4:
    st.subheader("Social category distribution by sector")
    if {"sector1", "social_category"}.issubset(fdf.columns):
        ct = fdf.groupby(["sector1", "social_category"]).size().reset_index(name="count")
        fig = px.bar(ct, x="sector1", y="count", color="social_category", barmode="stack",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ)
        fig.update_layout(xaxis_title="", yaxis_title="Entrepreneurs")
        st.plotly_chart(fig, width='stretch')

st.subheader("District × Sector heatmap")
if {"district1", "sector1"}.issubset(fdf.columns):
    pivot = pd.crosstab(fdf["district1"], fdf["sector1"])
    if pivot.size:
        fig = px.imshow(
            pivot, template=PLOTLY_TEMPLATE, color_continuous_scale=[[0, PAPER], [1, FOREST]], aspect="auto",
            labels=dict(x="Sector", y="District", color="Entrepreneurs"),
        )
        fig.update_layout(xaxis_tickangle=-35)
        st.plotly_chart(fig, width='stretch')
        st.caption("Darker cells = more entrepreneurs in that district-sector combination. "
                   "Useful for spotting which districts are concentrated in a narrow set of sectors "
                   "vs. diversified.")

st.divider()

# ---------------------------------------------------------------------------
# 5. Section 4 — Geographic & Agency Performance
# ---------------------------------------------------------------------------

st.header("4 · Geographic & Agency Performance")

st.subheader("Agency-wise Progress")
if "agency" in fdf.columns:
    agency_agg = fdf.groupby("agency").agg(
        onboarded=("ID", "nunique") if "ID" in fdf.columns else ("agency", "size"),
        jobs_created=("total_employees", "sum"),
        green_pct=("is_green_flag", "mean"),
        loan_mobilized=("total_loan_amount", "sum"),
    ).reset_index()
    agency_agg["green_pct"] = agency_agg["green_pct"] * 100
    agency_agg = agency_agg.sort_values("onboarded", ascending=False)

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.caption("Entrepreneurs Onboarded")
        fig = px.bar(agency_agg.sort_values("onboarded"), x="onboarded", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[FOREST])
        fig.update_layout(yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')
    with a2:
        st.caption("Jobs Created")
        fig = px.bar(agency_agg.sort_values("jobs_created"), x="jobs_created", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[TEAL])
        fig.update_layout(yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')
    with a3:
        st.caption("Green %")
        fig = px.bar(agency_agg.sort_values("green_pct"), x="green_pct", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[SAGE])
        fig.update_layout(yaxis_title="", xaxis_title="%")
        st.plotly_chart(fig, width='stretch')
    with a4:
        st.caption("Loan Mobilized (₹)")
        fig = px.bar(agency_agg.sort_values("loan_mobilized"), x="loan_mobilized", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[MARIGOLD])
        fig.update_layout(yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')
else:
    st.caption("No agency field available for the current filter selection.")

st.divider()
st.subheader("District / Agency Comparison")

geo_dim = st.radio("Break down by", ["district1", "agency"], horizontal=True,
                    format_func=lambda x: "District" if x == "district1" else "Agency")

if geo_dim in fdf.columns:
    agg = fdf.groupby(geo_dim).agg(
        onboarded=("ID", "nunique") if "ID" in fdf.columns else (geo_dim, "size"),
        green_pct=("is_green_flag", "mean"),
        loan_mobilized=("total_loan_amount", "sum"),
    ).reset_index()
    agg["green_pct"] = agg["green_pct"] * 100
    agg = agg.sort_values("onboarded", ascending=False)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.caption("Onboarded")
        fig = px.bar(agg, x="onboarded", y=geo_dim, orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[TEAL])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')
    with c2:
        st.caption("Green %")
        fig = px.bar(agg.sort_values("green_pct"), x="green_pct", y=geo_dim, orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[SAGE])
        fig.update_layout(yaxis_title="", xaxis_title="%")
        st.plotly_chart(fig, width='stretch')
    with c3:
        st.caption("Loan Mobilized (₹)")
        fig = px.bar(agg.sort_values("loan_mobilized"), x="loan_mobilized", y=geo_dim, orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[CLAY])
        fig.update_layout(yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')

st.subheader(f"Data quality status by {'district' if geo_dim=='district1' else 'agency'}")
if "verification_status" in fdf.columns:
    qc = fdf.groupby([geo_dim, "verification_status"]).size().reset_index(name="count")
    fig = px.bar(qc, x=geo_dim, y="count", color="verification_status", barmode="stack",
                 template=PLOTLY_TEMPLATE,
                 color_discrete_map={
                     "pending": MARIGOLD, "verified - correct": SAGE,
                     "verified - issue flagged": CLAY,
                 })
    fig.update_layout(xaxis_title="", yaxis_title="Records")
    st.plotly_chart(fig, width='stretch')

st.subheader("Target vs. Achieved (official district targets)")
targets_merged, untracked_districts = target_vs_achieved(fdf)
if len(targets_merged):
    metric_pick = st.selectbox(
        "Metric", list(METRIC_LABELS.values()),
        index=list(METRIC_LABELS.values()).index("Total Enterprises"),
    )
    metric_key = [k for k, v in METRIC_LABELS.items() if v == metric_pick][0]
    tv = targets_merged[targets_merged["metric"] == metric_key].sort_values("target", ascending=False)

    fig = go.Figure()
    fig.add_bar(x=tv["district1"], y=tv["target"], name="Target", marker_color=STONE)
    fig.add_bar(x=tv["district1"], y=tv["achieved_official"], name="Achieved (official)",
                marker_color=TEAL)
    fig.update_layout(barmode="group", template=PLOTLY_TEMPLATE, yaxis_title=metric_pick,
                       xaxis_title="", legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width='stretch')

    with st.expander("Compare official 'Achieved' vs. live count in this data extract"):
        st.caption(
            "These two numbers are **not expected to match**: 'Achieved (official)' is the "
            "reported figure from the programme's targets sheet as of its own cutoff date; "
            "'Live extract count' is counted fresh from whatever data extract is currently "
            "loaded and filtered, and typically includes unverified/in-progress records the "
            "official figure may not. Useful for spotting extract lag, not as a reconciliation."
        )
        st.dataframe(
            tv[["district1", "target", "achieved_official", "pct_official", "live_extract_count"]]
            .rename(columns={
                "district1": "District", "target": "Target",
                "achieved_official": "Achieved (official)", "pct_official": "% (official)",
                "live_extract_count": "Live extract count",
            }),
            width='stretch', hide_index=True,
        )

    if untracked_districts:
        st.caption(
            "⚠️ No target row found for: " + ", ".join(d.title() for d in untracked_districts) +
            " — these districts appear in the data extract but not in the targets sheet, "
            "so they're excluded from this comparison rather than guessed at."
        )
else:
    st.caption("No target data available for the current filter selection.")

st.divider()

# ---------------------------------------------------------------------------
# 6. Section 5 — Temporal Trends
# ---------------------------------------------------------------------------

st.header("5 · Temporal Trends")

tdf = fdf[fdf["date_valid"]] if "date_valid" in fdf.columns else fdf

st.subheader("FY-over-FY growth")
if "financial_year" in tdf.columns:
    fy_agg = tdf.groupby("financial_year").agg(
        entrepreneurs=("ID", "nunique") if "ID" in tdf.columns else ("financial_year", "size"),
        jobs=("total_employees", "sum"),
        loan_mobilized=("total_loan_amount", "sum"),
    ).reset_index().sort_values("financial_year")
    fig = go.Figure()
    fig.add_bar(x=fy_agg["financial_year"], y=fy_agg["entrepreneurs"], name="Entrepreneurs",
                marker_color=TEAL)
    fig.add_trace(go.Scatter(x=fy_agg["financial_year"], y=fy_agg["jobs"], name="Jobs Created",
                              yaxis="y2", mode="lines+markers", line=dict(color=CLAY)))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        yaxis=dict(title="Entrepreneurs"),
        yaxis2=dict(title="Jobs Created", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.1),
    )
    st.plotly_chart(fig, width='stretch')

st.subheader("Enterprise growth — new vs. existing, over time")
if {"onboard_month", "new_or_existing"}.issubset(tdf.columns):
    growth = tdf.groupby(["onboard_month", "new_or_existing"]).size().reset_index(name="count")
    fig = px.area(
        growth, x="onboard_month", y="count", color="new_or_existing", template=PLOTLY_TEMPLATE,
        color_discrete_sequence=[TEAL, SAGE],
        labels={"onboard_month": "Month", "count": "Entrepreneurs", "new_or_existing": "Enterprise Status"},
    )
    st.plotly_chart(fig, width='stretch')

st.subheader("Current FY drill-down")
current_fy_opts = sorted(tdf["financial_year"].dropna().unique()) if "financial_year" in tdf.columns else []
if current_fy_opts:
    default_fy = current_fy_opts[-1]
    fy_pick = st.selectbox("Financial Year", current_fy_opts, index=len(current_fy_opts) - 1)
    fy_df = tdf[tdf["financial_year"] == fy_pick]

    granularity = st.radio("Granularity", ["Monthly", "Weekly"], horizontal=True)
    period_col = "onboard_month" if granularity == "Monthly" else "onboard_week"

    period_agg = fy_df.groupby(period_col).agg(
        entrepreneurs=("ID", "nunique") if "ID" in fy_df.columns else (period_col, "size"),
        jobs=("total_employees", "sum"),
        loan_mobilized=("total_loan_amount", "sum"),
    ).reset_index().sort_values(period_col)

    fig = px.bar(
        period_agg, x=period_col, y="entrepreneurs", template=PLOTLY_TEMPLATE,
        color_discrete_sequence=[TEAL],
        labels={period_col: granularity, "entrepreneurs": "New Onboarding"},
    )
    st.plotly_chart(fig, width='stretch')

    c1, c2 = st.columns(2)
    with c1:
        fig = px.line(period_agg, x=period_col, y="jobs", markers=True, template=PLOTLY_TEMPLATE,
                       labels={period_col: granularity, "jobs": "Jobs Created"},
                       color_discrete_sequence=[SAGE])
        st.plotly_chart(fig, width='stretch')
    with c2:
        fig = px.line(period_agg, x=period_col, y="loan_mobilized", markers=True, template=PLOTLY_TEMPLATE,
                       labels={period_col: granularity, "loan_mobilized": "Loan Mobilized (₹)"},
                       color_discrete_sequence=[CLAY])
        st.plotly_chart(fig, width='stretch')

st.subheader("Monthly trajectories — key indicators (multi-line, indexed)")
if "onboard_month" in tdf.columns:
    monthly = tdf.groupby("onboard_month").agg(
        Entrepreneurs=("ID", "nunique") if "ID" in tdf.columns else ("onboard_month", "size"),
        Jobs=("total_employees", "sum"),
        Green_Enterprises=("is_green_flag", "sum"),
        CO2_Mitigated=("co2_mitigated_tonnes_per_month", "sum"),
    ).reset_index().sort_values("onboard_month")

    # index each series to 100 at its first period so scales are comparable
    plot_cols = ["Entrepreneurs", "Jobs", "Green_Enterprises", "CO2_Mitigated"]
    indexed = monthly.copy()
    for c in plot_cols:
        base = indexed[c].iloc[0] if len(indexed) and indexed[c].iloc[0] not in (0, np.nan) else 1
        indexed[c] = indexed[c] / base * 100

    melt = indexed.melt(id_vars="onboard_month", value_vars=plot_cols,
                         var_name="indicator", value_name="index_value")
    fig = px.line(melt, x="onboard_month", y="index_value", color="indicator", markers=True,
                  template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ,
                  labels={"onboard_month": "Month", "index_value": "Index (first month = 100)"})
    st.plotly_chart(fig, width='stretch')
    st.caption("Series are indexed to 100 at the first month in range so indicators with very "
               "different scales (e.g. CO₂ tonnes vs. entrepreneur counts) can be compared on one chart. "
               "Toggle raw values below.")
    with st.expander("Show raw monthly values"):
        st.dataframe(monthly, width='stretch')

st.divider()

# ---------------------------------------------------------------------------
# 7. Section 6 — Sustainability & Support
# ---------------------------------------------------------------------------

st.header("6 · Sustainability & Support")

# --- Green energy adoption --------------------------------------------------
st.subheader("Green Energy Adoption")
c1, c2, c3 = st.columns(3)

with c1:
    st.caption("Solar adoption")
    if "are_you_using_solar_electricity" in fdf.columns:
        solar_counts = fdf["are_you_using_solar_electricity"].value_counts().reset_index()
        solar_counts.columns = ["status", "count"]
        fig = px.pie(solar_counts, names="status", values="count", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[SAGE, STONE], hole=0.4)
        fig.update_traces(textinfo="percent+label")
        st.plotly_chart(fig, width='stretch')

with c2:
    st.caption("Solar adoption by district")
    if {"are_you_using_solar_electricity", "district1"}.issubset(fdf.columns):
        solar_by_d = fdf.groupby("district1")["are_you_using_solar_electricity"].apply(
            lambda s: (s == "yes").mean() * 100
        ).reset_index(name="solar_pct").sort_values("solar_pct")
        fig = px.bar(solar_by_d, x="solar_pct", y="district1", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[MARIGOLD])
        fig.update_layout(yaxis_title="", xaxis_title="% using solar")
        st.plotly_chart(fig, width='stretch')

with c3:
    st.caption("Solar panel capacity (kW) — among adopters")
    if "solar_panel_capacity" in fdf.columns:
        cap = fdf.loc[fdf["solar_panel_capacity"] > 0, "solar_panel_capacity"].dropna()
        if len(cap):
            fig = px.histogram(cap, template=PLOTLY_TEMPLATE, color_discrete_sequence=[TEAL],
                               labels={"value": "Capacity (kW)"})
            fig.update_layout(showlegend=False, yaxis_title="Enterprises")
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No solar capacity data for current filter selection.")

# --- Waste & water -----------------------------------------------------------
st.subheader("Waste & Water")
c1, c2, c3 = st.columns(3)

with c1:
    st.caption("Waste treatment status")
    if "do_you_treat_your_waste" in fdf.columns:
        treat_counts = fdf["do_you_treat_your_waste"].value_counts().reset_index()
        treat_counts.columns = ["status", "count"]
        fig = px.pie(treat_counts, names="status", values="count", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[SAGE, CLAY], hole=0.4)
        fig.update_traces(textinfo="percent+label")
        st.plotly_chart(fig, width='stretch')

with c2:
    st.caption("Water reuse")
    if "do_you_reuse_your_water" in fdf.columns:
        reuse_counts = fdf["do_you_reuse_your_water"].value_counts().reset_index()
        reuse_counts.columns = ["status", "count"]
        fig = px.pie(reuse_counts, names="status", values="count", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[TEAL, STONE], hole=0.4)
        fig.update_traces(textinfo="percent+label")
        st.plotly_chart(fig, width='stretch')

with c3:
    st.caption("Water sources used")
    if "water_sources" in fdf.columns:
        src_counts = split_multiselect_counts(fdf["water_sources"]).head(8).reset_index()
        src_counts.columns = ["source", "count"]
        fig = px.bar(src_counts, x="count", y="source", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[TEAL])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Enterprises")
        st.plotly_chart(fig, width='stretch')

st.caption("Waste and water fields are sparsely filled in the current extract — treat these as "
           "directional, not comprehensive, until more enterprises report on this section.")

# --- Support needs vs. support provided --------------------------------------
st.subheader("Support Needs vs. Support Provided")
if {"further_support_required", "support_provided_by_da"}.issubset(fdf.columns):
    needed = split_multiselect_counts(fdf["further_support_required"]).rename("Further support required")
    provided = split_multiselect_counts(fdf["support_provided_by_da"]).rename("Support already provided")
    gap = pd.concat([needed, provided], axis=1).fillna(0).reset_index().rename(columns={"index": "category"})
    gap = gap.sort_values("Further support required", ascending=True)

    fig = go.Figure()
    fig.add_bar(y=gap["category"], x=gap["Further support required"], name="Further support required",
                orientation="h", marker_color=CLAY)
    fig.add_bar(y=gap["category"], x=gap["Support already provided"], name="Support already provided",
                orientation="h", marker_color=SAGE)
    fig.update_layout(barmode="group", template=PLOTLY_TEMPLATE, xaxis_title="Mentions",
                       yaxis_title="", legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width='stretch')
    st.caption("Categories are split out of multi-select responses (e.g. 'financial,marketing' counts "
               "toward both Financial and Marketing) — bars are mention counts, not unique entrepreneurs, "
               "so they won't sum to the total record count.")

    with st.expander("Most common specific support requests (free text)"):
        if "detail_of_support_required" in fdf.columns:
            detail = fdf["detail_of_support_required"].dropna().value_counts().head(15).reset_index()
            detail.columns = ["Request", "Mentions"]
            st.dataframe(detail, width='stretch', hide_index=True)

st.markdown(f"""
<div class="seg-footer">
    <span>Development Alternatives &middot; Sustainable Entrepreneurship Group</span>
    <span>Generated {_generated_at} &middot; {len(fdf):,} of {len(df):,} records shown</span>
</div>
""", unsafe_allow_html=True)
