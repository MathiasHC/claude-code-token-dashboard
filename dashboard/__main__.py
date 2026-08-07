"""python3 -m dashboard"""

from __future__ import annotations

import argparse
import sys

from . import plans, web


def main() -> None:
    parser = argparse.ArgumentParser(prog="dashboard", description="Claude token dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=web.DEFAULT_PORT)
    parser.add_argument(
        "--refresh",
        type=int,
        default=web.DEFAULT_REFRESH_SECONDS,
        metavar="SECONDS",
        help=(
            "how often the page reloads (default "
            f"{web.DEFAULT_REFRESH_SECONDS}). Also how long a selected date "
            "range stays on screen before returning to the default view."
        ),
    )
    parser.add_argument(
        "--plan",
        metavar="PLAN",
        help=(
            "what to compare api-equivalent cost against: "
            + ", ".join(p.key for p in plans.CATALOGUE)
            + ", or a monthly amount like 149. Saved for next time. "
            "Asked once interactively if never set."
        ),
    )
    args = parser.parse_args()

    try:
        plan = plans.resolve(args.plan)
    except ValueError as error:
        parser.error(str(error))
        return  # unreachable; parser.error exits

    if args.refresh < 1:
        parser.error("--refresh must be at least 1 second")
    web.serve(host=args.host, port=args.port, plan=plan, refresh_seconds=args.refresh)


if __name__ == "__main__":
    sys.exit(main())
