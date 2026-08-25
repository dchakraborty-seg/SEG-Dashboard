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
# Dark canvas, cyan focus accent, neutral ramp for everything else. Color
# encodes meaning, never decoration: gray = baseline/target/inactive,
# cyan = focus series, amber = attention, coral = exception,
# green = confirmed/positive.
# ---------------------------------------------------------------------------

DEEP     = "#071A2A"
NAVY     = "#0D2A3D"
CYAN     = "#18B6F2"
SKY      = "#73D2F5"
INK      = "#EAF2F7"
MUTED    = "#8FA6B7"
GRAY     = "#71889A"
LINE     = "#203A4D"
PAGE     = "#081522"
PANEL    = "#0A1C2B"
PAPER    = "#0D2233"
ON_DARK  = "#EAF2F7"
ON_DARK_M = "#9FB3C1"
HAIR_D   = "rgba(255,255,255,0.10)"
GRID_D   = "rgba(255,255,255,0.08)"
LEAD     = "#5CC8F2"
CARD     = "#0D2233"
AMBER    = "#F5B942"
CORAL    = "#FF647C"
GREEN    = "#35C98A"


# Ordered so the first three carry the most weight; categorical charts stay
# legible in grayscale print because the ramp also varies in lightness.
BRAND_COLORWAY = [CYAN, SKY, GREEN, AMBER, CORAL, "#9FB3C1", "#5B9FE8", "#D7E6EF"]

DISPLAY_FONT = "'Inter', 'Segoe UI', Arial, sans-serif"
BODY_FONT = "'Inter', 'Segoe UI', Arial, sans-serif"
MONO_FONT = "'Inter', 'Segoe UI', Arial, sans-serif"

pio.templates["mck_brand"] = go.layout.Template(
    layout=go.Layout(
        colorway=BRAND_COLORWAY,
        font=dict(family=BODY_FONT, color=ON_DARK, size=12),
        title=dict(font=dict(family=BODY_FONT, size=14, color=ON_DARK), x=0, xanchor="left"),
        legend=dict(font=dict(size=11, color=ON_DARK_M), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, x=0,
                    title=dict(text="")),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=34, l=4, r=8, b=4),
        hoverlabel=dict(bgcolor=PAPER, bordercolor=PAPER,
                        font=dict(family=BODY_FONT, color=INK, size=12)),
        # Consulting-chart convention: no vertical gridlines, hairline
        # horizontals only, axis lines dropped so the data carries the chart.
        xaxis=dict(showgrid=False, zeroline=False, linecolor=GRID_D, ticks="outside",
                   ticklen=4, tickcolor=GRID_D, tickfont=dict(size=11, color=ON_DARK_M),
                   title=dict(font=dict(size=11, color=ON_DARK_M))),
        yaxis=dict(gridcolor=GRID_D, gridwidth=1, zeroline=False, showline=False,
                   tickfont=dict(size=11, color=ON_DARK_M),
                   title=dict(font=dict(size=11, color=ON_DARK_M))),
        coloraxis=dict(colorscale=[[0, "#14304A"], [1, CYAN]],
                       colorbar=dict(outlinewidth=0, thickness=10, len=0.8,
                                     tickfont=dict(size=10, color=ON_DARK_M))),
    )
)

PLOTLY_TEMPLATE = "mck_brand"
COLOR_SEQ = BRAND_COLORWAY

# Chart toolbar: appears on hover, keeps zoom / pan / reset / PNG download,
# drops the selection tools nobody uses on these charts. Exported PNGs get a
# solid dark background — the charts are transparent on screen, which would
# otherwise download as a transparent image with unreadable light text.
PLOTLY_CONFIG = {
    "displayModeBar": "hover",
    "displaylogo": False,
    "scrollZoom": False,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                               "hoverClosestCartesian", "hoverCompareCartesian",
                               "toggleSpikelines"],
    "toImageButtonOptions": {
        "format": "png",
        "filename": "seg_dashboard_chart",
        "scale": 2,
    },
}


