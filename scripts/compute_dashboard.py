"""
compute_dashboard.py

Reads data/usgs_daily.csv, compares the most recent complete day's mean
discharge against every historical observation within a +/- WINDOW_DAYS
window of that calendar date, and writes data/dashboard_data.json.

Also fetches the live instantaneous reading from USGS so the dashboard can
show a "right now" number alongside the historical comparison, without
letting a partial day's average sneak into the historical ranking.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

import config


BADGE_TABLE = [
    (10, "Much Below Normal", "\U0001F534"),   # < 10th percentile
    (25, "Below Normal", "\U0001F7E0"),        # 10-25
    (75, "Near Normal", "\U0001F7E2"),         # 25-75
    (90, "Above Normal", "\U0001F535"),        # 75-90
    (98, "Much Above Normal", "\U0001F7E3"),   # 90-98
    (100, "Near Record", "\u2B50"),            # >98
]


def _ordinal(n: int) -> str:
    """82 -> '82nd', 11 -> '11th', 23 -> '23rd', etc."""
    n = int(round(n))
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _percentile_phrase(pct: float) -> str:
    """Turns a percentile into a natural-language phrase for the narrative,
    e.g. 'in the 46th percentile'. Special-cases the extreme tails: a value
    that rounds to the 0th or 100th percentile is still meaningful (it's a
    near-record low/high), but "in the 0th percentile" or "in the 100th
    percentile" reads like a typo rather than a real, extreme reading. Using
    'below the 1st' / 'above the 99th' instead keeps it sounding like an
    actual number worth trusting."""
    rounded = int(round(pct))
    if rounded <= 0:
        return "below the 1st percentile"
    if rounded >= 100:
        return "above the 99th percentile"
    return f"in the {_ordinal(rounded)} percentile"


def badge_for_percentile(pct: float) -> dict:
    for ceiling, label, emoji in BADGE_TABLE:
        if pct < ceiling:
            return {"label": label, "emoji": emoji}
    return {"label": BADGE_TABLE[-1][1], "emoji": BADGE_TABLE[-1][2]}


def day_of_year_window(target: date, window_days: int) -> list[tuple[int, int]]:
    """Return list of (month, day) pairs within +/- window_days of target,
    ignoring year (so it works across Feb 29 / year boundaries reasonably)."""
    days = []
    for offset in range(-window_days, window_days + 1):
        d = target + timedelta(days=offset)
        days.append((d.month, d.day))
    return days


def load_history() -> pd.DataFrame:
    if not config.USGS_DAILY_CSV.exists():
        print(f"ERROR: {config.USGS_DAILY_CSV} not found. Run download_usgs.py first.",
              file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(config.USGS_DAILY_CSV)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_current_instantaneous() -> dict | None:
    """Pull the latest instantaneous (real-time) reading using USGS's
    latest-continuous collection, which is purpose-built to return just the
    newest value per time series (no need to page through a time range and
    sort client-side). Returns None on failure so the dashboard can still
    render with yesterday's official daily mean only."""
    headers = {"Accept": "application/geo+json"}
    if config.API_KEY:
        headers["X-Api-Key"] = config.API_KEY
    params = {
        "f": "json",
        "monitoring_location_id": config.MONITORING_LOCATION_ID,
        "parameter_code": config.PARAMETER_CODE,
    }
    try:
        resp = requests.get(config.LATEST_CONTINUOUS_ITEMS_URL, params=params,
                             headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features", [])
        if not features:
            return None
        props = features[0]["properties"]
        return {
            "flow": float(props["value"]),
            "timestamp": props["time"],
        }
    except Exception as exc:  # noqa: BLE001 - we deliberately degrade gracefully
        print(f"WARNING: couldn't fetch live instantaneous reading: {exc}", file=sys.stderr)
        return None


def _compute_stats(sample: pd.Series, current_flow: float, n_years: int) -> dict:
    """Given a historical sample (excluding today's own value) and today's
    flow, compute the standard set of comparison stats. Shared by both the
    full-record comparison and the post-megadrought comparison.

    n_years is the count of distinct calendar years contributing to
    `sample`, passed in explicitly rather than derived from len(sample) --
    sample contains one row per DAY in the +/-WINDOW_DAYS window, so its
    length is usually several times the actual number of years."""
    rank = int((sample > current_flow).sum() + 1)  # 1 = highest on record
    percentile = float((sample < current_flow).mean() * 100)
    median = float(sample.median())
    pct_diff = float((current_flow - median) / median * 100) if median else None
    return {
        "n_years_in_sample": int(n_years),
        "n_observations": int(sample.shape[0]),  # individual day-readings, not years
        "rank_in_window": rank,
        "percentile": round(percentile, 1),
        "median": round(median, 1),
        "mean": round(float(sample.mean()), 1),
        "min": round(float(sample.min()), 1),
        "max": round(float(sample.max()), 1),
        "std": round(float(sample.std()), 1),
        "pct_diff_from_median": round(pct_diff, 1) if pct_diff is not None else None,
        "badge": badge_for_percentile(percentile),
    }


def build_dashboard(history: pd.DataFrame, report_date: date) -> dict:
    window_pairs = set(day_of_year_window(report_date, config.WINDOW_DAYS))
    hist_window = history[
        history["date"].apply(lambda d: (d.month, d.day) in window_pairs)
    ].copy()

    # The exact same date, across all years -- used only for "today's flow
    # vs same date historically" framing in the narrative, not for the main
    # window stats.
    same_date_rows = hist_window[
        (hist_window["date"].dt.month == report_date.month)
        & (hist_window["date"].dt.day == report_date.day)
    ]

    todays_row = history[history["date"].dt.date == report_date]
    if todays_row.empty:
        print(f"ERROR: no daily-mean value on file for {report_date}. "
              f"Run download_usgs.py to update the CSV first.", file=sys.stderr)
        sys.exit(1)

    current_flow = float(todays_row.iloc[0]["flow"])

    # Historical sample excludes this year's own value from the ranking pool
    # comparison (otherwise "today" is always compared partly against itself).
    hist_sample = hist_window[hist_window["date"].dt.date != report_date]["flow"].dropna()
    if hist_sample.empty:
        print("ERROR: no historical comparison data found in the window.", file=sys.stderr)
        sys.exit(1)

    n_years = hist_window["date"].dt.year.nunique()
    stats = _compute_stats(hist_sample, current_flow, n_years)

    # Secondary comparison: same window, but only years from
    # MEGADROUGHT_START_YEAR onward. Answers "how does today compare with
    # just the recent, drier era" instead of the full 1975-on record. None
    # if there isn't enough post-baseline data yet.
    modern_window = hist_window[hist_window["date"].dt.year >= config.MEGADROUGHT_START_YEAR]
    modern_sample = modern_window[modern_window["date"].dt.date != report_date]["flow"].dropna()
    since_megadrought = None
    if not modern_sample.empty:
        modern_n_years = modern_window["date"].dt.year.nunique()
        since_megadrought = _compute_stats(modern_sample, current_flow, modern_n_years)
        since_megadrought["baseline_start_year"] = config.MEGADROUGHT_START_YEAR

    # Same-calendar-date-only rank (for the "#8 highest July 9 since 1975"
    # style headline), computed on distinct years only to avoid double
    # counting when window and exact-date overlap.
    exact_hist = same_date_rows[same_date_rows["date"].dt.date != report_date]["flow"].dropna()
    exact_rank = int((exact_hist > current_flow).sum() + 1) if not exact_hist.empty else None

    live = fetch_current_instantaneous()

    label_month_day = f"{report_date.strftime('%B')} {report_date.day}"
    window_start = report_date - timedelta(days=config.WINDOW_DAYS)
    window_end = report_date + timedelta(days=config.WINDOW_DAYS)
    window_label = f"{window_start.strftime('%b')} {window_start.day}\u2013{window_end.strftime('%b')} {window_end.day}"

    if config.WINDOW_DAYS == 0:
        narrative = (
            f"{label_month_day}'s mean discharge at the Cisco gauge was {current_flow:,.0f} CFS, "
            f"placing it {_percentile_phrase(stats['percentile'])} of all {label_month_day} "
            f"readings since {config.START_YEAR}. "
        )
        if exact_rank is not None:
            narrative += (
                f"{exact_rank - 1} of the past {len(exact_hist)} years recorded a higher flow on this date. "
            )
    else:
        narrative = (
            f"{label_month_day}'s mean discharge at the Cisco gauge was {current_flow:,.0f} CFS, "
            f"placing it {_percentile_phrase(stats['percentile'])} of observations from "
            f"{window_label} since {config.START_YEAR}. "
        )
        if exact_rank is not None:
            narrative += (
                f"On this exact calendar date, {exact_rank - 1} of the last {len(exact_hist)} years "
                f"recorded a higher flow. "
            )
    if stats["pct_diff_from_median"] is not None:
        direction = "above" if stats["pct_diff_from_median"] >= 0 else "below"
        narrative += (
            f"Discharge is {abs(stats['pct_diff_from_median']):.0f}% {direction} the historical "
            f"median for this time of year."
        )
    if since_megadrought is not None:
        m_direction = "above" if (since_megadrought["pct_diff_from_median"] or 0) >= 0 else "below"
        narrative += (
            f" Compared with only the years since {config.MEGADROUGHT_START_YEAR}, "
            f"{label_month_day} ranks {_percentile_phrase(since_megadrought['percentile'])} and is "
            f"{abs(since_megadrought['pct_diff_from_median']):.0f}% {m_direction} that more recent median."
        )

    dashboard = {
        "site_name": config.SITE_NAME,
        "monitoring_location_id": config.MONITORING_LOCATION_ID,
        "report_date": report_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": config.WINDOW_DAYS,
        "start_year": config.START_YEAR,
        "daily_mean_flow": round(current_flow, 1),
        "rank_same_date_only": exact_rank,
        **stats,
        "since_megadrought": since_megadrought,
        "narrative": narrative,
        "current_conditions": live,  # None if the live fetch failed
    }
    return dashboard


def main():
    history = load_history()
    # "Today's report" is always about the most recently completed day on
    # file (the daily mean for "today" itself isn't finalized by USGS yet).
    report_date = history["date"].max().date()

    dashboard = build_dashboard(history, report_date)

    with open(config.DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2, ensure_ascii=False)

    print(f"Wrote {config.DASHBOARD_JSON}")
    print(json.dumps(dashboard, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

