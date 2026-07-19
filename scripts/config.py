"""
Shared configuration for the Cisco Gauge Daily River Report pipeline.

Everything that might change (site, parameter, window size, file paths)
lives here so the other scripts stay generic.
"""
import os
from pathlib import Path

# --- Site & parameter -------------------------------------------------
SITE_ID = "09180500"                     # USGS site number (no prefix)
MONITORING_LOCATION_ID = f"USGS-{SITE_ID}"  # format required by the new API
PARAMETER_CODE = "00060"                 # discharge, cubic feet per second
STATISTIC_ID_MEAN = "00003"              # "daily mean" statistic code
SITE_NAME = "Colorado River near Cisco, UT"

# --- USGS API ------------------------------------------------------------
# New OGC-API endpoints (replacing the legacy waterservices.usgs.gov, which
# USGS is decommissioning). Sign up for a free key at:
#   https://api.waterdata.usgs.gov/signup
API_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"
DAILY_ITEMS_URL = f"{API_BASE}/collections/daily/items"
CONTINUOUS_ITEMS_URL = f"{API_BASE}/collections/continuous/items"
LATEST_CONTINUOUS_ITEMS_URL = f"{API_BASE}/collections/latest-continuous/items"

# Where your API key comes from. Checked in this order:
#   1. local_settings.py (a file YOU create, next to this one, that never
#      gets shared -- see local_settings.example.py for the format)
#   2. an environment variable called USGS_API_KEY (this is what Replit
#      "Secrets" sets under the hood, for whenever you deploy there)
try:
    from local_settings import USGS_API_KEY as _LOCAL_API_KEY
except ImportError:
    _LOCAL_API_KEY = None

API_KEY = os.environ.get("USGS_API_KEY") or _LOCAL_API_KEY or ""

# --- Historical window -----------------------------------------------
# First year of daily mean discharge record you want to include.
# (Cisco gauge record actually goes back further than 1975 -- change this
# if you want the full period of record. Check the site's period-of-record
# on the monitoring location page if unsure.)
START_YEAR = 1975

# First year you're treating as "post-megadrought." Used only for the
# optional secondary comparison in compute_dashboard.py (rank/percentile
# against just the recent, drier years, alongside the full-record stats).
# Change this if your climate charts use a different cutoff.
MEGADROUGHT_START_YEAR = 2000

# +/- N days around "today" used to build the historical comparison sample.
# A window smooths out the fact that snowmelt peaks don't land on the exact
# same calendar day every year.
WINDOW_DAYS = 0

# --- File paths --------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATIC_DIR = PROJECT_ROOT / "static"

USGS_DAILY_CSV = DATA_DIR / "usgs_daily.csv"
DASHBOARD_JSON = DATA_DIR / "dashboard_data.json"
DASHBOARD_PNG = STATIC_DIR / "dashboard.png"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
