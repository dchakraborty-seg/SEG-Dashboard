"""
targets_utils.py
-----------------
District-level programme targets AND officially reported achieved figures
(from the targets sheet supplied by the user), plus a live count of the same
metrics from whatever data extract is currently loaded/filtered. Both are
surfaced separately in Section 4 — see the docstring on target_vs_achieved()
for why they aren't reconciled into one number.

Target-sheet district names are normalized to the same lowercase/stripped
keys used in data_utils.normalize_text() so they can be joined onto
`district1`. "Sant Ravidas Nagar" is the official name for the district the
data extract records as "Bhadohi" -> mapped explicitly below.
"""

from __future__ import annotations
import pandas as pd

# ---------------------------------------------------------------------------
# Targets, as supplied. One row per district, one column pair per metric.
# Values are the "Target" column from the source sheet.
# ---------------------------------------------------------------------------

_RAW_TARGETS = {
    # district_name_in_sheet: {metric: (target, achieved)}  -- both from the official sheet
    "jhansi":              dict(total=(6546,2467), women_led=(5000,2352), male_led=(1546,115), non_traditional=(2618,250), e_rickshaw=(60,13),  green=(1964,107), high_growth=(3273,102), youth=(2618,367), jobs=(13091,2467)),
    "mirzapur":            dict(total=(8000,3774), women_led=(8000,3707), male_led=(0,67),      non_traditional=(3200,304), e_rickshaw=(120,54), green=(2400,217), high_growth=(4000,290), youth=(3200,196), jobs=(16000,3774)),
    "lucknow":             dict(total=(6795,21),   women_led=(6000,21),   male_led=(795,0),      non_traditional=(2718,3),   e_rickshaw=(60,0),   green=(2038,0),   high_growth=(3397,3),   youth=(2718,1),   jobs=(13590,21)),
    "varanasi":            dict(total=(6104,1291), women_led=(5000,1266), male_led=(1104,25),   non_traditional=(2442,96),  e_rickshaw=(170,26), green=(1831,81),  high_growth=(3052,91),  youth=(2442,130), jobs=(12208,1291)),
    "gorakhpur":           dict(total=(3000,395),  women_led=(3000,370),  male_led=(0,25),       non_traditional=(1200,49),  e_rickshaw=(100,7),  green=(900,39),   high_growth=(1500,27),  youth=(1200,28),  jobs=(6000,395)),
    "prayagraj":           dict(total=(7104,1802), women_led=(6000,1767), male_led=(1104,35),   non_traditional=(2842,225), e_rickshaw=(130,2),  green=(2131,73),  high_growth=(3552,103), youth=(2842,126), jobs=(14208,1802)),
    "kaushambi":           dict(total=(3000,2406), women_led=(3000,2395), male_led=(0,11),      non_traditional=(1200,185), e_rickshaw=(170,12), green=(900,274),  high_growth=(1500,320), youth=(1200,292), jobs=(6000,2406)),
    "sonbhadra":           dict(total=(2500,679),  women_led=(2500,677),  male_led=(0,2),        non_traditional=(1000,39),  e_rickshaw=(20,5),   green=(750,3),    high_growth=(1250,63),  youth=(1000,77),  jobs=(5000,697)),
    "sant ravidas nagar":  dict(total=(6766,2446), women_led=(5000,2347), male_led=(1766,99),   non_traditional=(2707,267), e_rickshaw=(20,2),   green=(2030,125), high_growth=(3383,173), youth=(2707,166), jobs=(13533,2446)),
    "sitapur":             dict(total=(2500,14),   women_led=(2500,14),   male_led=(0,0),        non_traditional=(1000,7),   e_rickshaw=(50,2),   green=(750,1),    high_growth=(1250,2),   youth=(1000,5),   jobs=(5000,14)),
    "lakhimpur kheri":     dict(total=(3000,62),   women_led=(3000,61),   male_led=(0,1),        non_traditional=(1200,55),  e_rickshaw=(50,1),   green=(900,28),   high_growth=(1500,6),   youth=(1200,31),  jobs=(6000,62)),
    "deoria":              dict(total=(3000,43),   women_led=(3000,43),   male_led=(0,0),        non_traditional=(1200,13),  e_rickshaw=(50,0),   green=(900,0),    high_growth=(1500,2),   youth=(1200,4),   jobs=(6000,43)),
    "nalanda":             dict(total=(1000,682),  women_led=(1000,682),  male_led=(0,0),        non_traditional=(400,39),   e_rickshaw=(0,0),    green=(300,110),  high_growth=(500,140),  youth=(400,55),   jobs=(2000,682)),
    "gumla":               dict(total=(1000,0),    women_led=(1000,0),    male_led=(0,0),        non_traditional=(400,0),    e_rickshaw=(0,0),    green=(300,0),    high_growth=(500,0),    youth=(400,0),    jobs=(2000,0)),
}

