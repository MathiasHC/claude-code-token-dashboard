"""python3 -m dashboard"""

from __future__ import annotations

import argparse

from . import web


def main() -> None:
    parser = argparse.ArgumentParser(prog="dashboard", description="Claude token dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=web.DEFAULT_PORT)
    args = parser.parse_args()
    web.serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
