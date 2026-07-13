"""
verify_climate_claims.py

One-off sanity check: computes the real pre-2000 / post-2000 average flow,
peak years, and a decade-by-decade breakdown directly from your actual
usgs_daily.csv -- so you can compare these against the specific numbers
written into your scrollytelling page (7,539 / 5,418 / 15,264 / -565 per
decade / etc.) and see whether they actually hold up.

Run from the folder containing usgs_daily.csv, or edit CSV_PATH below.
    python verify_climate_claims.py
"""
import numpy as np
import pandas as pd

CSV_PATH = "data/usgs_daily.csv"  # adjust if your CSV lives somewhere else

df = pd.read_csv(CSV_PATH)
df["date"] = pd.to_datetime(df["date"])

# One mean value per year -- matches how "the river averaged X CFS" is
# normally meant (average of yearly averages, not a flat average of every
# single day, which would over-weight years with more readings).
annual = df.groupby("year")["flow"].mean().reset_index()
annual.columns = ["year", "mean_cfs"]

pre_2000 = annual[annual["year"] < 2000]["mean_cfs"]
post_2000 = annual[annual["year"] >= 2000]["mean_cfs"]

pre_avg = pre_2000.mean()
post_avg = post_2000.mean()
pct_change = (post_avg - pre_avg) / pre_avg * 100

print("=== Pre/post 2000 comparison ===")
print(f"Pre-2000 average  ({pre_2000.count()} years): {pre_avg:,.0f} CFS   <- compare to the 7,539 claim")
print(f"Post-2000 average ({post_2000.count()} years): {post_avg:,.0f} CFS   <- compare to the 5,418 claim")
print(f"Change: {pct_change:+.1f}%   <- compare to the -28% claim")
print()

# Highest single DAY ever recorded -- matches "X CFS roaring through the
# gauge" framing, i.e. a flood/peak-event number, not an annual average.
peak_day = df.loc[df["flow"].idxmax()]
print("=== Peak flow ===")
print(f"Highest single-day flow on record: {peak_day['flow']:,.0f} CFS on {peak_day['date'].date()}")
print("   <- compare to the '1984, 15,264 CFS' claim -- check both the YEAR and the number")

# Highest annual MEAN -- a different, gentler kind of "peak"
peak_year_row = annual.loc[annual["mean_cfs"].idxmax()]
print(f"Highest annual-mean year: {int(peak_year_row['year'])} ({peak_year_row['mean_cfs']:,.0f} CFS average)")
print()

# Rough flow-change-per-decade via a simple linear fit over annual means
slope, intercept = np.polyfit(annual["year"], annual["mean_cfs"], 1)
print("=== Long-term trend ===")
print(f"Approximate flow trend: {slope * 10:+,.0f} CFS per decade   <- compare to the -565 CFS/decade claim")
print()

# Decade-by-decade breakdown, same baseline framing your page uses
BASELINE = 7539  # Replit's claimed pre-2000 baseline -- change to pre_avg above to test against YOUR real baseline instead
annual["decade"] = (annual["year"] // 10 * 10).astype(int).astype(str) + "s"
by_decade = annual.groupby("decade")["mean_cfs"].agg(mean_cfs="mean", n_years="count")
by_decade["vs_baseline_pct"] = (by_decade["mean_cfs"] - BASELINE) / BASELINE * 100
print("=== Decade breakdown (vs. Replit's claimed 7,539 baseline) ===")
print(by_decade.round(1))
