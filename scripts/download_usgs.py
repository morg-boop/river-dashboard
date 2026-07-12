"""
download_usgs.py

Downloads daily-mean discharge (CFS) for the Cisco gauge from USGS's
modernized Water Data API and keeps a local CSV (data/usgs_daily.csv)
up to date.

Two modes, chosen automatically:

  1. BACKFILL  - if usgs_daily.csv doesn't exist yet, download the entire
     history from config.START_YEAR through yesterday. This is a one-time,
     somewhat slow operation (paginated, a few thousand records).

  2. UPDATE    - if usgs_daily.csv already exists, just fetch anything
     newer than the last date on file and append it. This is what the
     daily cron job / scheduled task will run.

Run directly:
    python download_usgs.py
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import pandas as pd
import requests

import config


def _headers() -> dict:
    headers = {"Accept": "application/geo+json"}
    if config.API_KEY:
        headers["X-Api-Key"] = config.API_KEY
    else:
        print(
            "WARNING: no USGS_API_KEY set. You're limited to 50 requests/hour "
            "per IP. Get a free key at https://api.waterdata.usgs.gov/signup "
            "and `export USGS_API_KEY=...` before running this script.",
            file=sys.stderr,
        )
    return headers


def _fetch_all_pages(params: dict, max_pages: int = 50) -> list[dict]:
    """Fetch every page of results for a /daily/items query, following the
    GeoJSON 'next' links USGS returns instead of hand-building offsets."""
    features: list[dict] = []
    url = config.DAILY_ITEMS_URL
    query = dict(params)  # first request uses our params
    headers = _headers()

    for page_num in range(max_pages):
        resp = requests.get(url, params=query, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        features.extend(payload.get("features", []))

        next_link = next(
            (link["href"] for link in payload.get("links", []) if link.get("rel") == "next"),
            None,
        )
        if not next_link:
            break

        # Per USGS docs, the 'next' link omits the API key -- add it back,
        # and stop sending our original query params since they're baked
        # into next_link already.
        url = next_link
        query = {}
        if config.API_KEY:
            query["api_key"] = config.API_KEY

        time.sleep(0.2)  # be polite
    else:
        print(f"WARNING: hit max_pages={max_pages} while paginating; data may be incomplete.",
              file=sys.stderr)

    return features


def fetch_daily_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily-mean discharge for [start_date, end_date] inclusive."""
    params = {
        "f": "json",
        "monitoring_location_id": config.MONITORING_LOCATION_ID,
        "parameter_code": config.PARAMETER_CODE,
        "statistic_id": config.STATISTIC_ID_MEAN,
        "time": f"{start_date.isoformat()}/{end_date.isoformat()}",
        "limit": 10000,
    }
    features = _fetch_all_pages(params)

    rows = []
    for feat in features:
        props = feat.get("properties", {})
        val = props.get("value")
        dt = props.get("time")
        if val is None or dt is None:
            continue
        rows.append({"date": dt, "flow": float(val)})

    df = pd.DataFrame(rows, columns=["date", "flow"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.drop_duplicates(subset="date").sort_values("date")
    return df


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["day"] = df["date"].dt.day
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df[["date", "year", "month", "day", "flow"]]


def _safe_fetch_daily_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Wraps fetch_daily_range with friendly error messages for the common
    failure modes, used by both backfill() and update()."""
    try:
        return fetch_daily_range(start_date, end_date)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 429:
            print("ERROR: rate limited (HTTP 429). Get a free API key at "
                  "https://api.waterdata.usgs.gov/signup and set USGS_API_KEY.", file=sys.stderr)
        elif status == 403:
            print("ERROR: request forbidden (HTTP 403). Double check your API key, "
                  "or that this environment has outbound internet access to "
                  "api.waterdata.usgs.gov.", file=sys.stderr)
        else:
            print(f"ERROR: USGS API request failed ({e}).", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: couldn't reach the USGS API ({e}). Check your network connection.",
              file=sys.stderr)
        sys.exit(1)


def backfill():
    start = date(config.START_YEAR, 1, 1)
    end = date.today() - timedelta(days=1)  # yesterday: today's daily mean isn't final yet
    print(f"Backfilling {config.SITE_NAME} ({config.MONITORING_LOCATION_ID}) "
          f"from {start} to {end}. This may take a minute...")

    df = _safe_fetch_daily_range(start, end)

    if df.empty:
        print("ERROR: no data returned. Check your API key, site ID, and network access.",
              file=sys.stderr)
        sys.exit(1)

    df = _finalize(df)
    df.to_csv(config.USGS_DAILY_CSV, index=False)
    print(f"Saved {len(df)} daily records to {config.USGS_DAILY_CSV}")


def update():
    existing = pd.read_csv(config.USGS_DAILY_CSV)
    if existing.empty:
        print("Existing CSV is empty -- running a full backfill instead.")
        backfill()
        return

    last_date = pd.to_datetime(existing["date"]).max().date()
    start = last_date + timedelta(days=1)
    end = date.today() - timedelta(days=1)

    if start > end:
        print(f"Already up to date (latest on file: {last_date}). Nothing to do.")
        return

    print(f"Fetching new daily values from {start} to {end}...")
    new_df = _safe_fetch_daily_range(start, end)

    if new_df.empty:
        print(f"No new data available yet for {start}-{end} "
              f"(USGS may not have finalized the daily mean). Nothing to append.")
        return

    new_df = _finalize(new_df)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined["date"] = combined["date"].astype(str)
    combined = combined.drop_duplicates(subset="date").sort_values("date")
    combined.to_csv(config.USGS_DAILY_CSV, index=False)
    print(f"Appended {len(new_df)} new record(s). CSV now has {len(combined)} rows.")


def main():
    if config.USGS_DAILY_CSV.exists():
        update()
    else:
        backfill()


if __name__ == "__main__":
    main()