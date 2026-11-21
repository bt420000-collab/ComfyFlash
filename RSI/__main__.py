"""On-demand access to RSI history and database maintenance."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from RSI.store import DEFAULT_DB_PATH, RSIStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the RSI SQLite database")
    actions = parser.add_subparsers(dest="action", required=True)
    history = actions.add_parser("history", help="Print recent matching run summaries")
    for field in ("tone", "device", "model", "resolution", "policy-version"):
        history.add_argument(f"--{field}")
    history.add_argument("--duration-s", type=float)
    history.add_argument("--limit", type=int, default=100)
    actions.add_parser("check", help="Check an existing RSI database")
    for action in ("backup", "restore"):
        command = actions.add_parser(action, help=f"Create a verified {action} at a new path")
        command.add_argument("destination")
    args = parser.parse_args()
    try:
        store = RSIStore(args.db)
        if args.action == "history":
            result = store.query_history(
                tone=args.tone, device=args.device, model=args.model, resolution=args.resolution,
                duration_s=args.duration_s, policy_version=args.policy_version, limit=args.limit,
            )
        elif args.action == "check":
            result = {"ok": True, "schema_version": store.check()}
        elif args.action == "backup":
            result = {"path": str(store.backup(args.destination))}
        else:
            result = {"path": str(RSIStore.restore(args.db, args.destination).path)}
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"RSI: {exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