def compact_number(v) -> str:
    """Short label for a data point: crore / lakh for money-scale figures,
    thousands separators below that, one decimal for fractional values such
    as percentages."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if pd.isna(v):
        return ""
    a = abs(v)
    if a >= 1e7:
        return f"{v / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"{v / 1e5:,.2f} L"
    if a >= 1000:
        return f"{v:,.0f}"
    if a and abs(v - round(v)) > 1e-9:
        return f"{v:,.1f}"
    return f"{v:,.0f}"


def show(fig, height: int = 340, labels: bool = True):
    """Single exit point for every chart, so spacing, hover behaviour and
    height stay identical across all six sections instead of drifting.
    Also stamps values onto every trace that can carry one — bars, pies,
    heatmap cells, line markers — so figures read without hovering."""
    fig.update_layout(height=height, hovermode="closest",
                      bargap=0.28, bargroupgap=0.12,
                      modebar=dict(bgcolor="rgba(0,0,0,0)", color=ON_DARK_M,
                                   activecolor=CYAN))
    fig.update_traces(marker_line_width=0, selector=dict(type="bar"))

    if labels:
        stacked = fig.layout.barmode in ("stack", "relative")
        for tr in fig.data:
            kind = tr.type
            if kind == "bar":
                vals = tr.x if getattr(tr, "orientation", None) == "h" else tr.y
                if vals is None or getattr(tr, "text", None) is not None:
                    continue
                tr.text = [compact_number(v) for v in vals]
                # stacked segments have no outside room; group/single bars do
                tr.textposition = "inside" if stacked else "outside"
                tr.textfont = dict(size=10)
                tr.cliponaxis = False
                if stacked:
                    tr.insidetextanchor = "middle"
            elif kind == "pie":
                tr.textinfo = "label+value+percent"
                tr.textfont = dict(size=10)
            elif kind == "heatmap":
                tr.texttemplate = "%{z}"
                tr.textfont = dict(size=9)
            elif kind == "scatter" and "markers" in (getattr(tr, "mode", "") or ""):
                tr.text = [compact_number(v) for v in (tr.y if tr.y is not None else [])]
                tr.mode = tr.mode if "text" in tr.mode else tr.mode + "+text"
                tr.textposition = "top center"
                tr.textfont = dict(size=9)
        if not stacked:
            # outside labels need headroom or they clip at the plot edge
            fig.update_layout(margin=dict(t=44, l=8, r=48, b=8))

    st.plotly_chart(fig, width='stretch', config=PLOTLY_CONFIG)


def section(number: str, title: str, standfirst: str = ""):
    """Numbered section rule — the report-chapter device, not a Streamlit h2."""
    st.markdown(
        f"""<div class="mck-section">
              <div class="mck-section-num">{number}</div>
              <div>
                <div class="mck-section-title">{title}</div>
                {f'<div class="mck-section-sub">{standfirst}</div>' if standfirst else ''}
              </div>
            </div>""",
        unsafe_allow_html=True,
    )


def inject_theme():
    st.markdown(f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --page: {PAGE}; --panel: {PANEL}; --card: {CARD}; --card2: #102A3D;
            --cyan: {CYAN}; --text: {ON_DARK}; --muted: {ON_DARK_M};
            --border: {LINE}; --green: {GREEN}; --amber: {AMBER}; --coral: {CORAL};
        }}
        html, body, [class*="css"] {{ font-family: {BODY_FONT}; color: {ON_DARK}; }}
        .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
        [data-testid="stMainBlockContainer"], [data-testid="stBottomBlockContainer"] {{
            background: var(--page) !important;
        }}
        header[data-testid="stHeader"] {{ background: var(--page) !important; border-bottom: 1px solid var(--border); }}
        header[data-testid="stHeader"] * {{ color: var(--muted) !important; fill: var(--muted) !important; }}
        .block-container {{ max-width: 1480px; padding: 2.2rem 2.2rem 3rem; }}
        [data-testid="stVerticalBlock"] {{ gap: .8rem; }}
        [data-testid="stHorizontalBlock"] {{ gap: 1rem; align-items: stretch; }}
        h1,h2,h3,h4 {{ font-family: {DISPLAY_FONT} !important; color: var(--text) !important; letter-spacing: -.025em; }}
        h3 {{ font-size: .92rem !important; font-weight: 700 !important; margin: .8rem 0 .2rem !important; }}
        p, label, span {{ font-family: {BODY_FONT}; }}
        .stCaption, [data-testid="stCaptionContainer"] {{ color: var(--muted) !important; font-size: .76rem !important; }}

        /* Sidebar */
        section[data-testid="stSidebar"] {{ background: var(--panel) !important; border-right: 1px solid var(--border); }}
        section[data-testid="stSidebar"] > div {{ padding: 1.25rem 1rem 1.5rem; }}
        section[data-testid="stSidebar"] h1 {{ font-size: 1.05rem !important; font-weight: 800 !important; margin-bottom: .15rem !important; }}
        section[data-testid="stSidebar"] label {{ color: #AFC2D1 !important; font-size: .73rem !important; font-weight: 600 !important; }}
        section[data-testid="stSidebar"] [data-baseweb="select"] > div,
        section[data-testid="stSidebar"] [data-baseweb="input"] > div {{
            background: #102638 !important; border: 1px solid #28465A !important; border-radius: 8px !important;
        }}
        section[data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] {{ background: rgba(24,182,242,.18) !important; border: 1px solid rgba(24,182,242,.35); border-radius: 6px !important; }}
        section[data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] * {{ color: #BDEBFA !important; }}
        section[data-testid="stSidebar"] button {{ border-radius: 8px !important; border: 1px solid #31566C !important; background: transparent !important; font-weight: 700; }}
        section[data-testid="stSidebar"] button:hover {{ background: rgba(24,182,242,.12) !important; border-color: var(--cyan) !important; }}
        .side-note {{ display:flex; gap:.45rem; align-items:flex-start; font-size:.72rem; line-height:1.5; margin:.35rem 0 .15rem; color:var(--muted); }}
        .side-note .dot {{ flex:0 0 auto; width:6px; height:6px; border-radius:50%; margin-top:.42rem; }}
        .side-note.ok .dot {{ background:var(--green); }}
        .side-note.warn .dot {{ background:var(--amber); }}
        .side-note.warn {{ color:#D8C48A; }}
        .flt-head {{ display:flex; align-items:center; justify-content:space-between; font-size:.68rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; color:#fff; padding:.75rem 0 .55rem; border-bottom:1px solid var(--border); }}
        .flt-group {{ font-size:.61rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; color:{SKY}; margin:1.05rem 0 .25rem; }}
        .flt-status {{ display:grid; grid-template-columns:1fr 1fr; gap:.55rem; margin:1rem 0 .65rem; }}
        .flt-status div {{ background:#102638; border:1px solid #28465A; border-radius:9px; padding:.6rem .65rem; }}
        .flt-status span {{ display:block; color:{ON_DARK_M}; font-size:.56rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; }}
        .flt-status b {{ display:block; color:#fff; font-size:1rem; margin-top:.15rem; }}

        /* Masthead */
        .mck-masthead {{ display:flex; justify-content:space-between; align-items:flex-end; gap:2rem; flex-wrap:wrap; padding:0 0 1.25rem; margin-bottom:1rem; border-bottom:1px solid var(--border); }}
        .mck-eyebrow {{ color:var(--cyan); font-size:.62rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; margin-bottom:.45rem; }}
        .mck-masthead h1 {{ font-size:2rem !important; font-weight:800 !important; margin:0 !important; line-height:1.08; }}
        .mck-masthead p {{ color:var(--muted) !important; font-size:.82rem; max-width:68ch; margin:.45rem 0 0; line-height:1.55; }}
        .mck-runmeta {{ display:flex; flex-wrap:wrap; gap:.45rem; }}
        .mck-runmeta div {{ min-width:88px; padding:.55rem .7rem; background:#0D2233; border:1px solid var(--border); border-radius:8px; color:var(--muted); font-size:.55rem; font-weight:700; text-transform:uppercase; letter-spacing:.09em; }}
        .mck-runmeta b {{ display:block; color:#fff; font-size:.78rem; margin-top:.15rem; letter-spacing:0; text-transform:none; }}

        /* Section headers */
        .mck-section {{ display:flex; align-items:flex-start; gap:.7rem; margin:2.25rem 0 .85rem; }}
        .mck-section-num {{ display:grid; place-items:center; min-width:28px; height:28px; border-radius:7px; background:rgba(24,182,242,.14); border:1px solid rgba(24,182,242,.3); color:var(--cyan); font-size:.62rem; font-weight:800; }}
        .mck-section-title {{ color:#fff; font-size:.92rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; line-height:1.25; }}
        .mck-section-sub {{ color:var(--muted); font-size:.73rem; margin-top:.2rem; }}

        /* KPI cards */
        div[data-testid="stMetric"] {{ background:linear-gradient(145deg,#0D2233,#0B1D2C) !important; border:1px solid var(--border) !important; border-radius:12px !important; padding:1rem 1rem .85rem !important; min-height:108px; box-shadow:0 8px 24px rgba(0,0,0,.12); }}
        div[data-testid="stMetric"]:hover {{ border-color:#31566C !important; transform:translateY(-1px); transition:.15s ease; }}
        div[data-testid="stMetricLabel"] {{ color:var(--muted) !important; font-size:.65rem !important; font-weight:700 !important; text-transform:uppercase; letter-spacing:.08em; }}
        div[data-testid="stMetricValue"] {{ color:#fff !important; font-size:1.65rem !important; font-weight:800 !important; letter-spacing:-.035em; margin-top:.25rem; }}
        div[data-testid="stMetricDelta"] {{ font-size:.68rem !important; color:var(--muted) !important; margin-top:.25rem; }}
        div[data-testid="stMetricDelta"] * {{ color:var(--muted) !important; background:transparent !important; }}
        div[data-testid="stMetricDelta"] svg {{ display:none; }}

        /* Chart and data surfaces */
        [data-testid="stPlotlyChart"] {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:.3rem .3rem .1rem; box-shadow:0 8px 24px rgba(0,0,0,.10); overflow:visible; }}
        [data-testid="stElementToolbar"] {{ z-index:5; background:#102638 !important; border:1px solid var(--border); border-radius:8px; }}
        [data-testid="stElementToolbarButton"] svg, [data-testid="stElementToolbar"] button svg {{ fill:var(--muted) !important; color:var(--muted) !important; }}
        [data-testid="stElementToolbar"] button:hover svg {{ fill:var(--cyan) !important; color:var(--cyan) !important; }}
        .modebar-container .modebar {{ background:transparent !important; }}
        .modebar-btn svg {{ opacity:.75; }}
        .modebar-btn:hover svg {{ opacity:1; }}
        div[data-testid="stDataFrame"] {{ border:1px solid var(--border); border-radius:10px; overflow:hidden; }}
        div[data-testid="stExpander"] {{ background:rgba(13,34,51,.55); border:1px solid var(--border) !important; border-radius:10px !important; }}
        div[data-testid="stExpander"] summary {{ font-weight:700; color:var(--text); }}
        .stTabs [data-baseweb="tab-list"] {{ gap:1.2rem; border-bottom:1px solid var(--border); }}
        .stTabs [data-baseweb="tab"] {{ color:var(--muted); font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.07em; padding:.45rem .05rem; }}
        .stTabs [aria-selected="true"] {{ color:#fff !important; }}
        .stTabs [data-baseweb="tab-highlight"] {{ background:var(--cyan) !important; height:2px; }}
        [data-testid="stMain"] div[data-baseweb="select"] > div {{ background:#102638 !important; border:1px solid #28465A !important; border-radius:8px !important; }}
        [data-testid="stMain"] div[data-baseweb="select"] * {{ color:var(--text) !important; }}
        .stRadio [role="radiogroup"] {{ gap:1rem; }}
        .stRadio label {{ color:var(--muted) !important; font-size:.72rem !important; font-weight:600 !important; }}
        hr {{ border-color:var(--border) !important; }}
        div[data-testid="stAlert"] {{ background:#102638 !important; border:1px solid #28465A !important; border-left:3px solid var(--cyan) !important; border-radius:9px !important; }}
        div[data-testid="stAlert"] * {{ color:var(--text) !important; }}
        .mck-footer {{ margin-top:3rem; padding-top:1rem; border-top:1px solid var(--border); display:flex; justify-content:space-between; gap:1rem; flex-wrap:wrap; color:var(--muted); font-size:.68rem; }}

        /* Responsive */
        @media (max-width: 900px) {{
            .block-container {{ padding:1.2rem .9rem 2rem; }}
            .mck-masthead h1 {{ font-size:1.55rem !important; }}
            .mck-runmeta {{ width:100%; }}
            .mck-runmeta div {{ flex:1; }}
        }}
    </style>
    """, unsafe_allow_html=True)
