"""
update_dashboard.py

The single command your scheduler (cron, Replit "Scheduled Deployment",
GitHub Actions, etc.) runs once a day. Runs the whole pipeline in order and
exits non-zero if anything fails, so a scheduler can alert you.

    python update_dashboard.py
"""
import sys
import traceback

import compute_dashboard
import download_usgs
import make_dashboard_plot


def main():
    steps = [
        ("Downloading latest USGS data", download_usgs.main),
        ("Computing dashboard statistics", compute_dashboard.main),
        ("Generating comparison plot", make_dashboard_plot.main),
    ]

    for label, fn in steps:
        print(f"\n=== {label} ===")
        try:
            fn()
        except SystemExit as e:
            if e.code:
                print(f"Step '{label}' exited with error, stopping.", file=sys.stderr)
                sys.exit(e.code)
        except Exception:
            print(f"Step '{label}' raised an unexpected error:", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

    print("\nDaily River Report updated successfully.")


if __name__ == "__main__":
    main()