"""
Clean the Victorian Rental Report (DFFH / Homes Victoria) Excel files into
tidy long-format CSVs ready for Tableau.

Inputs (September quarter 2025 release):
  - quarterly-median-rents-local-government-area-september-quarter-2025-excel.xlsx
  - Moving_annual_median_rent_by_suburb_and_town_-_September_quarter_2025.xlsx

Outputs:
  - lga_quarterly_rents.csv    one row per LGA x property type x quarter
  - suburb_annual_rents.csv    one row per suburb x property type x quarter

Each Excel sheet has the same wide layout:
  row 0: title
  row 1: col A = property type label, then each quarter repeated twice
  row 2: "Count", "Median" alternating
  row 3+: col A = region (only on the first row of each group),
          col B = LGA / suburb, then count/median pairs; "-" = suppressed
"""

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IN_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "clean"

LGA_FILE = IN_DIR / "quarterly-median-rents-local-government-area-september-quarter-2025-excel.xlsx"
SUBURB_FILE = IN_DIR / "Moving_annual_median_rent_by_suburb_and_town_-_September_quarter_2025.xlsx"

# Consistent property-type labels across both files (sheet names differ slightly)
PROPERTY_TYPES = {
    "1br flat": "1 bedroom flat", "1 bedroom flat": "1 bedroom flat",
    "2br flat": "2 bedroom flat", "2 bedroom flat": "2 bedroom flat",
    "3br flat": "3 bedroom flat", "3 bedroom flat": "3 bedroom flat",
    "2br house": "2 bedroom house", "2 bedroom house": "2 bedroom house",
    "3br house": "3 bedroom house", "3 bedroom house": "3 bedroom house",
    "4br house": "4 bedroom house", "4 bedroom house": "4 bedroom house",
    "all properties": "All properties",
}

# Fix truncated / misspelled names in the source so they match map boundaries
NAME_FIXES = {
    "Mornington Penin'a": "Mornington Peninsula",
    "Wanagaratta": "Wangaratta",
}

# Suburb-file regions that are outside metropolitan Melbourne
REGIONAL_SUBURB_GROUPS = {"Geelong", "Ballarat", "Bendigo", "Other Regional Centres"}


def tidy_sheet(raw: pd.DataFrame, property_type: str) -> pd.DataFrame:
    """Turn one wide sheet into long format: region, area, quarter, count, median."""
    quarters = raw.iloc[1, 2:].tolist()
    measures = raw.iloc[2, 2:].tolist()

    body = raw.iloc[3:].copy()
    body = body[body[1].notna()]                     # drop blank spacer rows
    body[0] = body[0].ffill()                        # region only on first row of group
    body = body.rename(columns={0: "region", 1: "area"})

    # Give every data column a "Quarter|Measure" name, then melt
    body.columns = ["region", "area"] + [f"{q}|{m}" for q, m in zip(quarters, measures)]
    long = body.melt(id_vars=["region", "area"], var_name="key", value_name="value")
    long[["quarter", "measure"]] = long["key"].str.split("|", expand=True)

    long = (long.pivot_table(index=["region", "area", "quarter"], columns="measure",
                             values="value", aggfunc="first")
                .reset_index()
                .rename(columns={"Count": "bond_count", "Median": "median_rent"}))
    long.columns.name = None

    # "-" means too few bonds to publish -> missing
    for col in ["bond_count", "median_rent"]:
        long[col] = pd.to_numeric(long[col], errors="coerce")

    long["property_type"] = property_type
    return long


def add_common_columns(df: pd.DataFrame) -> pd.DataFrame:
    df["area"] = df["area"].str.strip().replace(NAME_FIXES)
    df["region"] = df["region"].str.strip()
    df["quarter_end"] = pd.to_datetime(df["quarter"], format="%b %Y") + pd.offsets.MonthEnd(0)
    df["year"] = df["quarter_end"].dt.year

    # Label summary rows so they can be filtered in Tableau
    df["level"] = "Area"
    df.loc[df["area"] == "Group Total", "level"] = "Region total"
    df.loc[df["region"].isin(["Table Total", "METRO NON-METRO"]), "level"] = "State total"
    df.loc[df["level"] == "Region total", "area"] = df["region"] + " (total)"
    return df


def load_file(path: Path) -> pd.DataFrame:
    sheets = pd.read_excel(path, sheet_name=None, header=None)
    frames = [tidy_sheet(raw, PROPERTY_TYPES[name.strip().lower()]) for name, raw in sheets.items()]
    return add_common_columns(pd.concat(frames, ignore_index=True))


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # ---- LGA quarterly medians ------------------------------------------
    lga = load_file(LGA_FILE)
    # The Victoria total appears twice (under "Table Total" and "METRO NON-METRO"); keep one
    lga = lga[lga["region"] != "Table Total"]
    lga["metro_regional"] = lga["region"].str.contains("Metro", case=False).map(
        {True: "Metropolitan Melbourne", False: "Regional Victoria"})
    lga.loc[lga["level"] == "State total", "metro_regional"] = lga["area"]
    lga = lga.rename(columns={"area": "lga"})

    # ---- Suburb moving annual medians -----------------------------------
    sub = load_file(SUBURB_FILE)
    sub["metro_regional"] = sub["region"].isin(REGIONAL_SUBURB_GROUPS).map(
        {True: "Regional Victoria", False: "Metropolitan Melbourne"})
    sub = sub.rename(columns={"area": "suburb"})

    cols_lga = ["lga", "region", "metro_regional", "level", "property_type",
                "quarter", "quarter_end", "year", "bond_count", "median_rent"]
    cols_sub = ["suburb", "region", "metro_regional", "level", "property_type",
                "quarter", "quarter_end", "year", "bond_count", "median_rent"]

    lga = lga[cols_lga].sort_values(["level", "lga", "property_type", "quarter_end"])
    sub = sub[cols_sub].sort_values(["level", "suburb", "property_type", "quarter_end"])

    lga.to_csv(OUT_DIR / "lga_quarterly_rents.csv", index=False)
    sub.to_csv(OUT_DIR / "suburb_annual_rents.csv", index=False)

    # ---- Quick checks ----------------------------------------------------
    for name, df, key in [("LGA", lga, "lga"), ("Suburb", sub, "suburb")]:
        areas = df.loc[df["level"] == "Area", key].nunique()
        print(f"{name}: {len(df):,} rows | {areas} areas | "
              f"{df['quarter_end'].min():%b %Y} to {df['quarter_end'].max():%b %Y} | "
              f"{df['median_rent'].isna().mean():.1%} suppressed")
        dupes = df.duplicated([key, "region", "property_type", "quarter"]).sum()
        assert dupes == 0, f"{name}: {dupes} duplicate rows"


if __name__ == "__main__":
    main()
