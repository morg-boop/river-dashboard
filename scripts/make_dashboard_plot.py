"""
make_dashboard_plot.py

Renders the historical-comparison scatter plot: one gray dot per year in the
comparison window, this year's value highlighted as a star, plus a dashed
median line. Saves to static/dashboard.png.

Deliberately skips Matplotlib's default style in favor of something closer
to a clean editorial graphic (muted grid, no top/right spines, generous
whitespace).
"""
from __future__ import annotations

import json
import sys
from datetime import date as _date
from datetime import timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import config


BACKGROUND = "#FAF8F5"
GRID = "#DDD8CF"
DOT = "#9CA3AF"
STAR = "#1D4E89"
STAR_FILL = "#2E6FB0"
MEDIAN_LINE = "#B45309"
TEXT = "#2B2B2B"


def load_data():
    if not config.DASHBOARD_JSON.exists():
        print(f"ERROR: {config.DASHBOARD_JSON} not found. Run compute_dashboard.py first.",
              file=sys.stderr)
        sys.exit(1)
    with open(config.DASHBOARD_JSON) as f:
        dashboard = json.load(f)

    history = pd.read_csv(config.USGS_DAILY_CSV)
    history["date"] = pd.to_datetime(history["date"])
    return dashboard, history


def window_series(history: pd.DataFrame, dashboard: dict) -> pd.DataFrame:
    report_date = _date.fromisoformat(dashboard["report_date"])
    window = config.WINDOW_DAYS

    pairs = set()
    for offset in range(-window, window + 1):
        d = report_date + timedelta(days=offset)
        pairs.add((d.month, d.day))

    mask = history["date"].apply(lambda d: (d.month, d.day) in pairs)
    windowed = history[mask].copy()

    # Collapse to one value per year: if multiple days in the window fall in
    # the same year, use their mean so each year gets one dot. Note: if the
    # report date is within WINDOW_DAYS of Jan 1, the window spans a
    # calendar-year boundary and a single "season" gets split into two
    # thinner dots (e.g. late Dec + early Jan) rather than one. Not an issue
    # for this river's summer-runoff reporting season; would need a
    # "water year" grouping to fully fix if you extend this to winter dates.
    per_year = windowed.groupby(windowed["date"].dt.year)["flow"].mean().reset_index()
    per_year.columns = ["year", "flow"]
    return per_year


def make_plot(dashboard: dict, per_year: pd.DataFrame):
    report_year = pd.to_datetime(dashboard["report_date"]).year
    current_flow = dashboard["daily_mean_flow"]
    median = dashboard["median"]

    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=150)
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)

    others = per_year[per_year["year"] != report_year]
    ax.scatter(others["year"], others["flow"], s=42, color=DOT, alpha=0.85,
               zorder=2, edgecolors="none", label="Other years")

    ax.axhline(median, color=MEDIAN_LINE, linestyle="--", linewidth=1.3,
               alpha=0.8, zorder=1)
    ax.text(per_year["year"].min(), median, f" Median: {median:,.0f} CFS",
            color=MEDIAN_LINE, fontsize=9, va="bottom", ha="left", fontweight="medium")

    ax.scatter([report_year], [current_flow], s=260, marker="*",
               color=STAR_FILL, edgecolors=STAR, linewidths=1.2, zorder=4,
               label="This year")
    ax.annotate(
        f"{report_year}: {current_flow:,.0f} CFS",
        xy=(report_year, current_flow),
        xytext=(0, 16), textcoords="offset points",
        ha="center", fontsize=10, fontweight="bold", color=STAR,
    )

    report_dt = pd.to_datetime(dashboard["report_date"])
    ax.set_title(
        f"Cisco Gauge — Flow near {report_dt.strftime('%B')} {report_dt.day}, "
        f"{config.START_YEAR}\u2013{report_year}",
        fontsize=13, fontweight="bold", color=TEXT, pad=14, loc="left",
    )
    ax.set_ylabel("Daily mean discharge (CFS)", fontsize=10, color=TEXT)
    ax.set_xlabel("")

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRID)

    ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.7, zorder=0)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.set_ylim(bottom=0)

    fig.tight_layout()
    fig.savefig(config.DASHBOARD_PNG, facecolor=BACKGROUND)
    plt.close(fig)
    print(f"Saved plot to {config.DASHBOARD_PNG}")


def main():
    dashboard, history = load_data()
    per_year = window_series(history, dashboard)
    make_plot(dashboard, per_year)


if __name__ == "__main__":
    main()