# Maps a target-sheet district name to the district1 value(s) it corresponds
# to in the raw data extract (post data_utils.normalize_text cleaning).
DISTRICT_NAME_MAP = {
    "jhansi": ["jhansi"],
    "mirzapur": ["mirzapur"],
    "lucknow": ["lucknow"],
    "varanasi": ["varanasi"],
    "gorakhpur": ["gorakhpur"],
    "prayagraj": ["prayagraj"],
    "kaushambi": ["kaushambi"],
    "sonbhadra": ["sonbhadra"],
    "sant ravidas nagar": ["bhadohi"],  # official name vs. extract's short name
    "sitapur": ["sitapur"],
    "lakhimpur kheri": ["lakhimpur kheri"],
    "deoria": ["deoria"],
    "nalanda": ["nalanda"],
    # "gumla" (Jharkhand) does not appear among district1 values in the extract
    # tested against (bahraich, balrampur, bhadohi, deoria, gaya, gorakhpur,
    # jhansi, kaushambi, lakhimpur kheri, lucknow, mirzapur, nalanda, niwadi,
    # niwari, prayagraj, sitapur, sonbhadra, varanasi). Left unmapped rather
    # than guessed, so the gap is surfaced instead of silently misattributed.
    "gumla": [],
}

METRIC_LABELS = {
    "total": "Total Enterprises",
    "women_led": "Women-led Enterprises",
    "male_led": "Male-led Enterprises",
    "non_traditional": "Non-traditional Enterprises",
    "e_rickshaw": "Women-led E-rickshaw Enterprises",
    "green": "Green / Clean Tech Enterprises",
    "high_growth": "High Growth Enterprises",
    "youth": "Youth-led Enterprises",
    "jobs": "Jobs Created",
}

# gumla wasn't actually found among district1 values in the extract we tested
# against — see the comment on DISTRICT_NAME_MAP above.


def build_targets_df() -> pd.DataFrame:
    """Long-format official targets/achieved table: one row per (district, metric)."""
    rows = []
    for sheet_name, metrics in _RAW_TARGETS.items():
        for metric, (target, achieved_official) in metrics.items():
            rows.append({
                "district_sheet_name": sheet_name, "metric": metric,
                "target": target, "achieved_official": achieved_official,
            })
    return pd.DataFrame(rows)


def compute_live_extract_count(fdf: pd.DataFrame) -> pd.DataFrame:
    """
    Count records in the (already-filtered) current data extract per district1
    and metric, using the same metric definitions implied by the target
    sheet's column headers. This is NOT the same as the official 'Achieved'
    figure above — see the note in target_vs_achieved() — it's the live count
    in whatever extract is currently loaded/filtered, useful for sanity-
    checking extract completeness against the official reported numbers.
    """
    if "district1" not in fdf.columns:
        return pd.DataFrame(columns=["district1", "metric", "live_extract_count"])

    g = fdf.groupby("district1")
    out = pd.DataFrame({
        "total": g.size(),
        "women_led": g.apply(lambda d: (d["gender"] == "female").sum(), include_groups=False),
        "male_led": g.apply(lambda d: (d["gender"] == "male").sum(), include_groups=False),
        "non_traditional": g.apply(lambda d: (d["traditional_non_traditional"] == "non-traditional").sum(), include_groups=False),
        "e_rickshaw": g.apply(lambda d: ((d["enterprise_type"] == "e-rickshaw") & (d["gender"] == "female")).sum(), include_groups=False),
        "green": g["is_green_flag"].sum() if "is_green_flag" in fdf.columns else 0,
        "high_growth": g["is_high_growth"].sum() if "is_high_growth" in fdf.columns else 0,
        "youth": g["is_youth"].sum() if "is_youth" in fdf.columns else 0,
        "jobs": g["total_employees"].sum() if "total_employees" in fdf.columns else 0,
    }).reset_index()

    return out.melt(id_vars="district1", var_name="metric", value_name="live_extract_count")


def target_vs_achieved(fdf: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Returns (merged dataframe, list of districts present in the data but with
    no matching target row).

    IMPORTANT: `achieved_official` (from the target sheet) and
    `live_extract_count` (counted fresh from the loaded data extract) do NOT
    match 1:1 when tested against the actual extract — e.g. Jhansi's official
    "Total" achieved is 2,467 but the raw extract has 12,627 records for
    Jhansi. That gap likely reflects the official figure being a
    verified/reported-as-of-date subset, while the extract includes
    unverified/in-progress records. Both numbers are kept, separately
    labeled, rather than silently reconciled — don't treat live_extract_count
    as a stand-in for the official achieved figure without checking with the
    programme team what subset the official number represents.
    """
    targets = build_targets_df()

    expanded_rows = []
    for _, row in targets.iterrows():
        for d1 in DISTRICT_NAME_MAP.get(row["district_sheet_name"], []):
            expanded_rows.append({
                "district1": d1, "metric": row["metric"],
                "target": row["target"], "achieved_official": row["achieved_official"],
            })
    targets_expanded = pd.DataFrame(expanded_rows)

    live = compute_live_extract_count(fdf)

    merged = targets_expanded.merge(live, on=["district1", "metric"], how="left")
    merged["live_extract_count"] = merged["live_extract_count"].fillna(0)
    merged["pct_official"] = (merged["achieved_official"] / merged["target"].replace(0, pd.NA) * 100)
    merged["metric_label"] = merged["metric"].map(METRIC_LABELS)

    data_districts = set(fdf["district1"].dropna().unique()) if "district1" in fdf.columns else set()
    target_districts = set(targets_expanded["district1"].unique())
    untracked = sorted(data_districts - target_districts)

    return merged, untracked
