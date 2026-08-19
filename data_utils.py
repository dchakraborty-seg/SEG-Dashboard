"""
data_utils.py
-------------
Data loading, cleaning, and feature-derivation layer for the WEE / M&E dashboard.
Kept separate from app.py so the cleaning logic can be unit-tested or reused
(e.g. in the WEE analytics script / master enterprise dashboard exports)
independently of Streamlit.

Design notes based on a real extract of this dataset (85k+ rows, 84 cols):
- Text columns (district1, agency, sector1, phase, ...) have inconsistent casing,
  stray whitespace, and mixed numbering schemes (e.g. "phase v" vs "phase 3").
  -> normalize_text() + PHASE_MAP handle this.
- date_of_onboarding_to_the_program contains clear data-entry errors
  (dates in 1986, dates in 2027+). -> sanity-bounded before FY derivation.
- verified_by_mis_team is 0/1 with heavy nulls -> treated as "not yet verified".
- is_green is a 'green' / 'not green' string, not a boolean.
"""

from __future__ import annotations
import io
import os
import re
import numpy as np
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Config: columns & constants
# ---------------------------------------------------------------------------

DATE_COL = "date_of_onboarding_to_the_program"

# Roman / shorthand phase labels all collapse to a single canonical scheme.
PHASE_MAP = {
    "phase i": "Phase 1", "phase 1": "Phase 1",
    "phase ii": "Phase 2", "phase 2": "Phase 2",
    "phase iii": "Phase 3", "phase 3": "Phase 3",
    "phase iv": "Phase 4", "phase 4": "Phase 4",
    "phase v": "Phase 5", "phase 5": "Phase 5",
    "phase vi": "Phase 6", "phase 6": "Phase 6",
}

LOAN_SOURCE_COLS = [
    "from_clf", "from_bank", "from_mfi", "from_family_friends",
    "from_rang_de", "from_nbfc", "from_government_scheme",
]

NUMERIC_COLS = [
    "age", "total_employees", "number_of_male_employees", "number_of_female_employees",
    "total_loan_amount", "total_investment", "individual_saving_invested",
    "co2_mitigated_till_date", "co2_mitigated_tonnes_per_month",
    "annual_individual_income_before_intervention", "annual_family_income_before_intervention",
]

TEXT_CATEGORICAL_COLS = [
    "district1", "block", "village", "agency", "name_of_field_coordinator",
    "sector1", "enterprise_type", "gender", "social_category", "phase",
    "new_or_existing", "is_green", "verification_outcome", "checked_by_mis_team",
    "traditional_non_traditional", "have_you_taken_any_kind_of_loan",
]

# Plausible bounds for onboarding dates — anything outside this is a data-entry error.
MIN_VALID_DATE = pd.Timestamp("2018-01-01")
MAX_VALID_DATE = pd.Timestamp.today() + pd.Timedelta(days=30)


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def normalize_text(series: pd.Series) -> pd.Series:
    """Lowercase, strip, collapse internal whitespace. Keeps NaN as NaN."""
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", " ", regex=True)
    )


def derive_financial_year(dt: pd.Series) -> pd.Series:
    """
    India FY runs Apr 1 - Mar 31.
    Returns strings like 'FY 2024-25'. NaT / out-of-bounds dates -> pd.NA.
    """
    valid = dt.where((dt >= MIN_VALID_DATE) & (dt <= MAX_VALID_DATE))
    fy_start_year = np.where(valid.dt.month >= 4, valid.dt.year, valid.dt.year - 1)
    fy_start_year = pd.Series(fy_start_year, index=dt.index)
    label = fy_start_year.astype("Int64").astype("string") + "-" + \
        (fy_start_year.astype("Int64") + 1).astype("string").str.slice(-2)
    label = "FY " + label
    return label.where(valid.notna(), other=pd.NA)


def derive_quarter(dt: pd.Series) -> pd.Series:
    """Fiscal quarter label, Q1 = Apr-Jun ... Q4 = Jan-Mar."""
    valid = dt.where((dt >= MIN_VALID_DATE) & (dt <= MAX_VALID_DATE))
    month = valid.dt.month
    q = ((month - 4) % 12) // 3 + 1
    return ("Q" + q.astype("Int64").astype("string")).where(valid.notna(), other=pd.NA)


