"""
convert_to_parquet.py
----------------------
Run this locally each time before you push an updated extract to your
private data repo. Converts the raw .xlsx (as exported from your MIS) into
.parquet, which is ~3x smaller and loads faster in the dashboard.

Usage:
    python convert_to_parquet.py path/to/all_data_latest.xlsx

Produces all_data.parquet in the same folder — that's the file you push to
the data repo (see README.md), keeping the same filename each time so the
app's link to it never breaks.
"""

import sys
import pandas as pd

def main():
    if len(sys.argv) != 2:
        print("Usage: python convert_to_parquet.py path/to/your_extract.xlsx")
        sys.exit(1)

    src = sys.argv[1]
    out = "all_data.parquet"

    print(f"Reading {src} ...")
    df = pd.read_excel(src)
    print(f"  {len(df):,} rows x {len(df.columns)} columns")

    df.to_parquet(out, index=False)

    import os
    src_mb = os.path.getsize(src) / 1e6
    out_mb = os.path.getsize(out) / 1e6
    print(f"Wrote {out}  ({src_mb:.1f} MB -> {out_mb:.1f} MB)")
    print("Push this file to your private data repo, replacing the previous version.")

if __name__ == "__main__":
    main()
