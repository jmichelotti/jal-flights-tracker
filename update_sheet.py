"""CLI wrapper around SheetClient for manual operations.

Usage:
    python update_sheet.py init
    python update_sheet.py read-snapshot
    python update_sheet.py upsert-snapshot --json '[{...}, {...}]'
    python update_sheet.py append-history --json '[{...}, {...}]'
    python update_sheet.py append-alerts --json '[{...}, {...}]'

The scraper imports SheetClient directly — this CLI is for debugging
and one-time init.
"""

from __future__ import annotations

import argparse
import json
import sys

from sheet_client import SheetClient


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("read-snapshot")

    p_up = sub.add_parser("upsert-snapshot")
    p_up.add_argument("--json", dest="json_str", required=True)

    p_hist = sub.add_parser("append-history")
    p_hist.add_argument("--json", dest="json_str", required=True)

    p_alert = sub.add_parser("append-alerts")
    p_alert.add_argument("--json", dest="json_str", required=True)

    args = parser.parse_args()

    try:
        client = SheetClient()

        if args.cmd == "init":
            result = client.init()
        elif args.cmd == "read-snapshot":
            result = client.read_snapshot()
        elif args.cmd == "upsert-snapshot":
            result = client.upsert_snapshot_bulk(json.loads(args.json_str))
        elif args.cmd == "append-history":
            result = client.append_history_bulk(json.loads(args.json_str))
        elif args.cmd == "append-alerts":
            result = client.append_alerts(json.loads(args.json_str))
        else:
            print(f"Unknown command: {args.cmd}", file=sys.stderr)
            return 1

        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