def _sniff_and_read(path_or_buffer) -> pd.DataFrame:
    """
    Reads .xlsx or .parquet transparently, from either a path or an
    in-memory buffer (file uploader, BytesIO from a remote fetch, etc).
    Parquet is strongly preferred for anything beyond a few MB — see
    README.md for why (GitHub file-size limits + faster loads).
    """
    if isinstance(path_or_buffer, (str, os.PathLike)):
        path = str(path_or_buffer)
        if path.lower().endswith(".parquet"):
            return pd.read_parquet(path)
        return pd.read_excel(path)

    # file-like object: sniff the magic bytes rather than trust a filename
    data = path_or_buffer.read()
    path_or_buffer.seek(0)
    if data[:4] == b"PAR1":
        return pd.read_parquet(io.BytesIO(data))
    return pd.read_excel(io.BytesIO(data))


@st.cache_data(show_spinner="Loading and cleaning data…")
def load_data(path_or_buffer) -> pd.DataFrame:
    df = _sniff_and_read(path_or_buffer)
    df = clean_dataframe(df)
    return df


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # --- text normalization -------------------------------------------------
    for col in TEXT_CATEGORICAL_COLS:
        if col in df.columns:
            df[col] = normalize_text(df[col])

    if "phase" in df.columns:
        df["phase"] = df["phase"].map(PHASE_MAP).fillna(df["phase"])

    if "is_green" in df.columns:
        df["is_green_flag"] = df["is_green"].eq("green")

    if "verified_by_mis_team" in df.columns:
        df["verified_flag"] = df["verified_by_mis_team"].fillna(0).astype(int).eq(1)

    if "verification_outcome" in df.columns:
        cond_pending = (df["verified_by_mis_team"].fillna(0).eq(0) & df["verification_outcome"].isna())
        cond_correct = df["verification_outcome"].eq("correct")
        df["verification_status"] = np.select(
            [cond_pending.fillna(False).to_numpy(dtype=bool),
             cond_correct.fillna(False).to_numpy(dtype=bool)],
            ["pending", "verified - correct"],
            default="verified - issue flagged",
        )

    # --- numeric coercion -----------------------------------------------------
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in LOAN_SOURCE_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # --- date / FY derivation --------------------------------------------------
    if DATE_COL in df.columns:
        df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce")
        df["date_valid"] = df[DATE_COL].between(MIN_VALID_DATE, MAX_VALID_DATE)
        df["financial_year"] = derive_financial_year(df[DATE_COL])
        df["fy_quarter"] = derive_quarter(df[DATE_COL])
        df["onboard_month"] = df[DATE_COL].dt.to_period("M").astype("string")
        df["onboard_week"] = df[DATE_COL].dt.to_period("W").astype("string")

    # --- derived business fields -----------------------------------------------
    if "total_employees" in df.columns:
        df["is_high_growth"] = df["total_employees"] > 2
    if "age" in df.columns:
        df["is_youth"] = df["age"] <= 29

    return df


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def apply_filters(
    df: pd.DataFrame,
    districts: list[str] | None = None,
    blocks: list[str] | None = None,
    villages: list[str] | None = None,
    agencies: list[str] | None = None,
    coordinators: list[str] | None = None,
    financial_years: list[str] | None = None,
    phases: list[str] | None = None,
    date_range: tuple | None = None,
) -> pd.DataFrame:
    out = df
    if districts:
        out = out[out["district1"].isin(districts)]
    if blocks:
        out = out[out["block"].isin(blocks)]
    if villages:
        out = out[out["village"].isin(villages)]
    if agencies:
        out = out[out["agency"].isin(agencies)]
    if coordinators:
        out = out[out["name_of_field_coordinator"].isin(coordinators)]
    if financial_years:
        out = out[out["financial_year"].isin(financial_years)]
    if phases:
        out = out[out["phase"].isin(phases)]
    if date_range and len(date_range) == 2 and all(date_range):
        start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
        out = out[out[DATE_COL].between(start, end)]
    return out
