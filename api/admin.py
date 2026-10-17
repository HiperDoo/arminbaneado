"""Moderación local de firmas. Se ejecuta en el servidor, no hay endpoint público.

  python admin.py stats
  python admin.py list --status flagged
  python admin.py approve 42
  python admin.py reject 42
  python admin.py ban-net <net_hash>
  python admin.py import-legacy api3_data.json
"""

import argparse
import json
import os
import time
from pathlib import Path

from db import Database


def main():
    parser = argparse.ArgumentParser(description="Moderación de firmas del Caso Armin")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", str(Path(__file__).parent / "data" / "firmas.db")))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    ls = sub.add_parser("list")
    ls.add_argument("--status", default="flagged", choices=["accepted", "flagged", "rejected"])
    ls.add_argument("--limit", type=int, default=50)
    for name in ("approve", "reject"):
        sub.add_parser(name).add_argument("id", type=int)
    sub.add_parser("ban-net").add_argument("net_hash")
    sub.add_parser("import-legacy").add_argument("path")
    args = parser.parse_args()

    db = Database(Path(args.db))
    if args.cmd == "stats":
        offset = db.get_meta("legacy_offset") or "0"
        stats = db.stats()
        print(f"Base heredada: {offset}")
        for status, n in sorted(stats.items()):
            print(f"{status:>9}: {n}")
        print(f"Contador público: {int(offset) + stats.get('accepted', 0)}")
    elif args.cmd == "list":
        for r in db.list_by_status(args.status, args.limit):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
            print(f"#{r['id']:<6} {when}  red={r['net_hash'][:12]}  phash={r['phash']}  {r['stroke_stats']}")
    elif args.cmd in ("approve", "reject"):
        status = "accepted" if args.cmd == "approve" else "rejected"
        print("Hecho." if db.set_status(args.id, status) else "No existe esa firma.")
    elif args.cmd == "ban-net":
        db.ban_net(args.net_hash)
        print("Red bloqueada.")
    elif args.cmd == "import-legacy":
        counter = int(json.loads(Path(args.path).read_text("utf-8")).get("counter", 0))
        db.set_meta("legacy_offset", str(counter))
        print(f"Base heredada fijada en {counter} firmas.")


if __name__ == "__main__":
    main()
