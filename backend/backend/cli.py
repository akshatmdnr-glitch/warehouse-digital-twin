"""Admin CLI for the warehouse backend.

Usage:
    python -m backend.cli create-user <username> <role> [password]
    python -m backend.cli set-password <username> [password]
    python -m backend.cli backup [dest]
    python -m backend.cli migrate
    python -m backend.cli prune --table robot_positions --before-ts <epoch>
    python -m backend.cli stats
"""

import argparse
import sys
import time

from . import auth
from . import repository as repo
from .config import get_config
from .database import get_db
from .logging_config import get_logger

log = get_logger("warehouse.cli")


def cmd_create_user(args):
    password = args.password or "changeme"
    repo.create_user(args.username, args.role, auth.hash_password(password))
    print(f"user {args.username} created with role {args.role}")


def cmd_set_password(args):
    password = args.password or "changeme"
    user = repo.get_user(args.username)
    if not user:
        print(f"user {args.username} not found", file=sys.stderr)
        return 1
    get_db().execute(
        "UPDATE users SET password_hash=? WHERE username=?",
        (auth.hash_password(password), args.username),
    )
    print(f"password updated for {args.username}")


def cmd_backup(args):
    dest = args.dest or f"data/backups/manual_{int(time.time())}.db"
    get_db().backup(dest)
    print(f"backup written to {dest}")


def cmd_migrate(args):
    get_db().migrate()
    print("migrations applied")


def cmd_stats(args):
    print("tables:", repo.table_sizes())
    print("robots:", len(repo.list_robots()))
    print("tasks:", len(repo.list_tasks()))
    print("latest fleet:", repo.latest_fleet_status())


def cmd_prune(args):
    n = repo.prune(args.table, args.column, args.before_ts)
    print(f"pruned {n} rows from {args.table}")


def main():
    parser = argparse.ArgumentParser(prog="backend.cli")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("create-user")
    p.add_argument("username")
    p.add_argument("role", choices=auth.VALID_ROLES)
    p.add_argument("password", nargs="?")
    p = sub.add_parser("set-password")
    p.add_argument("username")
    p.add_argument("password", nargs="?")
    p = sub.add_parser("backup")
    p.add_argument("dest", nargs="?")
    sub.add_parser("migrate")
    sub.add_parser("stats")
    p = sub.add_parser("prune")
    p.add_argument("--table", required=True)
    p.add_argument("--column", default="ts")
    p.add_argument("--before-ts", type=float, required=True)

    args = parser.parse_args()
    handlers = {
        "create-user": cmd_create_user,
        "set-password": cmd_set_password,
        "backup": cmd_backup,
        "migrate": cmd_migrate,
        "stats": cmd_stats,
        "prune": cmd_prune,
    }
    if not args.command:
        parser.print_help()
        return 1
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
