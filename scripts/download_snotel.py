"""
download_snotel.py
 
Builds the snowpack dataset for the "The River Is Made of Snow" chapter:
snow water equivalent (SWE) from every NRCS SNOTEL station in the basins
that drain to the Cisco gauge, reduced to one April 1 value and one peak
value per station per water year, then aggregated to a basin-level
"percent of 1991-2020 median" -- the same framing NRCS and NOAA's river
forecasters use.
 
Which stations count: SNOTEL stations whose 8-digit hydrologic unit code
(HUC) falls in the basins upstream of USGS gauge 09180500 --
  1401xxxx  Colorado Headwaters (Fraser, Blue, Eagle, Roaring Fork...)
  1402xxxx  Gunnison
  14030002  Upper Dolores
  14030003  San Miguel
  14030004  Lower Dolores
(14030001 / 14030005 are the low-desert mainstem canyons -- no SNOTEL --
and everything below the gauge, e.g. the Green River basin, is excluded.)
 
Outputs:
  data/snotel_yearly.csv  - one row per station per water year
  data/snowpack.json      - basin summary per year (what the chapter reads)
 
Run:
  python3 scripts/download_snotel.py
First run backfills the full record (a few minutes, ~100 stations).
Later runs only refresh the current water year.
"""
from __future__ import annotations
 
import json
import sys
import time
from datetime import date, datetime, timezone
 
import pandas as pd
import requests
 
import config
 
API_BASE = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
STATES = ["CO", "UT"]
HUC_PREFIXES = ("1401", "1402", "14030002", "14030003", "14030004")
NORMAL_START, NORMAL_END = 1991, 2020   # NRCS standard normal period
MIN_NORMAL_YEARS = 20                   # station needs >=20 of the 30 normal years
BACKFILL_BEGIN = "1979-10-01"           # water year 1980; SNOTEL barely predates this
 