inject_theme()

# ---------------------------------------------------------------------------
# 0. Data load
# ---------------------------------------------------------------------------

st.sidebar.markdown('<div style="font-size:.62rem;font-weight:800;letter-spacing:.16em;color:#18B6F2;text-transform:uppercase;margin-bottom:.35rem;">SEG · M&E</div>', unsafe_allow_html=True)
st.sidebar.title("SEG Dashboard")
st.sidebar.caption("Development Alternatives")

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
    st.sidebar.markdown(
        '<div class="side-note ok"><span class="dot"></span>'
        '<span>Auto-synced from private data repo</span></div>',
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Refresh now"):
        fetch_remote_bytes.clear()
        st.rerun()
else:
    st.sidebar.markdown(
        '<div class="side-note"><span class="dot" style="background:#5CC8F2"></span>'
        '<span>No remote data source configured — using manual upload.</span></div>',
        unsafe_allow_html=True,
    )
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
    st.sidebar.markdown(
        f'<div class="side-note warn"><span class="dot"></span><span>{n_bad:,} records have an '
        f'out-of-range onboarding date and are excluded from FY / time-trend views '
        f'(but included in KPI totals).</span></div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# 1. Global filters
# ---------------------------------------------------------------------------

FILTER_KEYS = ["fk_district", "fk_block", "fk_village", "fk_agency",
               "fk_coord", "fk_phase", "fk_gender", "fk_fy", "fk_dates"]

# Values that count as a woman entrepreneur — the extract has used several
# spellings across revisions, so match on a normalised set rather than one.
FEMALE_VALUES = {"female", "f", "woman", "women", "girl"}


def is_female(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(FEMALE_VALUES)


def filter_group(label: str):
    """Small tracked rule that splits the panel into scannable groups."""
    st.sidebar.markdown(f'<div class="flt-group">{label}</div>', unsafe_allow_html=True)


def reset_filters():
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)


def multiselect_sorted(label, col, key, container=st.sidebar):
    opts = sorted(df[col].dropna().unique().tolist()) if col in df.columns else []
    return container.multiselect(label, opts, key=key)


st.sidebar.markdown('<div class="flt-head">Filters</div>', unsafe_allow_html=True)

filter_group("Geography")
f_districts = multiselect_sorted("District", "district1", "fk_district")

# cascading block -> village based on selected districts
_block_pool = df[df["district1"].isin(f_districts)] if f_districts else df
f_blocks = st.sidebar.multiselect(
    "Block", sorted(_block_pool["block"].dropna().unique().tolist()), key="fk_block"
)

_village_pool = _block_pool[_block_pool["block"].isin(f_blocks)] if f_blocks else _block_pool
f_villages = st.sidebar.multiselect(
    "Village", sorted(_village_pool["village"].dropna().unique().tolist()), key="fk_village"
)

filter_group("Delivery")
f_agencies = multiselect_sorted("Agency", "agency", "fk_agency")
f_coordinators = multiselect_sorted("Field Coordinator", "name_of_field_coordinator", "fk_coord")
f_phases = multiselect_sorted("Phase", "phase", "fk_phase")

filter_group("Profile")
f_genders = multiselect_sorted("Gender", "gender", "fk_gender")

filter_group("Period")
fy_opts = sorted([x for x in df["financial_year"].dropna().unique()])
f_fys = st.sidebar.multiselect("Financial Year", fy_opts, key="fk_fy")

min_d, max_d = df[DATE_COL].min(), df[DATE_COL].max()
f_date_range = st.sidebar.date_input(
    "Onboarding date range", value=(), min_value=min_d, max_value=max_d, key="fk_dates"
)
f_date_range = f_date_range if isinstance(f_date_range, tuple) and len(f_date_range) == 2 else None

fdf = apply_filters(
    df,
    districts=f_districts, blocks=f_blocks, villages=f_villages,
    agencies=f_agencies, coordinators=f_coordinators,
    financial_years=f_fys, phases=f_phases, date_range=f_date_range,
)

# Gender isn't a parameter of apply_filters, so it is applied on the result.
if f_genders and "gender" in fdf.columns:
    fdf = fdf[fdf["gender"].isin(f_genders)]

_n_active = sum(bool(x) for x in [f_districts, f_blocks, f_villages, f_agencies,
                                  f_coordinators, f_phases, f_genders, f_fys, f_date_range])
st.sidebar.markdown(
    f'''<div class="flt-status">
         <div><span>Active filters</span><b>{_n_active}</b></div>
         <div><span>Matching records</span><b>{len(fdf):,}</b></div>
       </div>''',
    unsafe_allow_html=True,
)
st.sidebar.button("Clear all filters", on_click=reset_filters, width='stretch')


_generated_at = pd.Timestamp.now().strftime("%d %b %Y, %H:%M")
_active_filters = sum(bool(x) for x in [f_districts, f_blocks, f_villages, f_agencies,
                                        f_coordinators, f_phases, f_genders, f_fys, f_date_range])
_coverage = f"{len(fdf) / max(len(df), 1) * 100:.0f}%"

st.markdown(f"""
<div class="mck-masthead">
    <div>
        <div class="mck-eyebrow">Monitoring &amp; Evaluation &middot; Sustainable Entrepreneurship Group</div>
        <h1>SEG Data Dashboard</h1>
        <p>Executive progress, financial, sector, geographic, temporal and sustainability views.
        Every figure responds to the filters set in the left panel.</p>
    </div>
    <div class="mck-runmeta">
        <div>Records<b>{len(fdf):,}</b></div>
        <div>Of extract<b>{_coverage}</b></div>
        <div>Filters<b>{_active_filters}</b></div>
        <div>Source<b>{"Repo sync" if using_remote else "Manual"}</b></div>
        <div>Generated<b>{_generated_at}</b></div>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 2. Section 1 — Executive KPI cards
# ---------------------------------------------------------------------------

section("01", "Executive Summary(2017 to 19th August 2026)", " Programme totals for the current filter selection.")
k = compute_kpis(fdf)

# The extract has been through several column-naming revisions, so resolve
# the women-employee column by pattern rather than hard-coding one name.
WOMEN_EMP_CANDIDATES = ["total_female_employees", "female_employees", "women_employees",
                        "total_women_employees", "no_of_female_employees",
                        "number_of_female_employees"]
women_emp_col = next((c for c in WOMEN_EMP_CANDIDATES if c in fdf.columns), None)
if women_emp_col is None:
    women_emp_col = next(
        (c for c in fdf.columns
         if ("female" in c.lower() or "women" in c.lower())
         and any(w in c.lower() for w in ("employee", "worker", "staff"))
         and pd.api.types.is_numeric_dtype(fdf[c])),
        None,
    )
total_women_employees = float(fdf[women_emp_col].sum()) if women_emp_col else None

# Women entrepreneurs: the enterprise owners themselves, distinct from the
# women they employ above.
women_entrepreneurs = int(is_female(fdf["gender"]).sum()) if "gender" in fdf.columns else None

# Finance unlocked = credit mobilised from all sources + the entrepreneur's
# own money put into the enterprise.
total_finance_unlocked = float(k["total_loan_mobilized"]) + float(k["total_savings_invested"])

r1 = st.columns(4)
r1[0].metric("Total Entrepreneurs", f"{k['total_entrepreneurs']:,}")
r1[1].metric("Youth (≤29)", f"{k['youth_entrepreneurs']:,}",
             f"{k['youth_entrepreneurs'] / max(k['total_entrepreneurs'],1) * 100:.1f}%")
r1[2].metric("Jobs Created", f"{k['total_jobs_created']:,.0f}")
r1[3].metric(
    "Total Women Employees",
    f"{total_women_employees:,.0f}" if total_women_employees is not None else "—",
    None if women_emp_col else "no matching column in extract",
    delta_color="off",
)

r2 = st.columns(4)
r2[0].metric(
    "Women Entrepreneurs",
    f"{women_entrepreneurs:,}" if women_entrepreneurs is not None else "—",
    (f"{women_entrepreneurs / max(k['total_entrepreneurs'], 1) * 100:.1f}% of entrepreneurs"
     if women_entrepreneurs is not None else "no gender column in extract"),
    delta_color="off",
)
r2[1].metric("High-Growth Enterprises", f"{k['high_growth_enterprises']:,}", "> 2 employees",
              delta_color="off")
r2[2].metric("Green Enterprises", f"{k['green_enterprises']:,}", f"{k['green_pct']:.1f}% of total")
r2[3].metric("CO₂ Mitigated Till Date", f"{k['co2_mitigated_till_date']:,.1f} t")

r3 = st.columns(4)
r3[0].metric("Total Savings Invested", format_indian_number(k['total_savings_invested']))
r3[1].metric("Total Loan Mobilized", format_indian_number(k['total_loan_mobilized']))
r3[2].metric("Total Finance Unlocked", format_indian_number(total_finance_unlocked),
             "loan + own savings", delta_color="off")
r3[3].metric("Verification Rate", f"{k['verification_rate_pct']:.1f}%",
             f"{k['data_correct_rate_pct']:.1f}% flagged correct")


# ---------------------------------------------------------------------------
# 3. Section 2 — Financial & Loan Breakdown
# ---------------------------------------------------------------------------

section("02", "Financial &amp; Loan Breakdown", "Where capital comes from, how much of it, and how it is distributed across sectors and sources.")
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
            color_continuous_scale=[[0, "#14304A"], [1, GREEN]],
            template=PLOTLY_TEMPLATE,
        )
        fig.update_traces(texttemplate="%{label}<br>₹%{value:,.0f}")
        show(fig)
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
        show(fig)

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
                     color_discrete_sequence=[CYAN])
        fig.update_layout(yaxis_title="", xaxis_title="Entrepreneurs")
        show(fig)
    with c2:
        st.caption("Average loan size by source (among that source's borrowers)")
        ad = borrower_df.sort_values("avg_loan_size", ascending=True)
        fig = px.bar(ad, x="avg_loan_size", y="source", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[AMBER])
        fig.update_layout(yaxis_title="", xaxis_title="Average Loan Amount (₹)")
        show(fig)

    st.caption("Borrower counts and per-source averages count each entrepreneur under every source they "
               "used (someone can borrow from both CLF and a bank) — they won't sum to the total above. "
               "'Average loan size' is computed only among entrepreneurs who actually borrowed from that "
               "source, not averaged over everyone.")

st.subheader("Investment vs. Loan Amount by Sector")
if {"total_investment", "total_loan_amount", "sector1"}.issubset(fdf.columns):
    plot_df = fdf.dropna(subset=["total_investment", "total_loan_amount", "sector1"])
    melt = plot_df.melt(id_vars="sector1", value_vars=["total_investment", "total_loan_amount"],
                         var_name="metric", value_name="amount")
    fig = px.box(
        melt, x="sector1", y="amount", color="metric", template=PLOTLY_TEMPLATE,
        color_discrete_sequence=COLOR_SEQ, points=False,
        labels={"amount": "Amount (₹)", "sector1": "Sector"},
    )
    show(fig, height=380)

st.subheader("Loan-to-Savings Ratio")
if {"total_loan_amount", "individual_saving_invested"}.issubset(fdf.columns):
    ratio_dim = "sector1"
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
            color_discrete_sequence=[CORAL],
            labels={"ratio": "Loan ÷ Savings", ratio_dim: "Sector"},
        )
        fig.add_vline(x=1, line_dash="dash", line_color="gray",
                       annotation_text="1:1 (loan = savings)", annotation_position="top")
        show(fig)
        st.caption("A ratio above 1 means loan mobilization outpaces personal savings invested in that "
                   "group — useful as a rough proxy for reliance on external credit vs. self-funding. "
                   "Groups with zero recorded savings are excluded to avoid a divide-by-zero distortion.")


# ---------------------------------------------------------------------------
# 4. Section 3 — Sector & Enterprise Deep-Dive
# ---------------------------------------------------------------------------

section("03", "Sector &amp; Enterprise Deep-Dive", "Composition of the portfolio by sector, enterprise type and entrepreneur profile.")

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
            show(fig)
        with tab_pie:
            fig = px.pie(counts, names="sector1", values="count", template=PLOTLY_TEMPLATE,
                         color_discrete_sequence=COLOR_SEQ, hole=0.35)
            fig.update_traces(textinfo="percent+label")
            show(fig)

with c2:
    st.subheader("Top 10 enterprise types (overall)")
    if "enterprise_type" in fdf.columns:
        top10 = fdf["enterprise_type"].value_counts().head(10).reset_index()
        top10.columns = ["enterprise_type", "count"]
        fig = px.bar(top10, x="count", y="enterprise_type", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[GREEN])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Entrepreneurs")
        show(fig)

if {"sector1", "enterprise_type"}.issubset(fdf.columns):
    st.subheader("Top 10 enterprise types — by selected sector")
    sector_pick = st.selectbox("Sector", sorted(fdf["sector1"].dropna().unique()))
    sub = fdf[fdf["sector1"] == sector_pick]
    top10_sector = sub["enterprise_type"].value_counts().head(10).reset_index()
    top10_sector.columns = ["enterprise_type", "count"]
    fig = px.bar(top10_sector, x="count", y="enterprise_type", orientation="h", template=PLOTLY_TEMPLATE,
                 color_discrete_sequence=[LEAD])
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Entrepreneurs")
    show(fig)

c3, c4 = st.columns(2)
with c3:
    st.subheader("Gender distribution by sector")
    if {"sector1", "gender"}.issubset(fdf.columns):
        ct = fdf.groupby(["sector1", "gender"]).size().reset_index(name="count")
        fig = px.bar(ct, x="sector1", y="count", color="gender", barmode="stack",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ)
        fig.update_layout(xaxis_title="", yaxis_title="Entrepreneurs")
        show(fig)

with c4:
    st.subheader("Social category distribution by sector")
    if {"sector1", "social_category"}.issubset(fdf.columns):
        ct = fdf.groupby(["sector1", "social_category"]).size().reset_index(name="count")
        fig = px.bar(ct, x="sector1", y="count", color="social_category", barmode="stack",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=COLOR_SEQ)
        fig.update_layout(xaxis_title="", yaxis_title="Entrepreneurs")
        show(fig)

st.subheader("District × Sector heatmap")
if {"district1", "sector1"}.issubset(fdf.columns):
    pivot = pd.crosstab(fdf["district1"], fdf["sector1"])
    if pivot.size:
        fig = px.imshow(
            pivot, template=PLOTLY_TEMPLATE, color_continuous_scale=[[0, "#14304A"], [1, CYAN]], aspect="auto",
            labels=dict(x="Sector", y="District", color="Entrepreneurs"),
        )
        fig.update_layout(xaxis_tickangle=-35)
        show(fig)
        st.caption("Darker cells = more entrepreneurs in that district-sector combination. "
                   "Useful for spotting which districts are concentrated in a narrow set of sectors "
                   "vs. diversified.")


# ---------------------------------------------------------------------------
# 5. Section 4 — Geographic & Agency Performance
# ---------------------------------------------------------------------------

section("04", "Geographic &amp; Agency Performance", "Delivery performance by district and implementing agency, measured against official targets.")

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
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[LEAD])
        fig.update_layout(yaxis_title="", xaxis_title="")
        show(fig)
    with a2:
        st.caption("Jobs Created")
        fig = px.bar(agency_agg.sort_values("jobs_created"), x="jobs_created", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[CYAN])
        fig.update_layout(yaxis_title="", xaxis_title="")
        show(fig)
    with a3:
        st.caption("Green %")
        fig = px.bar(agency_agg.sort_values("green_pct"), x="green_pct", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[GREEN])
        fig.update_layout(yaxis_title="", xaxis_title="%")
        show(fig)
    with a4:
        st.caption("Loan Mobilized (₹)")
        fig = px.bar(agency_agg.sort_values("loan_mobilized"), x="loan_mobilized", y="agency", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[AMBER])
        fig.update_layout(yaxis_title="", xaxis_title="")
        show(fig)
else:
    st.caption("No agency field available for the current filter selection.")

st.divider()
st.subheader("District Comparison")

geo_dim = "district1"

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
                     color_discrete_sequence=[CYAN])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="")
        show(fig)
    with c2:
        st.caption("Green %")
        fig = px.bar(agg.sort_values("green_pct"), x="green_pct", y=geo_dim, orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[GREEN])
        fig.update_layout(yaxis_title="", xaxis_title="%")
        show(fig)
    with c3:
        st.caption("Loan Mobilized (₹)")
        fig = px.bar(agg.sort_values("loan_mobilized"), x="loan_mobilized", y=geo_dim, orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[CORAL])
        fig.update_layout(yaxis_title="", xaxis_title="")
        show(fig)

st.subheader("Data quality status by district")
if "verification_status" in fdf.columns:
    qc = fdf.groupby([geo_dim, "verification_status"]).size().reset_index(name="count")
    fig = px.bar(qc, x=geo_dim, y="count", color="verification_status", barmode="stack",
                 template=PLOTLY_TEMPLATE,
                 color_discrete_map={
                     "pending": AMBER, "verified - correct": GREEN,
                     "verified - issue flagged": CORAL,
                 })
    fig.update_layout(xaxis_title="", yaxis_title="Records")
    show(fig)

st.subheader("Target vs. Achieved — live count against official district targets")
targets_merged, untracked_districts = target_vs_achieved(fdf)
if len(targets_merged):
    metric_pick = st.selectbox(
        "Metric", list(METRIC_LABELS.values()),
        index=list(METRIC_LABELS.values()).index("Total Enterprises"),
    )
    metric_key = [k for k, v in METRIC_LABELS.items() if v == metric_pick][0]
    tv = targets_merged[targets_merged["metric"] == metric_key].sort_values("target", ascending=False)

    # Achievement is read live from the current extract; the programme's own
    # reported figure is no longer plotted or used in any percentage here.
    tv = tv.copy()
    tv["pct_live"] = tv["live_extract_count"] / tv["target"].replace(0, np.nan) * 100

    fig = go.Figure()
    fig.add_bar(x=tv["district1"], y=tv["target"], name="Target", marker_color="#5C7386")
    fig.add_bar(x=tv["district1"], y=tv["live_extract_count"], name="Achieved (live count)",
                marker_color=CYAN)
    fig.update_layout(barmode="group", template=PLOTLY_TEMPLATE, yaxis_title=metric_pick,
                       xaxis_title="", legend=dict(orientation="h", y=1.1))
    show(fig, height=400)

    _tot_target = float(tv["target"].sum())
    _tot_live = float(tv["live_extract_count"].sum())
    t1, t2, t3 = st.columns(3)
    t1.metric("Total Target", f"{_tot_target:,.0f}")
    t2.metric("Achieved (live count)", f"{_tot_live:,.0f}")
    t3.metric("Achievement", f"{_tot_live / _tot_target * 100:,.1f}%" if _tot_target else "—",
              f"gap {(_tot_target - _tot_live):,.0f} to target" if _tot_target else None,
              delta_color="off")

    with st.expander("District-wise target vs. live achievement"):
        st.caption(
            "Achievement is counted fresh from the data extract currently loaded and "
            "filtered, so it moves with the sidebar filters and includes unverified / "
            "in-progress records. The programme's own reported figure is not used here."
        )
        _tbl = tv[["district1", "target", "live_extract_count", "pct_live"]].copy()
        _tbl["gap"] = _tbl["target"] - _tbl["live_extract_count"]
        _tbl["pct_live"] = _tbl["pct_live"].round(1)
        st.dataframe(
            _tbl.rename(columns={
                "district1": "District", "target": "Target",
                "live_extract_count": "Achieved (live count)",
                "pct_live": "% achieved", "gap": "Gap to target",
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


# ---------------------------------------------------------------------------
# 6. Section 5 — Temporal Trends
# ---------------------------------------------------------------------------

section("05", "Temporal Trends", "Onboarding, jobs and capital mobilisation over time. Records with invalid dates are excluded here.")

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
                marker_color=CYAN)
    fig.add_trace(go.Scatter(x=fy_agg["financial_year"], y=fy_agg["jobs"], name="Jobs Created",
                              yaxis="y2", mode="lines+markers", line=dict(color=CORAL)))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        yaxis=dict(title="Entrepreneurs"),
        yaxis2=dict(title="Jobs Created", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.1),
    )
    show(fig)

st.subheader("Enterprise growth — new vs. existing, over time")
if {"onboard_month", "new_or_existing"}.issubset(tdf.columns):
    growth = tdf.groupby(["onboard_month", "new_or_existing"]).size().reset_index(name="count")
    fig = px.area(
        growth, x="onboard_month", y="count", color="new_or_existing", template=PLOTLY_TEMPLATE,
        color_discrete_sequence=[CYAN, GREEN],
        labels={"onboard_month": "Month", "count": "Entrepreneurs", "new_or_existing": "Enterprise Status"},
    )
    show(fig)

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
        color_discrete_sequence=[CYAN],
        labels={period_col: granularity, "entrepreneurs": "New Onboarding"},
    )
    show(fig)

    c1, c2 = st.columns(2)
    with c1:
        fig = px.line(period_agg, x=period_col, y="jobs", markers=True, template=PLOTLY_TEMPLATE,
                       labels={period_col: granularity, "jobs": "Jobs Created"},
                       color_discrete_sequence=[GREEN])
        show(fig)
    with c2:
        fig = px.line(period_agg, x=period_col, y="loan_mobilized", markers=True, template=PLOTLY_TEMPLATE,
                       labels={period_col: granularity, "loan_mobilized": "Loan Mobilized (₹)"},
                       color_discrete_sequence=[CORAL])
        show(fig)

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
    show(fig)
    st.caption("Series are indexed to 100 at the first month in range so indicators with very "
               "different scales (e.g. CO₂ tonnes vs. entrepreneur counts) can be compared on one chart. "
               "Toggle raw values below.")
    with st.expander("Show raw monthly values"):
        st.dataframe(monthly, width='stretch')


# ---------------------------------------------------------------------------
# 7. Section 6 — Sustainability & Support
# ---------------------------------------------------------------------------

section("06", "Sustainability &amp; Support", "Green-energy adoption, resource practices, and the gap between support needed and support delivered.")

# --- Green energy adoption --------------------------------------------------
st.subheader("Green Energy Adoption")
c1, c2, c3 = st.columns(3)

with c1:
    st.caption("Solar adoption")
    if "are_you_using_solar_electricity" in fdf.columns:
        solar_counts = fdf["are_you_using_solar_electricity"].value_counts().reset_index()
        solar_counts.columns = ["status", "count"]
        fig = px.pie(solar_counts, names="status", values="count", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[GREEN, "#5C7386"], hole=0.4)
        fig.update_traces(textinfo="percent+label")
        show(fig)

with c2:
    st.caption("Solar adoption by district")
    if {"are_you_using_solar_electricity", "district1"}.issubset(fdf.columns):
        solar_by_d = fdf.groupby("district1")["are_you_using_solar_electricity"].apply(
            lambda s: (s == "yes").mean() * 100
        ).reset_index(name="solar_pct").sort_values("solar_pct")
        fig = px.bar(solar_by_d, x="solar_pct", y="district1", orientation="h",
                     template=PLOTLY_TEMPLATE, color_discrete_sequence=[AMBER])
        fig.update_layout(yaxis_title="", xaxis_title="% using solar")
        show(fig)

with c3:
    st.caption("Solar panel capacity (kW) — among adopters")
    if "solar_panel_capacity" in fdf.columns:
        cap = fdf.loc[fdf["solar_panel_capacity"] > 0, "solar_panel_capacity"].dropna()
        if len(cap):
            fig = px.histogram(cap, template=PLOTLY_TEMPLATE, color_discrete_sequence=[CYAN],
                               labels={"value": "Capacity (kW)"})
            fig.update_layout(showlegend=False, yaxis_title="Enterprises")
            show(fig)
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
                     color_discrete_sequence=[GREEN, CORAL], hole=0.4)
        fig.update_traces(textinfo="percent+label")
        show(fig)

with c2:
    st.caption("Water reuse")
    if "do_you_reuse_your_water" in fdf.columns:
        reuse_counts = fdf["do_you_reuse_your_water"].value_counts().reset_index()
        reuse_counts.columns = ["status", "count"]
        fig = px.pie(reuse_counts, names="status", values="count", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[CYAN, "#5C7386"], hole=0.4)
        fig.update_traces(textinfo="percent+label")
        show(fig)

with c3:
    st.caption("Water sources used")
    if "water_sources" in fdf.columns:
        src_counts = split_multiselect_counts(fdf["water_sources"]).head(8).reset_index()
        src_counts.columns = ["source", "count"]
        fig = px.bar(src_counts, x="count", y="source", orientation="h", template=PLOTLY_TEMPLATE,
                     color_discrete_sequence=[CYAN])
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title="", xaxis_title="Enterprises")
        show(fig)

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
                orientation="h", marker_color=CORAL)
    fig.add_bar(y=gap["category"], x=gap["Support already provided"], name="Support already provided",
                orientation="h", marker_color=GREEN)
    fig.update_layout(barmode="group", template=PLOTLY_TEMPLATE, xaxis_title="Mentions",
                       yaxis_title="", legend=dict(orientation="h", y=1.1))
    show(fig)
    st.caption("Categories are split out of multi-select responses (e.g. 'financial,marketing' counts "
               "toward both Financial and Marketing) — bars are mention counts, not unique entrepreneurs, "
               "so they won't sum to the total record count.")

    with st.expander("Most common specific support requests (free text)"):
        if "detail_of_support_required" in fdf.columns:
            detail = fdf["detail_of_support_required"].dropna().value_counts().head(15).reset_index()
            detail.columns = ["Request", "Mentions"]
            st.dataframe(detail, width='stretch', hide_index=True)

st.markdown(f"""
<div class="mck-footer">
    <span>Development Alternatives &middot; Sustainable Entrepreneurship Group</span>
    <span>Generated {_generated_at} &middot; {len(fdf):,} of {len(df):,} records shown</span>
</div>
""", unsafe_allow_html=True)
