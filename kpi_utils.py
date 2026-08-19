"""
kpi_utils.py
------------
Pure functions that compute the Section 1 executive KPIs from a (filtered)
dataframe. Kept separate from app.py so the numbers can be reused in
scheduled reports / the WEE analytics script without touching Streamlit.
"""

from __future__ import annotations
import pandas as pd


def compute_kpis(df: pd.DataFrame) -> dict:
    total = len(df)
    verified = int(df["verified_flag"].sum()) if "verified_flag" in df else 0
    correct = int(df["verification_status"].eq("verified - correct").sum()) \
        if "verification_status" in df else 0

    return {
        "total_entrepreneurs": df["ID"].nunique() if "ID" in df else total,
        "youth_entrepreneurs": int(df.get("is_youth", pd.Series(dtype=bool)).sum()),
        "total_jobs_created": float(df.get("total_employees", pd.Series(dtype=float)).sum()),
        "high_growth_enterprises": int(df.get("is_high_growth", pd.Series(dtype=bool)).sum()),
        "green_enterprises": int(df.get("is_green_flag", pd.Series(dtype=bool)).sum()),
        "green_pct": (df.get("is_green_flag", pd.Series(dtype=bool)).mean() * 100) if total else 0,
        "co2_mitigated_till_date": float(df.get("co2_mitigated_till_date", pd.Series(dtype=float)).sum()),
        "total_savings_invested": float(df.get("individual_saving_invested", pd.Series(dtype=float)).sum()),
        "total_loan_mobilized": float(df.get("total_loan_amount", pd.Series(dtype=float)).sum()),
        "verification_rate_pct": (verified / total * 100) if total else 0,
        "data_correct_rate_pct": (correct / total * 100) if total else 0,
        "total_records": total,
    }


def format_indian_number(n: float) -> str:
    """Format large numbers in Indian units (Lakh/Crore) for finance KPIs."""
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e7:
        return f"{sign}₹{n / 1e7:,.2f} Cr"
    if n >= 1e5:
        return f"{sign}₹{n / 1e5:,.2f} L"
    if n >= 1e3:
        return f"{sign}₹{n / 1e3:,.1f} K"
    return f"{sign}₹{n:,.0f}"