SNOTEL_CSV = config.DATA_DIR / "snotel_yearly.csv"
SNOWPACK_JSON = config.DATA_DIR / "snowpack.json"
 
 
def _get(url: str, params: dict, timeout: int = 60):
    resp = requests.get(url, params=params, timeout=timeout,
                        headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()
 
 
def fetch_basin_stations() -> list[dict]:
    """All SNOTEL stations in CO/UT measuring SWE, filtered to the HUCs
    that drain to the Cisco gauge."""
    stations: list[dict] = []
    for state in STATES:
        payload = _get(f"{API_BASE}/stations", {
            "states": state,
            "networkCds": "SNTL",
            "elements": "WTEQ",
        })
        if not isinstance(payload, list):
            print(f"ERROR: unexpected /stations response shape for {state}. "
                  f"First 300 chars:\n{str(payload)[:300]}", file=sys.stderr)
            sys.exit(1)
        stations.extend(payload)
 
    picked = []
    missing_huc = 0
    for s in stations:
        huc = str(s.get("huc") or s.get("hucId") or "")
        if not huc:
            missing_huc += 1
            continue
        if huc.startswith(HUC_PREFIXES):
            picked.append({
                "triplet": s.get("stationTriplet"),
                "name": s.get("name"),
                "huc": huc,
                "elevation": s.get("elevation"),
            })
    if missing_huc and not picked:
        print("ERROR: station metadata had no 'huc' field, so basin filtering "
              "failed. The API response format may have changed -- run:\n"
              f"  curl '{API_BASE}/stations?states=CO&networkCds=SNTL&elements=WTEQ'\n"
              "and send the first station object to debug.", file=sys.stderr)
        sys.exit(1)
 
    picked = [p for p in picked if p["triplet"]]
    # The API can return the same station more than once -- dedupe by triplet.
    seen: set = set()
    unique = []
    for p in picked:
        if p["triplet"] not in seen:
            seen.add(p["triplet"])
            unique.append(p)
    picked = unique
    picked.sort(key=lambda p: p["triplet"])
    print(f"Found {len(picked)} SNOTEL stations in the Cisco drainage "
          f"({missing_huc} stations statewide lacked HUC metadata and were skipped).")
    return picked
 
 
def fetch_station_daily_swe(triplet: str, begin: str, end: str) -> pd.DataFrame:
    """Daily WTEQ (inches) for one station. Returns columns: date, swe."""
    payload = _get(f"{API_BASE}/data", {
        "stationTriplets": triplet,
        "elements": "WTEQ",
        "duration": "DAILY",
        "beginDate": begin,
        "endDate": end,
    })
    rows = []
    if isinstance(payload, list):
        for item in payload:
            for block in item.get("data", []) or []:
                for v in block.get("values", []) or []:
                    d, val = v.get("date"), v.get("value")
                    if d is not None and val is not None:
                        rows.append({"date": d, "swe": float(val)})
    df = pd.DataFrame(rows, columns=["date", "swe"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).drop_duplicates(subset="date").sort_values("date")
    return df
 
 
def reduce_to_water_years(daily: pd.DataFrame) -> pd.DataFrame:
    """Collapse daily SWE to one row per water year:
    april1_swe, peak_swe, peak_date. Water year N = Oct N-1 through Sep N."""
    if daily.empty:
        return pd.DataFrame(columns=["water_year", "april1_swe", "peak_swe", "peak_date"])
    df = daily.copy()
    df["water_year"] = df["date"].dt.year + (df["date"].dt.month >= 10).astype(int)
 
    out = []
    for wy, grp in df.groupby("water_year"):
        apr1 = grp[(grp["date"].dt.month == 4) & (grp["date"].dt.day == 1)]
        april1_swe = float(apr1.iloc[0]["swe"]) if not apr1.empty else None
        peak_row = grp.loc[grp["swe"].idxmax()]
        out.append({
            "water_year": int(wy),
            "april1_swe": april1_swe,
            "peak_swe": float(peak_row["swe"]),
            "peak_date": peak_row["date"].strftime("%Y-%m-%d"),
            "n_days": int(len(grp)),
            # Did this station actually record data in midwinter (Dec-Feb)?
            # A station that came online in June contributes a "year" of
            # snowless summer days whose "peak" of 0 would poison the stats.
            "has_midwinter": bool(grp["date"].dt.month.isin([12, 1, 2]).any()),
        })
    return pd.DataFrame(out)
 
 
def compute_basin_summary(yearly: pd.DataFrame) -> dict:
    """Percent-of-median aggregation, NRCS-style: each station is compared
    with its own 1991-2020 median first, then the basin value for a year is
    the median of the station percentages. This keeps early years honest
    even though fewer stations existed then (each station only ever
    competes against itself)."""
    if "n_days" not in yearly.columns or "has_midwinter" not in yearly.columns:
        print("ERROR: snotel_yearly.csv is from an older version of this script "
              "and lacks coverage columns. Delete data/snotel_yearly.csv and "
              "data/snowpack.json, then rerun for a fresh backfill.", file=sys.stderr)
        sys.exit(1)
 
    # Belt and braces: one row per station per water year.
    yearly = yearly.drop_duplicates(subset=["triplet", "water_year"], keep="first")
    # Only station-years with real winter coverage count toward the stats.
    yearly = yearly[(yearly["n_days"] >= 150) & (yearly["has_midwinter"])]
 
    normals = {}
    for triplet, grp in yearly.groupby("triplet"):
        window = grp[(grp["water_year"] >= NORMAL_START) & (grp["water_year"] <= NORMAL_END)]
        apr = window["april1_swe"].dropna()
        pk = window["peak_swe"].dropna()
        if len(apr) >= MIN_NORMAL_YEARS and apr.median() > 0:
            normals[triplet] = {"apr": float(apr.median()), "peak": float(pk.median())}
 
    years_out = []
    for wy, grp in yearly.groupby("water_year"):
        apr_pcts, peak_pcts = [], []
        for _, row in grp.iterrows():
            nm = normals.get(row["triplet"])
            if not nm:
                continue
            if pd.notna(row["april1_swe"]):
                apr_pcts.append(row["april1_swe"] / nm["apr"] * 100)
            if pd.notna(row["peak_swe"]) and nm["peak"] > 0:
                peak_pcts.append(row["peak_swe"] / nm["peak"] * 100)
        if not apr_pcts and not peak_pcts:
            continue
        years_out.append({
            "water_year": int(wy),
            "april1_pct_of_median": round(float(pd.Series(apr_pcts).median()), 1) if apr_pcts else None,
            "peak_pct_of_median": round(float(pd.Series(peak_pcts).median()), 1) if peak_pcts else None,
            "n_stations": len(apr_pcts) or len(peak_pcts),
        })
 
    years_out.sort(key=lambda y: y["water_year"])
    return {
        "site_context": "SNOTEL stations in basins draining to USGS 09180500 (Colorado near Cisco, UT)",
        "basins": "Colorado Headwaters (HUC 1401), Gunnison (1402), Dolores (14030002-04)",
        "normal_period": f"{NORMAL_START}-{NORMAL_END} station medians",
        "n_stations_with_normals": len(normals),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "years": years_out,
    }
 
 
def main():
    stations = fetch_basin_stations()
    if not stations:
        print("ERROR: no stations matched the basin HUCs. Nothing to do.", file=sys.stderr)
        sys.exit(1)
 
    existing = None
    begin = BACKFILL_BEGIN
    if SNOTEL_CSV.exists():
        existing = pd.read_csv(SNOTEL_CSV)
        current_wy = date.today().year + (1 if date.today().month >= 10 else 0)
        existing = existing[existing["water_year"] < current_wy]
        begin = f"{current_wy - 1}-10-01"
        print(f"Existing data found -- refreshing water year {current_wy} only.")
    else:
        print(f"No existing data -- full backfill from {begin}. "
              f"~{len(stations)} stations, this takes a few minutes...")
 
    end = date.today().isoformat()
    frames = []
    for i, st in enumerate(stations, 1):
        try:
            daily = fetch_station_daily_swe(st["triplet"], begin, end)
        except requests.exceptions.RequestException as e:
            print(f"  WARNING: {st['triplet']} ({st['name']}) failed ({e}); skipping.",
                  file=sys.stderr)
            continue
        yearly = reduce_to_water_years(daily)
        if not yearly.empty:
            yearly.insert(0, "triplet", st["triplet"])
            yearly.insert(1, "name", st["name"])
            yearly.insert(2, "huc", st["huc"])
            frames.append(yearly)
        if i % 10 == 0:
            print(f"  ...{i}/{len(stations)} stations done")
        time.sleep(0.5)  # be polite to the NRCS API
 
    if not frames:
        print("ERROR: no SWE data returned for any station.", file=sys.stderr)
        sys.exit(1)
 
    new_data = pd.concat(frames, ignore_index=True)
    combined = pd.concat([existing, new_data], ignore_index=True) if existing is not None else new_data
    combined = combined.sort_values(["triplet", "water_year"])
    combined.to_csv(SNOTEL_CSV, index=False)
    print(f"Saved {len(combined)} station-year rows to {SNOTEL_CSV}")
 
    summary = compute_basin_summary(combined)
    with open(SNOWPACK_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Wrote basin summary to {SNOWPACK_JSON}")
 
    recent = summary["years"][-3:]
    print("\nMost recent years (April 1 % of median):")
    for y in recent:
        print(f"  WY{y['water_year']}: {y['april1_pct_of_median']}% "
              f"({y['n_stations']} stations)")
 
 
if __name__ == "__main__":
    main()
