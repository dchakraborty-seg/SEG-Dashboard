"""

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
import requests
import streamlit as st

from data_utils import load_data, apply_filters, DATE_COL, LOAN_SOURCE_COLS
from kpi_utils import compute_kpis, format_indian_number
from targets_utils import target_vs_achieved, METRIC_LABELS

st.set_page_config(page_title="WEE / M&E Dashboard", layout="wide", page_icon="📊")

PLOTLY_TEMPLATE = "plotly_white"
COLOR_SEQ = px.colors.qualitative.Set2

# ---------------------------------------------------------------------------
# 0. Data load
# ---------------------------------------------------------------------------

st.sidebar.title("📊 M&E Dashboard")

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

st.title("Women's Economic Empowerment — M&E Dashboard")
st.caption("Executive progress, financial, sector, geographic and temporal views. "
           "Use the sidebar to filter; all sections below respond to the same filter set.")

# ---------------------------------------------------------------------------
# 2. Section 1 — Executive KPI cards
# ---------------------------------------------------------------------------

st.header("1 · Executive Summary (Till-Date)")
k = compute_kpis(fdf)

r1 = st.columns(5)
r1[0].metric("Total Entrepreneurs", f"{k['total_entrepreneurs']:,}")
r1[1].metric("Youth (≤29)", f"{k['youth_entrepreneurs']:,}",
             f"{k['youth_entrepreneurs'] / max(k['total_entrepreneurs'],1) * 100:.1f}%")
r1[2].metric("Jobs Created", f"{k['total_jobs_created']:,.0f}")
r1[3].metric("High-Growth Enterprises (>2 employees)", f"{k['high_growth_enterprises']:,}")
r1[4].metric("Green Enterprises", f"{k['green_enterprises']:,}", f"{k['green_pct']:.1f}% of total")

r2 = st.columns(4)
r2[0].metric("CO₂ Mitigated Till Date", f"{k['co2_mitigated_till_date']:,.1f} t")
r2[1].metric("Total Savings Invested", format_indian_number(k['total_savings_invested']))
r2[2].metric("Total Loan Mobilized", format_indian_number(k['total_loan_mobilized']))
r2[3].metric("Verification Rate", f"{k['verification_rate_pct']:.1f}%",
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
            color_continuous_scale="Greens",
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
        fig = px.bar(counts, x="count", y="sector1", orientation="h", template=PLOTLY_TEMPLATE,
                     color="sector1", color_discrete_sequence=COLOR_SEQ)
        fig.update_layout(showlegend=False, yaxis_title="", xaxis_title="Entrepreneurs")
        st.plotly_chart(fig, width='stretch')

with c2:
    st.subheader("Top 10 enterprise types (overall)")
    if "enterprise_type" in fdf.columns:
        top10 = fdf["enterprise_type"].value_counts().head(10).reset_index()
        top10.columns = ["enterprise_type", "count"]
        fig = px.bar(top10, x="count", y="enterprise_type", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=["#4C956C"])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Entrepreneurs")
        st.plotly_chart(fig, width='stretch')

if {"sector1", "enterprise_type"}.issubset(fdf.columns):
    st.subheader("Top 10 enterprise types — by selected sector")
    sector_pick = st.selectbox("Sector", sorted(fdf["sector1"].dropna().unique()))
    sub = fdf[fdf["sector1"] == sector_pick]
    top10_sector = sub["enterprise_type"].value_counts().head(10).reset_index()
    top10_sector.columns = ["enterprise_type", "count"]
    fig = px.bar(top10_sector, x="count", y="enterprise_type", orientation="h", template=PLOTLY_TEMPLATE,
                 color_discrete_sequence=["#2C6E49"])
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

st.divider()

# ---------------------------------------------------------------------------
# 5. Section 4 — Geographic & Agency Performance
# ---------------------------------------------------------------------------

st.header("4 · Geographic & Agency Performance")

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
                     color_discrete_sequence=["#3A6EA5"])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')
    with c2:
        st.caption("Green %")
        fig = px.bar(agg.sort_values("green_pct"), x="green_pct", y=geo_dim, orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=["#4C956C"])
        fig.update_layout(yaxis_title="", xaxis_title="%")
        st.plotly_chart(fig, width='stretch')
    with c3:
        st.caption("Loan Mobilized (₹)")
        fig = px.bar(agg.sort_values("loan_mobilized"), x="loan_mobilized", y=geo_dim, orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=["#BC4749"])
        fig.update_layout(yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width='stretch')

st.subheader(f"Data quality status by {'district' if geo_dim=='district1' else 'agency'}")
if "verification_status" in fdf.columns:
    qc = fdf.groupby([geo_dim, "verification_status"]).size().reset_index(name="count")
    fig = px.bar(qc, x=geo_dim, y="count", color="verification_status", barmode="stack",
                 template=PLOTLY_TEMPLATE,
                 color_discrete_map={
                     "pending": "#E0A458", "verified - correct": "#4C956C",
                     "verified - issue flagged": "#BC4749",
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
    fig.add_bar(x=tv["district1"], y=tv["target"], name="Target", marker_color="#B7C4CF")
    fig.add_bar(x=tv["district1"], y=tv["achieved_official"], name="Achieved (official)",
                marker_color="#3A6EA5")
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
                marker_color="#3A6EA5")
    fig.add_trace(go.Scatter(x=fy_agg["financial_year"], y=fy_agg["jobs"], name="Jobs Created",
                              yaxis="y2", mode="lines+markers", line=dict(color="#BC4749")))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        yaxis=dict(title="Entrepreneurs"),
        yaxis2=dict(title="Jobs Created", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.1),
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
        color_discrete_sequence=["#3A6EA5"],
        labels={period_col: granularity, "entrepreneurs": "New Onboarding"},
    )
    st.plotly_chart(fig, width='stretch')

    c1, c2 = st.columns(2)
    with c1:
        fig = px.line(period_agg, x=period_col, y="jobs", markers=True, template=PLOTLY_TEMPLATE,
                       labels={period_col: granularity, "jobs": "Jobs Created"},
                       color_discrete_sequence=["#4C956C"])
        st.plotly_chart(fig, width='stretch')
    with c2:
        fig = px.line(period_agg, x=period_col, y="loan_mobilized", markers=True, template=PLOTLY_TEMPLATE,
                       labels={period_col: granularity, "loan_mobilized": "Loan Mobilized (₹)"},
                       color_discrete_sequence=["#BC4749"])
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
